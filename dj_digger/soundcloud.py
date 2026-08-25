"""Client for SoundCloud's public (but undocumented) api-v2.

Why this exists: a playlist page only renders the first handful of tracks in the
DOM, so the old workflow needed you to scroll to the bottom and save the HTML by
hand. The API does not have that problem - ``/resolve`` returns every track id in
one response, however long the playlist is. Tracks are then hydrated in batches
of 50, which is the server-side cap.

The API needs no account and no key, but it does need a ``client_id`` lifted from
SoundCloud's own JS bundles. That id rotates, so it is cached and re-discovered
whenever a request comes back unauthorised.
"""

import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from itertools import batched
from pathlib import Path
from typing import Any, Self
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import auth, gates
from .browser import is_fetchable
from .links import host_matches
from .models import Crate, Track

# Windows refuses these as a filename whatever the extension - CON.mp3 is as
# reserved as CON - and says so with an OSError at the moment of writing, which
# is after the whole file has already been fetched. A track called "Aux" is not
# a hypothetical.
WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and invalid characters."""
    filename = os.path.basename(filename)
    filename = re.sub(r'[\\/*?:"<>|]', '_', filename)
    filename = filename.strip('. ')
    if filename.upper() in WINDOWS_RESERVED:
        filename = f"_{filename}"
    return filename or "download"

API_ROOT = "https://api-v2.soundcloud.com"
DISCOVER_URL = "https://soundcloud.com/discover"
# A ceiling on what one track may write to disk. A gate hands over whatever it
# likes and Content-Length is only a claim, so without this the write loop ends
# when the server decides it does. Two gigabytes clears any real DJ file - a
# 10-minute WAV at 24/96 is under 400 MB - by a wide margin.
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOWNLOAD_REDIRECTS = 5
# Concurrent downloads in the TUI pool race between target.exists() and
# os.replace when two tracks sanitise to the same stem; the lock makes
# pick-a-unique-name-and-rename one atomic step.
_DOWNLOAD_NAME_LOCK = threading.Lock()

# The /tracks endpoint answers 400 for more than 50 ids.
HYDRATE_BATCH = 50
PAGE_SIZE = 200

CLIENT_ID_RE = re.compile(r'client_id[=:]"?([A-Za-z0-9]{32})')
ASSET_RE = re.compile(r"https://a-v2\.sndcdn\.com/assets/[^\"']+\.js")

# Two-segment SoundCloud paths that mean "a collection belonging to this user"
# rather than "a track by this user", mapped to their API endpoint.
USER_COLLECTIONS = {"likes": "likes", "tracks": "tracks", "reposts": "reposts"}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int | None], None]


class SoundCloudError(RuntimeError):
    """Raised when SoundCloud cannot be reached or gives us nothing usable."""


class SoundCloudLoginRequired(SoundCloudError):
    """An artist download requires a SoundCloud account session."""


class SoundCloudTokenRejected(SoundCloudLoginRequired):
    """The saved SoundCloud token expired or was rejected."""


def _download_stem(track: Track) -> str:
    raw = " - ".join(part for part in (track.artist, track.title) if part).strip()
    raw = re.sub(r"[^\w .-]+", "", raw, flags=re.UNICODE).strip(" .")
    raw = re.sub(r"\s+", " ", raw)
    return (raw or f"track-{track.id or 'soundcloud'}")[:180]


def create_requests_session(max_retries: int = 5, backoff_factor: float = 0.5) -> requests.Session:
    """Return a session that backs off on rate limits and transient failures."""

    retry_strategy = Retry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        status_forcelist=[429, 500, 502, 503, 504],
        backoff_factor=backoff_factor,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)

    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _download_response(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None,
    timeout: tuple[float, float],
):
    """Follow a short public redirect chain without forwarding query credentials."""

    current = url
    current_params = params
    for _hop in range(MAX_DOWNLOAD_REDIRECTS + 1):
        if not is_fetchable(current):
            raise SoundCloudError("Download redirected to an unsafe address")
        try:
            response = session.get(
                current,
                params=current_params,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise SoundCloudError("Download request failed") from exc
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("Location", "")
        close = getattr(response, "close", None)
        if callable(close):
            close()
        if not location:
            raise SoundCloudError("Download redirect had no destination")
        current = urljoin(current, location)
        current_params = None
    raise SoundCloudError("Download exceeded the redirect limit")


def _looks_like_html(prefix: bytes) -> bool:
    sample = prefix.lstrip().lower()
    return sample.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _extension_for(content_disp: str, content_type: str) -> str:
    """Pick a file suffix from the response headers; .mp3 when nothing matches."""

    cd_lower = content_disp.lower()
    ct_lower = content_type.lower()
    for candidate, disp_needles, type_needles in (
        (".wav", (".wav",), ("wav",)),
        (".flac", (".flac",), ("flac",)),
        (".aiff", (".aiff", ".aif"), ("aiff", "aif")),
        (".zip", (".zip",), ("zip",)),
    ):
        if any(n in cd_lower for n in disp_needles) or any(
            n in ct_lower for n in type_needles
        ):
            return candidate
    return ".mp3"


def _stream_to_file(
    response: Any,
    temporary: Path,
    on_progress: Callable[[int, int | None], None] | None,
    total_size: int | None,
) -> bytes:
    """Stream the body to the temp file; returns the first 512 bytes for sniffing."""

    downloaded = 0
    prefix = bytearray()
    with temporary.open("wb") as handle:
        try:
            # 128 KB: disk writes want fewer, larger chunks than the 64 KB the
            # player streams with, where latency to first audio matters instead.
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    handle.write(chunk)
                    if len(prefix) < 512:
                        prefix.extend(chunk[: 512 - len(prefix)])
                    downloaded += len(chunk)
                    # Content-Length is a claim, and a gate that never
                    # stops sending would otherwise fill the disk: the
                    # loop above had no end but the server's goodwill.
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise SoundCloudError(
                            "Download exceeded "
                            f"{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB - stopped"
                        )
                    if on_progress:
                        on_progress(downloaded, total_size)
        except Exception as stream_exc:
            raise SoundCloudError(f"Download stream read failed: {stream_exc}") from stream_exc
    return bytes(prefix)


def _claim_target(directory: Path, track: Track, suffix: str, temporary: Path) -> Path:
    """Move a finished temp file to a unique final name under the shared lock."""

    stem = _sanitize_filename(_download_stem(track))
    with _DOWNLOAD_NAME_LOCK:
        target = directory / f"{stem}{suffix}"
        counter = 1
        while target.exists():
            target = directory / f"{stem} ({counter}){suffix}"
            counter += 1
        os.replace(temporary, target)
    return target


def save_browser_download(download: Any, track: Track, directory: Path) -> Path:
    """Validate and atomically keep a Playwright download."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    suggested = Path(str(getattr(download, "suggested_filename", "") or ""))
    suffix = suggested.suffix.lower()
    if suffix not in {".mp3", ".wav", ".flac", ".aiff", ".aif", ".zip"}:
        suffix = ".mp3"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".dj-digger-browser-", suffix=".part", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        download.save_as(str(temporary))
        size = temporary.stat().st_size
        if size > MAX_DOWNLOAD_BYTES:
            raise SoundCloudError(
                f"Browser download exceeded {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB"
            )
        with temporary.open("rb") as stream:
            prefix = stream.read(512)
        if _looks_like_html(prefix):
            raise SoundCloudError("Browser downloaded a web page rather than an audio file")
        return _claim_target(directory, track, suffix, temporary)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def is_soundcloud_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower().partition(":")[0]
    return host_matches(host, "soundcloud.com")


def _client_id_cache() -> Path:
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "dj-digger"
    return cache_dir / "client_id.txt"


def split_user_collection(url: str) -> tuple[str | None, str]:
    """Split ``/someone/likes`` into ``("likes", "https://soundcloud.com/someone")``.

    ``/resolve`` does not understand the collection suffixes, so they have to be
    peeled off and turned into a dedicated endpoint call.
    """

    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) == 2 and segments[1].lower() in USER_COLLECTIONS:
        base = f"{parsed.scheme}://{parsed.netloc}/{segments[0]}"
        return USER_COLLECTIONS[segments[1].lower()], base
    return None, url


class SoundCloudClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        *,
        timeout: float = 20.0,
        client_id: str | None = None,
        oauth_token: str | None = None,
        config=None,
    ) -> None:
        self._session = session or create_requests_session()
        self._timeout = timeout
        self._client_id = client_id
        if oauth_token is None:
            self._oauth_token = auth.get_stored_token()
        else:
            self._oauth_token = oauth_token

        # The caller passes its own when it has one, so that editing your name
        # and email in Settings reaches the gate resolvers below rather than
        # updating a second copy nobody reads.
        if config is None:
            from .config import AppConfig

            config = AppConfig()
        self.config = config

    @property
    def oauth_token(self) -> str | None:
        return self._oauth_token

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    @property
    def client_id(self) -> str:
        if self._client_id is None:
            self._client_id = self._discover_client_id()
        return self._client_id

    def _discover_client_id(self, *, force: bool = False) -> str:
        cache = _client_id_cache()
        if not force:
            try:
                cached = cache.read_text(encoding="utf-8").strip()
            except OSError:
                cached = ""
            if len(cached) == 32:
                LOGGER.debug("Using cached client_id")
                return cached

        LOGGER.debug("Discovering client_id from SoundCloud JS bundles")
        try:
            page = self._session.get(DISCOVER_URL, timeout=self._timeout).text
        except requests.RequestException as exc:
            raise SoundCloudError(f"Could not reach soundcloud.com: {exc}") from exc

        # Later bundles carry the API config, so search from the back.
        for asset in sorted(set(ASSET_RE.findall(page)), reverse=True):
            try:
                bundle = self._session.get(asset, timeout=self._timeout).text
            except requests.RequestException:
                continue
            match = CLIENT_ID_RE.search(bundle)
            if not match:
                continue
            client_id = match.group(1)
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(client_id, encoding="utf-8")
            except OSError as exc:
                LOGGER.debug("Could not cache client_id: %s", exc)
            return client_id

        raise SoundCloudError(
            "Could not find a client_id in SoundCloud's JS bundles. "
            "SoundCloud may have changed its site - try the saved-HTML fallback."
        )

    def _request(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET with one automatic retry against a freshly discovered client_id."""

        for attempt in (0, 1):
            merged = dict(params or {})
            merged["client_id"] = self.client_id
            kwargs: dict[str, Any] = {"params": merged, "timeout": self._timeout}
            if self._oauth_token:
                kwargs["headers"] = {"Authorization": f"OAuth {self._oauth_token}"}
            try:
                response = self._session.get(url, **kwargs)
            except requests.RequestException as exc:
                raise SoundCloudError(f"Request to {url} failed: {exc}") from exc

            if response.status_code in (401, 403) and attempt == 0:
                LOGGER.info("client_id rejected (%s), refreshing", response.status_code)
                self._client_id = self._discover_client_id(force=True)
                continue

            if response.status_code == 404:
                raise SoundCloudError(
                    "SoundCloud returned 404. Check the link, and note that private "
                    "or unlisted content needs the saved-HTML fallback."
                )
            if response.status_code >= 400:
                raise SoundCloudError(
                    f"SoundCloud returned HTTP {response.status_code} for {url}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise SoundCloudError(f"SoundCloud sent a non-JSON reply for {url}") from exc

        raise SoundCloudError("SoundCloud kept rejecting our client_id")

    def _get(self, path: str, **params: Any) -> Any:
        return self._request(f"{API_ROOT}{path}", params)

    @property
    def session(self) -> requests.Session:
        """For plain downloads, e.g. an audio stream, that are not API calls."""

        return self._session

    def fetch_track(self, track_id: int) -> dict[str, Any]:
        """The raw payload for one track.

        ``Track`` deliberately keeps only what the link digger needs, so playback
        has to come here for ``media`` and ``track_authorization``.
        """

        payload = self._get("/tracks", ids=str(int(track_id)))
        if not isinstance(payload, list) or not payload:
            raise SoundCloudError(f"Track {track_id} is no longer available")
        return payload[0]

    def authorize(self, url: str, **params: Any) -> dict[str, Any]:
        """Call an absolute api-v2 URL, such as a media transcoding."""

        payload = self._request(url, params)
        if not isinstance(payload, dict):
            raise SoundCloudError(f"Unexpected reply from {url}")
        return payload

    def _resolve_download_url(
        self, track: Track, gate_url: str | None, session: requests.Session
    ) -> tuple[str, bool]:
        """The URL to fetch and whether a gate produced it (which recolours errors)."""

        download_url: str | None = None
        gate_derived = False

        if gate_url:
            download_url = gates.resolve_gate_download_url(
                gate_url, session, timeout=self._timeout, config=self.config
            )
            gate_derived = download_url is not None

        if not download_url and track.has_direct_download and track.download_url:
            download_url = track.download_url

        if not download_url and track.free_download and track.id:
            if not self._oauth_token:
                raise SoundCloudLoginRequired(
                    "SoundCloud login is required for this artist-provided download; "
                    "run 'dj-digger auth login'"
                )
            try:
                response = session.get(
                    f"{API_ROOT}/tracks/{track.id}/download",
                    params={"client_id": self.client_id},
                    headers={"Authorization": f"OAuth {self._oauth_token}"},
                    timeout=self._timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise SoundCloudError(
                    f"SoundCloud download resolution failed: {exc}"
                ) from exc
            if response.status_code in (401, 403):
                raise SoundCloudTokenRejected(
                    "The saved SoundCloud login expired or was rejected; "
                    "run 'dj-digger auth login' again"
                )
            if response.status_code not in (200, 302):
                raise SoundCloudError(
                    f"SoundCloud returned HTTP {response.status_code} while resolving "
                    "the download"
                )
            if response.status_code == 302:
                download_url = response.headers.get("Location")
            else:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise SoundCloudError(
                        "SoundCloud returned an unreadable download reply"
                    ) from exc
                download_url = payload.get("redirectUri") or payload.get("url")

        if not download_url:
            if gate_url:
                raise SoundCloudError(
                    "Gate link requires browser completion - press 'o' to open"
                )
            raise SoundCloudError("This track has no active direct download or resolved gate link")
        return download_url, gate_derived

    def download_track(
        self,
        track: Track,
        directory: Path,
        *,
        gate_url: str | None = None,
        on_progress: Callable[[int, int | None], None] | None = None,
        session: requests.Session | None = None,
    ) -> Path:
        """Save artist-provided download file directly or via resolved gate URL.

        ``session`` exists for callers downloading several tracks at once. A gate
        is a multi-step flow held together by its own cookies, so two of them
        sharing one jar overwrite each other's state - the same reason
        ``dig._expand_one`` builds a session per track. Left out, this uses the
        client's own, which is right for a single download.
        """

        session = session or self._session
        download_url, gate_derived = self._resolve_download_url(track, gate_url, session)

        host = (urlparse(download_url).hostname or "").lower()
        # Domain-boundary match: "soundcloud.com" in host is also true of
        # evil-soundcloud.com.attacker.net, which would then be handed our
        # client_id along with the request.
        ours = host_matches(host, "soundcloud.com")

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None

        try:
            response = _download_response(
                session,
                download_url,
                params={"client_id": self.client_id} if ours else None,
                timeout=(self._timeout, self._timeout),
            )
            if response.status_code >= 400:
                message = f"Server returned HTTP {response.status_code} for download"
                if gate_derived:
                    raise gates.GateDownloadError(message)
                raise SoundCloudError(message)

            content_disp = response.headers.get("Content-Disposition", "")
            content_type = response.headers.get("Content-Type", "")
            try:
                total_size = int(response.headers.get("Content-Length", 0)) or None
            except ValueError:
                total_size = None

            if total_size and total_size > MAX_DOWNLOAD_BYTES:
                raise SoundCloudError(
                    f"Refusing a {total_size // (1024 * 1024)} MB download - "
                    f"the limit is {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB"
                )

            # A gate that has not been satisfied answers 200 with its own page
            # rather than a file. Without this that page was saved as a perfectly
            # ordinary .mp3, and the first sign of trouble was a player refusing
            # to open a track you thought you owned.
            if content_type.lower().startswith(("text/html", "application/xhtml")):
                message = "That link returned a web page rather than a file"
                if gate_derived:
                    raise gates.GateProtocolChanged(message)
                raise SoundCloudError(message)
            extension = _extension_for(content_disp, content_type)

            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".dj-digger-", suffix=".part", dir=directory
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            if os.name != "nt":
                temporary.chmod(0o600)
            prefix = _stream_to_file(response, temporary, on_progress, total_size)

            if _looks_like_html(prefix):
                message = "That link returned a web page rather than an audio file"
                if gate_derived:
                    raise gates.GateProtocolChanged(message)
                raise SoundCloudError(message)

            target = _claim_target(directory, track, extension, temporary)
            # The temp name is gone after the rename; None keeps the finally
            # below from turning a successful return into a PermissionError.
            temporary = None
            return target
        except gates.GateError:
            raise
        except SoundCloudError as exc:
            if gate_derived:
                raise gates.GateDownloadError(str(exc)) from exc
            raise
        except Exception as exc:
            raise SoundCloudError(f"Download failed: {exc}") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def resolve(self, url: str) -> dict[str, Any]:
        payload = self._get("/resolve", url=url)
        if not isinstance(payload, dict):
            raise SoundCloudError(f"Unexpected reply when resolving {url}")
        return payload

    def hydrate_tracks(
        self,
        track_ids: Sequence[int],
        *,
        on_progress: ProgressCallback | None = None,
    ) -> list[Track]:
        """Turn bare track ids into full track objects, 50 per request."""

        ids = [int(tid) for tid in track_ids]
        if not ids:
            return []

        position = {track_id: index for index, track_id in enumerate(ids)}
        tracks: list[Track] = []
        for chunk in batched(ids, HYDRATE_BATCH):
            payload = self._get("/tracks", ids=",".join(str(i) for i in chunk))
            if isinstance(payload, list):
                tracks.extend(Track.from_api(item) for item in payload if isinstance(item, dict))
            if on_progress:
                on_progress(len(tracks), len(ids))

        # The endpoint neither preserves order nor returns deleted tracks.
        tracks.sort(key=lambda track: position.get(track.id or -1, len(ids)))
        return tracks

    def _paginate(
        self,
        path: str,
        *,
        limit: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[Track]:
        tracks: list[Track] = []
        payload = self._get(path, limit=PAGE_SIZE)
        while True:
            if not isinstance(payload, dict):
                break
            for item in payload.get("collection") or []:
                if not isinstance(item, dict):
                    continue
                # Likes and reposts wrap the track; /tracks returns it bare.
                data = item.get("track") if "track" in item else item
                if isinstance(data, dict) and data.get("kind") == "track":
                    tracks.append(Track.from_api(data))
            if on_progress:
                on_progress(len(tracks), limit)
            if limit is not None and len(tracks) >= limit:
                break
            next_href = payload.get("next_href")
            if not next_href:
                break
            payload = self._request(next_href)

        return tracks[:limit] if limit is not None else tracks

    def collect(
        self,
        url: str,
        *,
        limit: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> Crate:
        """Pull every track behind a SoundCloud link."""

        collection, base_url = split_user_collection(url)
        payload = self.resolve(base_url)
        kind = payload.get("kind")

        if kind == "user":
            username = payload.get("username") or base_url
            user_id = payload.get("id")
            if not user_id:
                raise SoundCloudError(f"Resolved {base_url} to a user without an id")
            endpoint = collection or "tracks"
            tracks = self._paginate(
                f"/users/{user_id}/{endpoint}", limit=limit, on_progress=on_progress
            )
            return Crate(
                source=url,
                tracks=tracks,
                title=f"{username} - {endpoint}",
                declared_count=len(tracks),
            )

        if kind == "track":
            return Crate(
                source=url,
                tracks=[Track.from_api(payload)],
                title=payload.get("title") or url,
                declared_count=1,
            )

        raw_tracks = payload.get("tracks")
        if isinstance(raw_tracks, list):
            track_ids = [
                item["id"]
                for item in raw_tracks
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            ]
            declared = payload.get("track_count") or len(track_ids)
            if limit is not None:
                track_ids = track_ids[:limit]
            tracks = self.hydrate_tracks(track_ids, on_progress=on_progress)
            return Crate(
                source=url,
                tracks=tracks,
                title=payload.get("title") or url,
                declared_count=declared,
            )

        raise SoundCloudError(
            f"Nothing diggable behind that link (SoundCloud calls it '{kind}'). "
            "Supported: a playlist, an artist profile, /likes or a single track."
        )


def collect_tracks(
    url: str,
    *,
    limit: int | None = None,
    timeout: float = 20.0,
    on_progress: ProgressCallback | None = None,
    session: requests.Session | None = None,
) -> Crate:
    """Convenience wrapper for one-shot use."""

    with SoundCloudClient(session=session, timeout=timeout) as client:
        return client.collect(url, limit=limit, on_progress=on_progress)


def hydrate_ids(
    track_ids: Iterable[int],
    *,
    timeout: float = 20.0,
    on_progress: ProgressCallback | None = None,
    session: requests.Session | None = None,
) -> list[Track]:
    with SoundCloudClient(session=session, timeout=timeout) as client:
        return client.hydrate_tracks(list(track_ids), on_progress=on_progress)

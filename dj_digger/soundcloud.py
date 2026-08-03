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

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests
from platformdirs import user_cache_dir
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Crate, Track

API_ROOT = "https://api-v2.soundcloud.com"
DISCOVER_URL = "https://soundcloud.com/discover"

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

ProgressCallback = Callable[[int, Optional[int]], None]


class SoundCloudError(RuntimeError):
    """Raised when SoundCloud cannot be reached or gives us nothing usable."""


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


def is_soundcloud_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower().partition(":")[0]
    return host == "soundcloud.com" or host.endswith(".soundcloud.com")


def _client_id_cache() -> Path:
    return Path(user_cache_dir("dj-digger")) / "client_id.txt"


def _chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def split_user_collection(url: str) -> Tuple[Optional[str], str]:
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
        session: Optional[requests.Session] = None,
        *,
        timeout: float = 20.0,
        client_id: Optional[str] = None,
    ) -> None:
        self._session = session or create_requests_session()
        self._timeout = timeout
        self._client_id = client_id

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "SoundCloudClient":
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

    def _request(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET with one automatic retry against a freshly discovered client_id."""

        for attempt in (0, 1):
            merged = dict(params or {})
            merged["client_id"] = self.client_id
            try:
                response = self._session.get(url, params=merged, timeout=self._timeout)
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

    def fetch_track(self, track_id: int) -> Dict[str, Any]:
        """The raw payload for one track.

        ``Track`` deliberately keeps only what the link digger needs, so playback
        has to come here for ``media`` and ``track_authorization``.
        """

        payload = self._get("/tracks", ids=str(int(track_id)))
        if not isinstance(payload, list) or not payload:
            raise SoundCloudError(f"Track {track_id} is no longer available")
        return payload[0]

    def authorize(self, url: str, **params: Any) -> Dict[str, Any]:
        """Call an absolute api-v2 URL, such as a media transcoding."""

        payload = self._request(url, params)
        if not isinstance(payload, dict):
            raise SoundCloudError(f"Unexpected reply from {url}")
        return payload

    def download_track(self, track: Track, directory: Path) -> Path:
        """Save the artist-provided download, never a playback stream."""

        if not track.has_direct_download or not track.download_url:
            raise SoundCloudError("This track has no active direct download")
        host = (urlparse(track.download_url).hostname or "").lower()
        if not (
            host == "soundcloud.com"
            or host.endswith(".soundcloud.com")
            or host.endswith(".sndcdn.com")
        ):
            raise SoundCloudError("SoundCloud returned an unsafe download URL")

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{_download_stem(track)}.mp3"
        temporary = target.with_name(target.name + ".part")
        completed = False
        try:
            response = self._session.get(
                track.download_url,
                params={"client_id": self.client_id},
                timeout=self._timeout,
                stream=True,
            )
            if response.status_code >= 400:
                raise SoundCloudError(
                    f"SoundCloud returned HTTP {response.status_code} for the download"
                )
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        handle.write(chunk)
            os.replace(temporary, target)
            completed = True
        except requests.RequestException as exc:
            raise SoundCloudError(f"Download request failed: {exc}") from exc
        finally:
            if not completed:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        return target

    def resolve(self, url: str) -> Dict[str, Any]:
        payload = self._get("/resolve", url=url)
        if not isinstance(payload, dict):
            raise SoundCloudError(f"Unexpected reply when resolving {url}")
        return payload

    def hydrate_tracks(
        self,
        track_ids: Sequence[int],
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> List[Track]:
        """Turn bare track ids into full track objects, 50 per request."""

        ids = [int(tid) for tid in track_ids]
        if not ids:
            return []

        position = {track_id: index for index, track_id in enumerate(ids)}
        tracks: List[Track] = []
        for chunk in _chunks(ids, HYDRATE_BATCH):
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
        limit: Optional[int] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> List[Track]:
        tracks: List[Track] = []
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
        limit: Optional[int] = None,
        on_progress: Optional[ProgressCallback] = None,
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
    limit: Optional[int] = None,
    timeout: float = 20.0,
    on_progress: Optional[ProgressCallback] = None,
    session: Optional[requests.Session] = None,
) -> Crate:
    """Convenience wrapper for one-shot use."""

    with SoundCloudClient(session=session, timeout=timeout) as client:
        return client.collect(url, limit=limit, on_progress=on_progress)


def hydrate_ids(
    track_ids: Iterable[int],
    *,
    timeout: float = 20.0,
    on_progress: Optional[ProgressCallback] = None,
    session: Optional[requests.Session] = None,
) -> List[Track]:
    with SoundCloudClient(session=session, timeout=timeout) as client:
        return client.hydrate_tracks(list(track_ids), on_progress=on_progress)

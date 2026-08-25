"""Bypass and resolver module for download gates (Hypeddit, ToneDen, etc.).

Extracts direct file download URLs from gate pages without requiring manual
social media login steps.
"""

import html
import json
import logging
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from . import spotify
from .browser import is_fetchable
from .html_fallback import normalize_link
from .links import SHOP_CATEGORIES, host_of, store_for_url

LOGGER = logging.getLogger(__name__)


class GateError(RuntimeError):
    """A recognised gate could not be completed safely."""


class GateProfileRequired(GateError):
    """The gate would submit contact data that the user has not configured."""


class GateSocialActionsDisabled(GateError):
    """The user declined the social steps declared by this gate."""


class GateAuthenticationRequired(GateError):
    """A provider-owned OAuth flow must be completed in a browser."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"Hypeddit requires {provider} authentication in a browser")


class GateCaptchaRequired(GateError):
    """The gate presented an interactive anti-bot challenge."""


class GateManualActionRequired(GateError):
    """The gate declared a step whose semantics are not known."""

    def __init__(self, step: str) -> None:
        self.step = step
        super().__init__(f"Hypeddit step {step!r} requires browser completion")


class GateProtocolChanged(GateError):
    """The provider answered, but no longer speaks the supported protocol."""


class GateRejected(GateError):
    """The provider understood the request but refused to unlock the file."""


class GateDownloadError(GateError):
    """A resolved gate file could not be transferred or validated."""


class GateUnavailable(GateError):
    """The provider or the requested gate is unavailable."""


BROWSER_REQUIRED_ERRORS = (
    GateAuthenticationRequired,
    GateCaptchaRequired,
    GateManualActionRequired,
    GateProtocolChanged,
)


@dataclass(frozen=True)
class HypedditManifest:
    """The short-lived fields used by one desktop Hypeddit download attempt."""

    csrf: str
    file_id: str
    gvt: str
    gvf: str
    wrndk: str
    external_id: str
    steps: tuple[str, ...]
    fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    sc_comment_required: bool = False
    yt_comment_required: bool = False
    email_download_required: bool = False
    is_skippable: str = "0"
    is_mobile: str = ""
    hypesource: str = ""
    adcode: str = ""


@dataclass(frozen=True)
class HypedditInspection:
    """What one Hypeddit HTML document represents, without executing it."""

    kind: str
    url: str
    shops: tuple[tuple[str, str], ...] = ()
    nested_gates: tuple[str, ...] = ()
    direct_url: str | None = None
    manifest: HypedditManifest | None = None


@dataclass(frozen=True)
class LinkPageInspection:
    """Shops and gates found behind one purchase link."""

    shops: tuple[tuple[str, str], ...] = ()
    gate_urls: tuple[str, ...] = ()
    keep_original: bool = False
    recognized: bool = False


@dataclass(frozen=True)
class HypedditBrowserBatchResult:
    """Files and typed failures produced by one shared Chromium context."""

    completed: tuple[tuple[str, Path], ...] = ()
    failures: tuple[tuple[str, GateError], ...] = ()
    cancelled: bool = False


def _identity_for(config: Any | None) -> Any:
    """The profile a gate form gets filled in with.

    Warns when it is still the placeholder: these resolvers post a name and an
    email to somebody else's server, and the artist on the other end deserves a
    contact that exists rather than a reserved .invalid address.
    """

    if config is None:
        from .config import AppConfig

        config = AppConfig()
    if not config.has_real_email():
        LOGGER.warning(
            "The gate profile still uses a placeholder address; a gate that "
            "requires email will pause before submitting it."
        )
    return config

TONEDEN_RE = re.compile(r'https?://(?:www\.)?toneden\.io/([^/]+)/post/([a-zA-Z0-9_-]+)')

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _clean_url(raw_url: str | None) -> str | None:
    """Clean and validate an extracted download URL, rejecting preview clips."""
    if not raw_url or not isinstance(raw_url, str):
        return None
    cleaned = html.unescape(raw_url.replace("\\/", "/")).strip('"\' ')
    lower = cleaned.lower()
    if "_preview" in lower or "/preview/" in lower:
        return None
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return None


def _url_for_log(url: str) -> str:
    """Keep host/path for diagnostics while dropping credentials and query secrets."""

    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return "<invalid-url>"
    host = parsed.hostname or "<unknown-host>"
    return urllib.parse.urlunparse((parsed.scheme, host, parsed.path, "", "", ""))


CLICK_THROUGH_STEPS = frozenset(
    {"sc", "yt", "ig", "tw", "fb", "tk", "bc", "mc", "dn", "fbmsgr"}
)
PROVIDER_OAUTH_STEPS = {"dz": "Deezer", "ap": "Apple Music", "th": "Threads"}
_HYPEDDIT_FLOW_LOCK = threading.Lock()
MAX_GATE_REDIRECTS = 5


class _UnsafeGateRedirect(ValueError):
    pass


def _safe_page_get(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str],
    timeout: Any,
) -> tuple[Any, str]:
    """GET a page while validating every redirect target before requesting it."""

    current = url
    for _hop in range(MAX_GATE_REDIRECTS + 1):
        if not is_fetchable(current):
            raise _UnsafeGateRedirect("Page redirected to an unsafe address")
        response = session.get(
            current,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response, current
        location = str(getattr(response, "headers", {}).get("Location", ""))
        close = getattr(response, "close", None)
        if callable(close):
            close()
        if not location:
            raise _UnsafeGateRedirect("Page redirect had no destination")
        current = urllib.parse.urljoin(current, location)
    raise _UnsafeGateRedirect("Page exceeded the redirect limit")


def _is_hypeddit_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in {
        "hypeddit.com",
        "www.hypeddit.com",
        "hypd.it",
        "www.hypd.it",
    }


def _input_fields(soup: BeautifulSoup) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for tag in soup.find_all("input"):
        name = str(tag.get("name") or tag.get("id") or "")
        if name:
            found.setdefault(name, []).append(str(tag.get("value") or ""))
    return {name: tuple(values) for name, values in found.items()}


def _first(fields: dict[str, tuple[str, ...]], name: str, default: str = "") -> str:
    values = fields.get(name, ())
    return values[0] if values else default


def _is_hidden(tag: Any) -> bool:
    for node in (tag, *tag.parents):
        if not getattr(node, "attrs", None):
            continue
        style = str(node.get("style") or "").replace(" ", "").lower()
        if (
            node.has_attr("hidden")
            or str(node.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        ):
            return True
    return False


def _has_active_captcha(soup: BeautifulSoup) -> bool:
    """Find a rendered challenge, not a library used by a dormant modal."""

    for tag in soup.select(
        ".g-recaptcha, .h-captcha, .cf-turnstile, form [data-sitekey]"
    ):
        if tag.name != "script" and not _is_hidden(tag):
            return True
    return False


def _reply_requests_captcha(reply: dict[str, Any]) -> bool:
    """Recognise explicit challenge flags from the download flow reply."""

    for name in ("captcha_required", "requires_captcha", "show_captcha"):
        value = reply.get(name)
        if value is True or value == 1:
            return True
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes"}:
            return True
    return False


def inspect_hypeddit_html(url: str, text: str) -> HypedditInspection:
    """Classify a Hypeddit document and parse its short-lived desktop manifest."""

    soup = BeautifulSoup(text or "", "html.parser")
    landed = url

    shops: list[tuple[str, str]] = []
    nested: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = normalize_link(landed, str(anchor.get("href") or "").strip())
        if not href or href in seen:
            continue
        seen.add(href)
        label = anchor.get_text(" ", strip=True) or host_of(href)
        if store_for_url(href) in SHOP_CATEGORIES:
            shops.append((href, label))
        elif _is_hypeddit_url(href) and "/track/" in urllib.parse.urlparse(href).path:
            if href.rstrip("/") != landed.rstrip("/"):
                nested.append(href)

    fields = _input_fields(soup)
    raw_steps = _first(fields, "nwSteps")
    steps = tuple(step.strip().lower() for step in raw_steps.split(",") if step.strip())

    csrf_tag = soup.find("meta", attrs={"name": "csrf-token"})
    csrf = str(csrf_tag.get("content") or "") if csrf_tag else ""
    external_id = ""
    gate_data = re.search(r"var\s+jsonGateData\s*=\s*({.*?});", text or "", re.DOTALL)
    if gate_data:
        try:
            parsed = json.loads(gate_data.group(1))
            if isinstance(parsed, dict):
                external_id = str(parsed.get("externID") or "")
        except ValueError:
            pass

    script = soup.find("script", id="gate_ul_preview_js")
    hypesource = str(script.get("data-hypesource") or "") if script else ""
    adcode = str(script.get("data-adcode") or "") if script else ""
    fan_gate_id = _first(fields, "fan_gate_id") or _first(fields, "fangate_id")
    download_file_id = _first(fields, "current_download_file_listner")
    file_id = download_file_id or _first(fields, "fangate_id")
    manifest = None
    # Smartlinks also carry fan_gate_id for click telemetry. It is not evidence
    # of a download form: ky9i8z has one beside its Beatport/Bandcamp buttons and
    # points at a separate /track gate. An explicit step list or download-file
    # field is what distinguishes an actual desktop gate manifest.
    if raw_steps or download_file_id:
        additional: dict[str, tuple[str, ...]] = {
            name: values
            for name, values in fields.items()
            if re.fullmatch(
                r"additional_[a-z0-9_]+_(?:user_id|type_array)\[\]", name,
                re.IGNORECASE,
            )
        }
        for tag in soup.find_all("input"):
            name = str(tag.get("name") or "")
            match = re.fullmatch(
                r"additional_([a-z0-9_]+)_user_id\[\]", name,
                re.IGNORECASE,
            )
            profile_type = str(tag.get("data-profile_type") or "")
            if match and profile_type:
                key = f"additional_{match.group(1)}_type_array[]"
                additional[key] = (*additional.get(key, ()), profile_type)
        manifest = HypedditManifest(
            csrf=csrf,
            file_id=file_id or fan_gate_id,
            gvt=_first(fields, "gvt"),
            gvf=_first(fields, "gvf", "0"),
            wrndk=_first(fields, "wrndk"),
            external_id=external_id,
            steps=steps,
            fields=additional,
            sc_comment_required=_first(fields, "comment_sc") == "1",
            yt_comment_required=_first(fields, "comment_yt") == "1",
            email_download_required="email" in steps,
            is_skippable=_first(fields, "is_skippable", "0"),
            is_mobile=_first(fields, "is_mobile"),
            hypesource=hypesource or _first(fields, "hypesource"),
            adcode=adcode or _first(fields, "adcode"),
        )

    smartlink = bool(
        re.search(
            r"\bisSmartLink\s*=\s*['\"]1['\"]",
            text or "",
            re.IGNORECASE,
        )
    )
    if _has_active_captcha(soup):
        kind = "challenge"
    elif manifest and (shops or nested):
        kind = "hybrid"
    elif manifest:
        kind = "gate"
    elif smartlink or shops or nested:
        kind = "hub"
    else:
        kind = "unknown"
    return HypedditInspection(
        kind=kind,
        url=landed,
        shops=tuple(shops),
        nested_gates=tuple(nested),
        # Hypeddit pages contain unrelated recommendations and preview assets.
        # Only /gate/download/ul is scoped to the current fan_gate_id, so the
        # landing document is never trusted as a file source.
        direct_url=None,
        manifest=manifest,
    )


def _spotify_uris(manifest: HypedditManifest) -> list[str]:
    uris: list[str] = []
    for raw in manifest.fields.get("additional_sp_user_id[]", ()):
        parts = raw.split("|")
        kind = parts[0] if parts else ""
        if kind == "ART" and len(parts) == 2:
            spotify_id = parts[1]
            uri_kind = "artist"
        elif kind == "PLAY" and len(parts) in {2, 3}:
            if len(parts) == 3 and not re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]):
                raise GateManualActionRequired("sp")
            spotify_id = parts[-1]
            uri_kind = "playlist"
        else:
            raise GateManualActionRequired("sp")
        if not re.fullmatch(r"[A-Za-z0-9]{22}", spotify_id):
            raise GateManualActionRequired("sp")
        uris.append(f"spotify:{uri_kind}:{spotify_id}")
    if not uris:
        raise GateAuthenticationRequired("Spotify")
    return uris


def resolve_hypeddit_download_url(
    url: str,
    session: requests.Session,
    timeout: float = 10.0,
    config: Any | None = None,
    _visited: frozenset[str] = frozenset(),
    _flow_locked: bool = False,
) -> str | None:
    """Resolve the current desktop Hypeddit flow without faking provider writes."""

    if not _flow_locked:
        with _HYPEDDIT_FLOW_LOCK:
            return resolve_hypeddit_download_url(
                url,
                session,
                timeout=timeout,
                config=config,
                _visited=_visited,
                _flow_locked=True,
            )
    if not _is_hypeddit_url(url) or not is_fetchable(url):
        return None
    canonical = url.rstrip("/")
    if canonical in _visited or len(_visited) >= 5:
        raise GateProtocolChanged("Hypeddit nested-gate cycle or redirect limit reached")
    visited = _visited | {canonical}
    config = _identity_for(config)
    headers = {**DEFAULT_HEADERS, "Referer": url}

    try:
        response, landed = _safe_page_get(
            session, url, headers=headers, timeout=timeout
        )
    except _UnsafeGateRedirect as exc:
        raise GateProtocolChanged(str(exc)) from exc
    except requests.RequestException as exc:
        raise GateUnavailable("Hypeddit page could not be reached") from exc
    if response.status_code != 200:
        raise GateUnavailable(f"Hypeddit page returned HTTP {response.status_code}")
    if not _is_hypeddit_url(landed):
        raise GateProtocolChanged("Hypeddit redirected outside its canonical hosts")

    inspection = inspect_hypeddit_html(landed, response.text)
    if inspection.kind == "challenge":
        raise GateCaptchaRequired("Hypeddit CAPTCHA requires browser completion")
    if inspection.manifest is None:
        for nested in inspection.nested_gates:
            resolved = resolve_hypeddit_download_url(
                nested,
                session,
                timeout=timeout,
                config=config,
                _visited=frozenset(visited),
                _flow_locked=True,
            )
            if resolved:
                return resolved
        if inspection.kind == "hub":
            return None
        raise GateProtocolChanged("Hypeddit page has no supported download manifest")

    manifest = inspection.manifest
    if not manifest.file_id or not manifest.steps:
        raise GateProtocolChanged("Hypeddit gate manifest is incomplete")
    if "email" in manifest.steps and not config.has_real_email():
        raise GateProfileRequired(
            "Hypeddit requires a real email; set it in Settings before downloading"
        )

    social_steps = [step for step in manifest.steps if step != "email"]
    social = bool(getattr(config, "gate_social_actions", True))
    if social_steps and not social:
        raise GateSocialActionsDisabled(
            "This Hypeddit gate requires social steps, but they are disabled"
        )

    completed: list[str] = []
    for step in social_steps:
        if step in CLICK_THROUGH_STEPS:
            completed.append(step)
        elif step == "sp":
            try:
                spotify.save_uris(_spotify_uris(manifest))
            except spotify.SpotifyError as exc:
                raise GateAuthenticationRequired("Spotify") from exc
            completed.append(step)
        elif step in PROVIDER_OAUTH_STEPS:
            raise GateAuthenticationRequired(PROVIDER_OAUTH_STEPS[step])
        else:
            raise GateManualActionRequired(step)

    ajax_headers = {
        **headers,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if manifest.csrf:
        ajax_headers["X-CSRF-TOKEN"] = manifest.csrf

    comment = config.random_comment() if social else ""
    payload: dict[str, Any] = {
        "file": urllib.parse.quote(manifest.file_id, safe=""),
        "download_visit": "true",
        "profile_downloads": "true",
        "time": 0,
        "sc_comment_text": comment if manifest.sc_comment_required else "",
        "yt_comment_text": comment if manifest.yt_comment_required else "",
        "page": "nonsingle",
        "is_skippable": manifest.is_skippable,
        "steps": ",".join(manifest.steps),
        "email": config.user_email if "email" in manifest.steps else "",
        "download_action": "DOWNLOAD",
        "skip_gate_steps[]": completed,
        "wrndk": manifest.wrndk,
        "is_mobile": manifest.is_mobile,
        "external_id": manifest.external_id,
        "hypesource": manifest.hypesource,
        "adcode": manifest.adcode,
        "lifetime_fan_spotify": "0",
        "lifetime_fan_deezer": "0",
        "lifetime_fan_apple": "0",
        "gvf": manifest.gvf,
        **{name: list(values) for name, values in manifest.fields.items()},
    }
    try:
        if manifest.gvt:
            try:
                session.post(
                    "https://hypeddit.com/gate/ge",
                    data={"vt": manifest.gvt, "uid": manifest.file_id},
                    headers=ajax_headers,
                    timeout=timeout,
                )
            except requests.RequestException:
                LOGGER.debug("Hypeddit telemetry failed for %s", _url_for_log(url))
        download = session.post(
            "https://hypeddit.com/gate/download/ul",
            data=payload,
            headers=ajax_headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise GateRejected("Hypeddit download request failed") from exc
    if download.status_code != 200:
        raise GateRejected(f"Hypeddit download returned HTTP {download.status_code}")
    try:
        result = download.json()
    except ValueError as exc:
        raise GateProtocolChanged("Hypeddit returned an unreadable download reply") from exc
    if isinstance(result, dict) and _reply_requests_captcha(result):
        raise GateCaptchaRequired("Hypeddit CAPTCHA requires browser completion")
    if not isinstance(result, dict) or not result.get("download_status"):
        raise GateRejected("Hypeddit did not unlock the download")
    cleaned = _clean_url(result.get("URL") or result.get("url"))
    if not cleaned or not is_fetchable(cleaned):
        raise GateProtocolChanged("Hypeddit returned an unsafe download URL")
    return cleaned


def download_hypeddit_in_browser(
    track: Any,
    url: str,
    directory: Path,
    cancel: Any,
) -> Path:
    """Finish a provider-owned Hypeddit flow in the existing private browser."""

    if not _is_hypeddit_url(url) or not is_fetchable(url):
        raise GateProtocolChanged("Refusing an unsafe Hypeddit browser URL")
    from . import auth, cart, soundcloud

    if not auth.BROWSER_PROFILE_LOCK.acquire(blocking=False):
        raise GateUnavailable("The private browser profile is already in use")
    try:
        downloads: list[Any] = []
        watched: set[int] = set()

        def watch(page: Any) -> None:
            marker = id(page)
            if marker in watched:
                return
            watched.add(marker)
            page.on("download", lambda item: downloads.append(item))

        try:
            with cart._browser_context(
                auth.soundcloud_browser_profile_path(), accept_downloads=True
            ) as context:
                context.on("page", watch)
                page = context.pages[0] if context.pages else context.new_page()
                for existing in context.pages:
                    watch(existing)
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                if not _is_hypeddit_url(page.url):
                    raise GateProtocolChanged("Hypeddit redirected outside its canonical hosts")

                deadline = time.monotonic() + 300
                while time.monotonic() < deadline and not downloads:
                    if cancel is not None and cancel.is_set():
                        raise GateManualActionRequired("cancelled")
                    for open_page in context.pages:
                        watch(open_page)
                    page.wait_for_timeout(250)
                if not downloads:
                    raise GateManualActionRequired("manual browser step")
                return soundcloud.save_browser_download(downloads[0], track, directory)
        except GateError:
            raise
        except cart.ChromiumMissing:
            raise GateUnavailable("Playwright Chromium is not installed")
        except cart.AutomationError as exc:
            raise GateUnavailable(str(exc)) from exc
    finally:
        auth.BROWSER_PROFILE_LOCK.release()


def download_hypeddit_batch_in_browser(
    items: list[tuple[Any, str]],
    directory: Path,
    cancel: Any,
) -> HypedditBrowserBatchResult:
    """Open every manual Hypeddit gate in one persistent browser context."""

    keyed = {track.key: (track, url) for track, url in items}
    failures: dict[str, GateError] = {}
    completed: dict[str, Path] = {}
    if cancel is not None and cancel.is_set():
        return HypedditBrowserBatchResult(
            failures=tuple(
                (key, GateManualActionRequired("browser batch cancelled"))
                for key in keyed
            ),
            cancelled=True,
        )
    for key, (_track, url) in keyed.items():
        if not _is_hypeddit_url(url) or not is_fetchable(url):
            failures[key] = GateProtocolChanged(
                "Refusing an unsafe Hypeddit browser URL"
            )
    pending = {key: value for key, value in keyed.items() if key not in failures}
    if not pending:
        return HypedditBrowserBatchResult(failures=tuple(failures.items()))

    from . import auth, cart, soundcloud

    if not auth.BROWSER_PROFILE_LOCK.acquire(blocking=False):
        error = GateUnavailable("The private browser profile is already in use")
        return HypedditBrowserBatchResult(
            failures=tuple((key, error) for key in pending)
        )
    cancelled = False
    try:
        watched: set[int] = set()
        owners: dict[int, str] = {}

        def save(key: str, download: Any) -> None:
            if (
                key in completed
                or key in failures
                or (cancel is not None and cancel.is_set())
            ):
                return
            track, _url = pending[key]
            try:
                completed[key] = soundcloud.save_browser_download(
                    download, track, directory
                )
            except Exception as exc:
                failures[key] = GateDownloadError(str(exc))

        def watch(page: Any, key: str) -> None:
            marker = id(page)
            if marker in watched:
                return
            watched.add(marker)
            owners[marker] = key
            page.on("download", lambda download, owner=key: save(owner, download))
            page.on("popup", lambda popup, owner=key: watch(popup, owner))

        def watch_popup(page: Any) -> None:
            try:
                opener = page.opener
            except Exception:
                opener = None
            key = owners.get(id(opener)) if opener is not None else None
            if key is not None:
                watch(page, key)

        try:
            with cart._browser_context(
                auth.soundcloud_browser_profile_path(), accept_downloads=True
            ) as context:
                context.on("page", watch_popup)
                existing = list(context.pages)
                pages: list[tuple[str, Any, str]] = []
                for index, (key, (_track, url)) in enumerate(pending.items()):
                    page = (
                        existing[index]
                        if index < len(existing)
                        else context.new_page()
                    )
                    watch(page, key)
                    pages.append((key, page, url))

                # Every tab exists before the first navigation begins. A slow
                # page therefore cannot serialize the user's whole queue into
                # one short-lived context per track.
                for key, page, url in pages:
                    if cancel is not None and cancel.is_set():
                        cancelled = True
                        break
                    try:
                        page.goto(
                            url, wait_until="domcontentloaded", timeout=30_000
                        )
                        if not _is_hypeddit_url(str(page.url)):
                            failures[key] = GateProtocolChanged(
                                "Hypeddit redirected outside its canonical hosts"
                            )
                    except Exception as exc:
                        failures[key] = GateUnavailable(
                            f"Could not open Hypeddit in Chromium: {exc}"
                        )

                while len(completed) + len(failures) < len(pending):
                    if cancel is not None and cancel.is_set():
                        cancelled = True
                        break
                    try:
                        open_pages = [
                            page
                            for page in context.pages
                            if id(page) in owners
                        ]
                    except Exception:
                        break
                    if not open_pages:
                        break
                    try:
                        open_pages[0].wait_for_timeout(250)
                    except Exception:
                        # Closing the whole window races the last short wait.
                        # Treat it exactly like context.pages becoming empty.
                        break

                reason = (
                    "browser batch cancelled"
                    if cancelled
                    else "browser tab closed before the download finished"
                )
                for key in pending:
                    if key not in completed and key not in failures:
                        failures[key] = GateManualActionRequired(reason)
        except cart.ChromiumMissing:
            error = GateUnavailable("Playwright Chromium is not installed")
            for key in pending:
                failures.setdefault(key, error)
        except cart.AutomationError as exc:
            error = GateUnavailable(str(exc))
            for key in pending:
                failures.setdefault(key, error)
    finally:
        auth.BROWSER_PROFILE_LOCK.release()

    return HypedditBrowserBatchResult(
        completed=tuple(completed.items()),
        failures=tuple(failures.items()),
        cancelled=cancelled,
    )


def resolve_toneden_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> str | None:
    """Resolve direct audio download URL from ToneDen fan gate link."""
    match = TONEDEN_RE.search(url)
    if not match:
        return None

    headers = {**DEFAULT_HEADERS, "Referer": url}

    try:
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            text = resp.text

            # 1. Extract JSON state from page HTML
            json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});</script>', text, re.DOTALL)
            if json_match:
                try:
                    state = json.loads(json_match.group(1))
                    state_str = json.dumps(state)
                    for key_name in ("download_url", "free_download_location", "s3_url", "file_url"):
                        dl_match = re.search(rf'"{key_name}"\s*:\s*"([^"]+)"', state_str)
                        if dl_match:
                            cleaned = _clean_url(dl_match.group(1))
                            if cleaned:
                                return cleaned
                except Exception:
                    pass

            # 2. Try ToneDen fan gate API directly
            gate_slug = match.group(2)
            api_url = f"https://www.toneden.io/api/v1/fan_gates/slug/{gate_slug}"
            api_resp = session.get(api_url, headers=headers, timeout=timeout)
            if api_resp.status_code == 200:
                try:
                    data = api_resp.json()
                    if isinstance(data, dict):
                        for field in ("download_url", "free_download_location", "s3_url", "file_url"):
                            cleaned = _clean_url(data.get(field))
                            if cleaned:
                                return cleaned
                except ValueError:
                    pass
    except requests.RequestException as exc:
        LOGGER.debug(
            "ToneDen gate resolution failed for %s (%s)",
            _url_for_log(url),
            type(exc).__name__,
        )

    return None


DROPLOUD_RE = re.compile(
    r"https?://(?:www\.)?droploud\.com/(?:track|gate)/([a-f0-9\-]{36}|[a-zA-Z0-9_\-]+)", re.I
)


def resolve_droploud_download_url(
    url: str, session: requests.Session, timeout: float = 10.0
) -> str | None:
    """Resolve direct audio stream/download URL from Droploud track gate."""
    match = DROPLOUD_RE.search(url)
    if not match:
        return None
    track_id = match.group(1)
    api_url = f"https://api.droploud.com/api/tracks/{track_id}"
    try:
        resp = session.get(api_url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("stream_url"):
                stream_path = data["stream_url"]
                if stream_path.startswith(("http://", "https://")):
                    return stream_path
                return f"https://api.droploud.com{stream_path}"
    except requests.RequestException as exc:
        LOGGER.debug(
            "Droploud gate resolution failed for %s (%s)",
            _url_for_log(url),
            type(exc).__name__,
        )
    return None


GATERUSH_RE = re.compile(r"https?://(?:www\.)?gaterush\.me/([a-zA-Z0-9_-]+)", re.I)


def resolve_gaterush_download_url(
    url: str, session: requests.Session, timeout: float = 10.0, config: Any | None = None
) -> str | None:
    """Resolve direct audio download URL from GateRush fan gate link."""
    config = _identity_for(config)
    if not config.has_real_email():
        raise GateProfileRequired(
            "GateRush requires a real email; set it in Settings before downloading"
        )
    email = config.user_email
    comment = config.random_comment()

    match = GATERUSH_RE.search(url)
    if not match:
        return None
    slug = match.group(1)
    try:
        headers = {**DEFAULT_HEADERS, "Referer": url}
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            text = resp.text
            csrf_m = re.search(r'name="_csrf"\s+value="([^"]+)"', text) or re.search(r'csrf-token.*content="([^"]+)"', text)
            csrf = csrf_m.group(1) if csrf_m else ""
            ajax_headers = {**headers, "X-CSRF-Token": csrf, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}

            # The email is what the download is for; the comment is posted in
            # your name, so it goes only when you have said it may.
            if getattr(config, "gate_social_actions", True):
                session.post(f"https://gaterush.me/save-comment/{slug}", data={"commentText": comment, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)
            session.post(f"https://gaterush.me/save-email/{slug}", data={"email": email, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)

            steps_m = re.findall(r'id["\']:\s*["\']([^"\']+)["\']', text)
            for st in set(steps_m) | {"instagram", "spotify", "youtube", "soundcloud", "email", "tiktok"}:
                session.post(f"https://gaterush.me/gate-step/{slug}", data={"stepId": st, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)

            dl_resp = session.get(f"https://gaterush.me/download/{slug}", headers=headers, allow_redirects=False, timeout=timeout)
            if dl_resp.status_code in (301, 302) and dl_resp.headers.get("Location"):
                return dl_resp.headers["Location"]
            elif dl_resp.status_code == 200:
                return f"https://gaterush.me/download/{slug}"
    except requests.RequestException as exc:
        LOGGER.debug(
            "GateRush gate resolution failed for %s (%s)",
            _url_for_log(url),
            type(exc).__name__,
        )
    return None


def resolve_mediafire_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> str | None:
    """Extract direct download link from MediaFire page."""
    try:
        resp = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            match = re.search(r'href=["\'](https?://download\d+\.mediafire\.com/[^"\']+)["\']', resp.text)
            if match:
                return match.group(1)
            btn = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', resp.text)
            if btn:
                return btn.group(1)
    except requests.RequestException:
        pass
    return url


def resolve_dropbox_download_url(url: str) -> str:
    """Convert Dropbox link to direct download link."""
    return url.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("dl=0", "dl=1")


def resolve_google_drive_download_url(url: str) -> str:
    """Convert Google Drive view link to direct download link."""
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', url) or re.search(r'id=([a-zA-Z0-9_-]+)', url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url


# Every host the routing below knows how to unwrap. It lives here rather than
# beside the caller that has to pick a candidate link, because a second copy of
# this list is a second copy that drifts.
RESOLVABLE_HOSTS = (
    "hypeddit.com",
    "www.hypeddit.com",
    "hypd.it",
    "www.hypd.it",
    "droploud.com",
    "www.droploud.com",
    "gaterush.me",
    "www.gaterush.me",
    "toneden.io",
    "www.toneden.io",
    "mediafire.com",
    "www.mediafire.com",
    "dropbox.com",
    "www.dropbox.com",
    "dl.dropboxusercontent.com",
    "drive.google.com",
    "docs.google.com",
)


# Hubs wrap each shop in their own redirect (ampsuite's link-redirect, most
# smart-link services), so the real destination only shows up in a Location
# header. A page listing more than this many is a page we have misread.
HUB_REDIRECT_LIMIT = 12


# What the thing you press on a gate says it will do. Several languages, because
# a gate run by a German or Spanish label was invisible to a match on the English
# word alone and got rewritten into a shop list.
DOWNLOAD_WORDS = (
    "download",
    "herunterladen",
    "descargar",
    "télécharger",
    "telecharger",
    "scarica",
    "pobierz",
    "baixar",
)

# Where a page says what pressing it does. Matching the whole document instead
# caught every shop page with the word in a footer, a cookie banner or a script.
ACTION_TAGS = ("a", "button", "input", "label", "h1", "h2", "h3")


def _offers_a_download(text: str) -> bool:
    """Whether the page offers to hand over a file at all.

    A real follow-to-download gate says so on the thing you press, and a page
    that says it keeps its gate badge rather than being replaced by the shop it
    also happens to link to. A pure link list never says it.

    ponytail: the words are a fixed list in eight languages, and only the action
    elements are read. A gate whose button is an image, or whose language is not
    here, still reads as a hub. Widening it means the word list, not the shape.
    """


    soup = BeautifulSoup(text or "", "html.parser")
    for tag in soup.find_all(ACTION_TAGS):
        # An <input type=submit> carries its label in value=, not in its text.
        candidates = (tag.get_text(" ", strip=True), tag.get("value") or "", tag.get("title") or "")
        haystack = " ".join(candidates).lower()
        if any(word in haystack for word in DOWNLOAD_WORDS):
            return True
    return False


def inspect_link_page(
    url: str,
    session: requests.Session,
    timeout: float = 10.0,
) -> LinkPageInspection | None:
    """Inspect one purchase link without losing hybrid gate-and-shop pages.

    Some pages behind a purchase link hand over no file: ampsuite release pages,
    and gates run in smart-link mode, are a list of streaming services and shops.
    Those shops are the point, so they are read off the page and the caller drops
    the hub itself.

    ``None`` rather than ``[]`` when the host never answered, so a caller can tell
    "this page had nothing for us" from "this host is gone" and stop asking. A 404
    is the first kind: something replied.
    """


    # The caller filters too, but this is the function that issues the request,
    # so it is the one that has to refuse an address inside the user's network.
    if not is_fetchable(url):
        LOGGER.debug(
            "Refusing to read %s - not an address worth reaching out to.",
            _url_for_log(url),
        )
        return LinkPageInspection()

    try:
        response, landed = _safe_page_get(
            session, url, headers=DEFAULT_HEADERS, timeout=timeout
        )
    except _UnsafeGateRedirect as exc:
        LOGGER.debug("Refusing redirect from %s: %s", _url_for_log(url), exc)
        return LinkPageInspection()
    except requests.RequestException as exc:
        LOGGER.debug(
            "Could not read %s (%s)", _url_for_log(url), type(exc).__name__
        )
        return None
    if response.status_code >= 400:
        return LinkPageInspection()

    # response.url, not url: a hub reached through a redirect wraps its shops in
    # links relative to where it landed, not where it was asked for.
    hub_host = host_of(landed)
    hypeddit = inspect_hypeddit_html(landed, response.text) if _is_hypeddit_url(landed) else None
    found: list[tuple[str, str]] = list(hypeddit.shops) if hypeddit else []
    wrapped: list[tuple[str, str]] = []
    seen: set[str] = {
        *(pair[0] for pair in found),
        *((hypeddit.nested_gates if hypeddit else ())),
    }

    for anchor in BeautifulSoup(response.text, "html.parser").select("a[href]"):
        href = normalize_link(landed, (anchor.get("href") or "").strip())
        if not href or href in seen:
            continue
        seen.add(href)
        text = anchor.get_text(" ", strip=True) or host_of(href)
        if store_for_url(href) in SHOP_CATEGORIES:
            found.append((href, text))
        elif host_of(href) == hub_host:
            wrapped.append((href, text))

    for href, text in wrapped[:HUB_REDIRECT_LIMIT]:
        try:
            # stream=True so a link that turns out to be a page rather than a
            # redirect costs the headers and nothing else.
            hop = session.get(
                href,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            location = hop.headers.get("Location", "")
            hop.close()
        except requests.RequestException:
            continue
        location = normalize_link(href, location)
        if (
            location
            and is_fetchable(location)
            and store_for_url(location) in SHOP_CATEGORIES
        ):
            found.append((location, text))

    # A shop linked both directly and through a wrapper is still one shop.
    unique: dict[str, tuple[str, str]] = {}
    for pair in found:
        unique.setdefault(pair[0], pair)
    if hypeddit:
        # Unknown means protocol drift, not a proven empty wrapper. Keep it so
        # the caller has a diagnostic/manual fallback instead of losing a link.
        keep_original = hypeddit.kind in {
            "gate",
            "hybrid",
            "challenge",
            "unknown",
        } or (
            _offers_a_download(response.text) and not hypeddit.nested_gates
        )
        recognized = hypeddit.kind != "unknown"
    else:
        keep_original = _offers_a_download(response.text)
        recognized = bool(unique) or keep_original
    gate_urls = hypeddit.nested_gates if hypeddit else ()
    return LinkPageInspection(
        tuple(unique.values()), gate_urls, keep_original, recognized
    )


def can_resolve(url: str) -> bool:
    """True when ``resolve_gate_download_url`` has a resolver for this host."""

    try:
        host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return host in RESOLVABLE_HOSTS


def resolve_gate_download_url(
    url: str, session: requests.Session, timeout: float = 10.0, config: Any | None = None
) -> str | None:
    """Inspect and resolve direct download URL from supported gate providers and cloud storage."""
    # Was ``startswith("http")``, which also accepted ``httpfoo://``. is_fetchable
    # parses the URL instead of guessing at its prefix, and refuses an address on
    # the user's own network - every resolver below posts to whatever it is given.
    if not is_fetchable(url):
        return None

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in {"hypeddit.com", "www.hypeddit.com", "hypd.it", "www.hypd.it"}:
        return resolve_hypeddit_download_url(url, session, timeout=timeout, config=config)
    if host in {"droploud.com", "www.droploud.com"}:
        return resolve_droploud_download_url(url, session, timeout=timeout)
    if host in {"gaterush.me", "www.gaterush.me"}:
        return resolve_gaterush_download_url(url, session, timeout=timeout, config=config)
    if host in {"toneden.io", "www.toneden.io"}:
        return resolve_toneden_download_url(url, session, timeout=timeout)
    if host in {"mediafire.com", "www.mediafire.com"}:
        return resolve_mediafire_download_url(url, session, timeout=timeout)
    if host in {"dropbox.com", "www.dropbox.com", "dl.dropboxusercontent.com"}:
        return resolve_dropbox_download_url(url)
    if host in {"drive.google.com", "docs.google.com"}:
        return resolve_google_drive_download_url(url)

    # Direct audio file links (S3, R2, CDN, raw audio files)
    lower_url = url.lower()
    if any(lower_url.endswith(ext) or f"{ext}?" in lower_url for ext in (".mp3", ".wav", ".flac", ".zip", ".aiff")):
        return url

    return None

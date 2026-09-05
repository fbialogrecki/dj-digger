"""Bypass and resolver module for download gates (Hypeddit, ToneDen, etc.).

Extracts direct file download URLs from gate pages without requiring manual
social media login steps.
"""

import html
import json
import logging
import re
import threading
import urllib.parse
from collections.abc import Callable
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..config import AppConfig
from ..gate_models import (
    GateAuthenticationRequired,
    GateCaptchaRequired,
    GateManualActionRequired,
    GateProfileRequired,
    GateProtocolChanged,
    GateRejected,
    GateSocialActionsDisabled,
    GateUnavailable,
    HypedditInspection,
    HypedditManifest,
)
from ..http import REQUEST_HEADERS, UnsafeRedirect, follow_redirects, is_fetchable
from ..links import SHOP_CATEGORIES, host_of, is_hypeddit_url, redact_url, store_for_url

LOGGER = logging.getLogger(__name__)


def config_or_default(config: Any | None) -> Any:
    """The caller's own config when it passed one, else the one on disk.

    Callers pass their own so that editing your name and email in Settings
    reaches the gate resolvers rather than updating a second copy nobody reads.
    """

    return AppConfig() if config is None else config


def check_gate_action(config, cancel=None, *, social=False, profile=False):
    """Re-read permission immediately before the next external action."""
    from ..models import check_cancelled
    check_cancelled(cancel)
    if social and not getattr(config, "gate_social_actions", True):
        raise GateSocialActionsDisabled("Gate social actions were disabled")
    if profile and not config.has_real_email():
        raise GateProfileRequired("The gate profile no longer has an approved email")


def _identity_for(config: Any | None) -> Any:
    """The profile a gate form gets filled in with.

    Warns when it is still the placeholder: these resolvers post a name and an
    email to somebody else's server, and the artist on the other end deserves a
    contact that exists rather than a reserved .invalid address.
    """

    config = config_or_default(config)
    if not config.has_real_email():
        LOGGER.warning(
            "The gate profile still uses a placeholder address; a gate that "
            "requires email will pause before submitting it."
        )
    return config

# What a gate or a browser may hand over as a file. A direct link with one of
# these needs no resolver; a browser download without one is kept as .mp3.
DOWNLOAD_SUFFIXES = (".mp3", ".wav", ".flac", ".aiff", ".aif", ".zip")

TONEDEN_RE = re.compile(r'https?://(?:www\.)?toneden\.io/([^/]+)/post/([a-zA-Z0-9_-]+)')


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


def _log_gate_failure(provider: str, url: str, exc: Exception) -> None:
    LOGGER.debug(
        "%s gate resolution failed for %s (%s)",
        provider,
        redact_url(url),
        type(exc).__name__,
    )


# Steps the desktop flow reports as done without calling the provider. "sp" is
# here too: Hypeddit completes its Spotify step through its own OAuth app and
# server session, so nothing this program could do with a user's own Spotify
# login would ever reach the gate - the integration that tried was removed.
CLICK_THROUGH_STEPS = frozenset(
    {"sc", "yt", "ig", "tw", "fb", "tk", "bc", "mc", "dn", "fbmsgr", "sp"}
)
PROVIDER_OAUTH_STEPS = {"dz": "Deezer", "ap": "Apple Music", "th": "Threads"}
# A step the page offers as an alternative to the social ones: a direct
# download. Chosen first whenever a group offers it.
DIRECT_STEP = "dw"
# What each kind of step costs the user when the page lets us pick between
# alternatives. Lower wins; anything above the OAuth line needs a browser.
_EMAIL_STEP_COST = 2
_OAUTH_STEP_COST = 9
# How many unlock flows may talk to one Hypeddit host at once. A politeness
# limit, not a correctness one: every flow owns its own session. It used to be
# a global reentrant lock, which serialised all four download workers behind
# whichever one was waiting on a slow page.
HYPEDDIT_FLOWS_PER_HOST = 2
_HOST_FLOW_LIMITS: dict[str, threading.BoundedSemaphore] = {}
_HOST_FLOW_LIMITS_LOCK = threading.Lock()
# How deep a chain of gates-inside-gates is followed before calling it a cycle.
MAX_NESTED_GATES = 5


# The TUI still reaches for this name; links.is_hypeddit_url is the one home.
_is_hypeddit_url = is_hypeddit_url


def _cancelled(cancel: Any) -> bool:
    return cancel is not None and cancel.is_set()


def _input_fields(soup: BeautifulSoup) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for tag in soup.find_all("input"):
        name = str(tag.get("name") or tag.get("id") or "")
        if name:
            found.setdefault(name, []).append(str(tag.get("value") or ""))
    return {name: tuple(values) for name, values in found.items()}


def _first(fields: dict[str, tuple[str, ...]], name: str, default: str = "") -> str:
    return next(iter(fields.get(name, ())), default)


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


def _page_anchors(landed: str, soup: BeautifulSoup, seen: set[str]):
    """Every absolute link on the page with its label, each address once.

    ``seen`` is shared with the caller so links it has already accounted for
    are not offered twice.
    """

    for anchor in soup.select("a[href]"):
        href = urllib.parse.urljoin(landed, str(anchor.get("href") or "").strip())
        if not href or href in seen:
            continue
        seen.add(href)
        yield href, anchor.get_text(" ", strip=True) or host_of(href)


def _page_links(
    landed: str, soup: BeautifulSoup
) -> tuple[list[tuple[str, str]], list[str]]:
    """Shops and nested /track gates a Hypeddit page links to."""

    shops: list[tuple[str, str]] = []
    nested: list[str] = []
    for href, label in _page_anchors(landed, soup, set()):
        if store_for_url(href) in SHOP_CATEGORIES:
            shops.append((href, label))
        elif is_hypeddit_url(href) and "/track/" in urllib.parse.urlparse(href).path:
            if href.rstrip("/") != landed.rstrip("/"):
                nested.append(href)
    return shops, nested


def _parse_manifest(text: str, soup: BeautifulSoup) -> HypedditManifest | None:
    """The desktop download form's short-lived fields, when the page has one."""

    fields = _input_fields(soup)
    raw_steps = _first(fields, "nwSteps")
    download_file_id = _first(fields, "current_download_file_listner")
    # Smartlinks also carry fan_gate_id for click telemetry. It is not evidence
    # of a download form: ky9i8z has one beside its Beatport/Bandcamp buttons and
    # points at a separate /track gate. An explicit step list or download-file
    # field is what distinguishes an actual desktop gate manifest.
    if not raw_steps and not download_file_id:
        return None

    steps = tuple(step.strip().lower() for step in raw_steps.split(",") if step.strip())
    step_groups = tuple(
        group
        for group in (
            tuple(step.strip().lower() for step in raw.split("|") if step.strip())
            for raw in _first(fields, "steps_select").split(",")
        )
        if group
    )

    csrf_tag = soup.find("meta", attrs={"name": "csrf-token"})
    csrf = str(csrf_tag.get("content") or "") if csrf_tag else ""
    external_id = ""
    gate_data = re.search(r"var\s+jsonGateData\s*=\s*({.*?});", text, re.DOTALL)
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
    return HypedditManifest(
        csrf=csrf,
        file_id=(
            download_file_id
            or _first(fields, "fangate_id")
            or _first(fields, "fan_gate_id")
        ),
        gvt=_first(fields, "gvt"),
        gvf=_first(fields, "gvf", "0"),
        wrndk=_first(fields, "wrndk"),
        external_id=external_id,
        steps=steps,
        fields=additional,
        step_groups=step_groups,
        sc_comment_required=_first(fields, "comment_sc") == "1",
        yt_comment_required=_first(fields, "comment_yt") == "1",
        is_skippable=_first(fields, "is_skippable", "0"),
        is_mobile=_first(fields, "is_mobile"),
        hypesource=hypesource or _first(fields, "hypesource"),
        adcode=adcode or _first(fields, "adcode"),
    )


def _classify(
    text: str,
    soup: BeautifulSoup,
    manifest: HypedditManifest | None,
    links: bool,
) -> str:
    """What kind of Hypeddit document this is: challenge, gate, hybrid, hub or unknown."""

    smartlink = bool(
        re.search(r"\bisSmartLink\s*=\s*['\"]1['\"]", text, re.IGNORECASE)
    )
    if _has_active_captcha(soup):
        return "challenge"
    if manifest:
        return "hybrid" if links else "gate"
    if smartlink or links:
        return "hub"
    return "unknown"


def _inspect_hypeddit(url: str, text: str, soup: BeautifulSoup) -> HypedditInspection:
    shops, nested = _page_links(url, soup)
    manifest = _parse_manifest(text, soup)
    return HypedditInspection(
        kind=_classify(text, soup, manifest, bool(shops or nested)),
        shops=tuple(shops),
        nested_gates=tuple(nested),
        # Hypeddit pages contain unrelated recommendations and preview assets.
        # Only /gate/download/ul is scoped to the current fan_gate_id, so the
        # landing document is never trusted as a file source.
        manifest=manifest,
    )


def inspect_hypeddit_html(url: str, text: str) -> HypedditInspection:
    """Classify a Hypeddit document and parse its short-lived desktop manifest."""

    text = text or ""
    return _inspect_hypeddit(url, text, BeautifulSoup(text, "html.parser"))


def resolve_hypeddit_download_url(
    url: str,
    session: requests.Session,
    timeout: float = 10.0,
    config: Any | None = None,
    _visited: frozenset[str] = frozenset(),
    cancel=None,
) -> str | None:
    """Resolve the current desktop Hypeddit flow without faking provider writes."""

    if not is_hypeddit_url(url) or not is_fetchable(url):
        return None
    canonical = url.rstrip("/")
    if canonical in _visited or len(_visited) >= MAX_NESTED_GATES:
        raise GateProtocolChanged("Hypeddit nested-gate cycle or redirect limit reached")
    visited = _visited | {canonical}
    config = _identity_for(config)

    # The page fetch and the unlock POST run under the host's limit; a nested
    # gate is followed after it is released, so recursion cannot deadlock.
    with _host_flow_limit(url):
        inspection = _fetch_hypeddit_inspection(url, session, timeout)
        if inspection.manifest is not None:
            return _unlock_hypeddit(inspection.manifest, url, session, timeout, config, cancel)
    if inspection.kind == "challenge":
        raise GateCaptchaRequired("Hypeddit CAPTCHA requires browser completion")
    for nested in inspection.nested_gates:
        resolved = resolve_hypeddit_download_url(
            nested,
            session,
            timeout=timeout,
            config=config,
            _visited=frozenset(visited),
            cancel=cancel,
        )
        if resolved:
            return resolved
    if inspection.kind == "hub":
        return None
    raise GateProtocolChanged("Hypeddit page has no supported download manifest")


def _host_flow_limit(url: str) -> threading.BoundedSemaphore:
    host = host_of(url)
    with _HOST_FLOW_LIMITS_LOCK:
        limit = _HOST_FLOW_LIMITS.get(host)
        if limit is None:
            limit = _HOST_FLOW_LIMITS[host] = threading.BoundedSemaphore(HYPEDDIT_FLOWS_PER_HOST)
    return limit


def _fetch_hypeddit_inspection(
    url: str, session: requests.Session, timeout: float
) -> HypedditInspection:
    headers = {**REQUEST_HEADERS, "Referer": url}
    try:
        response, landed = follow_redirects(session, url, headers=headers, timeout=timeout)
    except UnsafeRedirect as exc:
        raise GateProtocolChanged(str(exc)) from exc
    except requests.RequestException as exc:
        raise GateUnavailable("Hypeddit page could not be reached") from exc
    if response.status_code != 200:
        raise GateUnavailable(f"Hypeddit page returned HTTP {response.status_code}")
    if not is_hypeddit_url(landed):
        raise GateProtocolChanged("Hypeddit redirected outside its canonical hosts")
    inspection = inspect_hypeddit_html(landed, response.text)
    if inspection.kind == "challenge" and inspection.manifest is not None:
        raise GateCaptchaRequired("Hypeddit CAPTCHA requires browser completion")
    return inspection


def _unlock_hypeddit(
    manifest: HypedditManifest,
    url: str,
    session: requests.Session,
    timeout: float,
    config: Any,
    cancel=None,
) -> str:
    headers = {**REQUEST_HEADERS, "Referer": url}
    if not manifest.file_id or not manifest.steps:
        raise GateProtocolChanged("Hypeddit gate manifest is incomplete")
    steps = _select_steps(manifest, config)
    if "email" in steps and not config.has_real_email():
        raise GateProfileRequired(
            "Hypeddit requires a real email; set it in Settings before downloading"
        )

    social_steps = [step for step in steps if step not in {"email", DIRECT_STEP}]
    social = bool(getattr(config, "gate_social_actions", True))
    if social_steps and not social:
        raise GateSocialActionsDisabled(
            "This Hypeddit gate requires social steps, but they are disabled"
        )

    skipped = _complete_social_steps(manifest, social_steps)
    return _post_download(
        session, manifest, config, skipped, social, headers, url, timeout, cancel
    )


def _step_cost(step: str, config: Any) -> int:
    if step in CLICK_THROUGH_STEPS:
        return 1
    if step == "email":
        return _EMAIL_STEP_COST if config.has_real_email() else _OAUTH_STEP_COST
    if step in PROVIDER_OAUTH_STEPS:
        return _OAUTH_STEP_COST
    return 0 if step == DIRECT_STEP else _OAUTH_STEP_COST + 1


def _select_steps(manifest: HypedditManifest, config: Any) -> list[str]:
    """The steps this attempt will report, picking the cheapest of each alternative.

    A page with ``steps_select`` lets the fan choose within each group - a
    direct download over a follow, a follow over a provider login. Without
    groups the declared step list stands as it is.
    """

    if not manifest.step_groups:
        return list(manifest.steps)
    chosen: list[str] = []
    for group in manifest.step_groups:
        best = min(group, key=lambda step: (_step_cost(step, config), group.index(step)))
        if best not in chosen:
            chosen.append(best)
    return chosen


def _complete_social_steps(manifest: HypedditManifest, social_steps: list[str]) -> list[str]:
    """Which declared steps can be reported as skipped without faking provider writes."""

    skipped: list[str] = []
    for step in social_steps:
        if step in CLICK_THROUGH_STEPS:
            skipped.append(step)
        elif step in PROVIDER_OAUTH_STEPS:
            raise GateAuthenticationRequired(
                f"Hypeddit requires {PROVIDER_OAUTH_STEPS[step]} authentication in a browser"
            )
        else:
            raise GateManualActionRequired(
                f"Hypeddit step {step!r} requires browser completion"
            )
    return skipped


def _unlock_payload(
    manifest: HypedditManifest, config: Any, skipped: list[str], social: bool
) -> dict[str, Any]:
    """The /gate/download/ul form, exactly as the desktop web flow sends it."""

    comment = config.random_comment() if social else ""
    # The fixed values below ("time": 0, "page": "nonsingle" - not a typo,
    # "download_visit": "true") mirror what the desktop web flow sends verbatim;
    # the endpoint rejects requests that deviate from them.
    return {
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
        "skip_gate_steps[]": skipped,
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


def _ping_telemetry(
    session: requests.Session,
    manifest: HypedditManifest,
    headers: dict[str, str],
    url: str,
    timeout: float,
) -> None:
    """The page's own visit ping. Best effort: it never blocks the download."""

    if not manifest.gvt:
        return
    try:
        session.post(
            "https://hypeddit.com/gate/ge",
            data={"vt": manifest.gvt, "uid": manifest.file_id},
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException:
        LOGGER.debug("Hypeddit telemetry failed for %s", redact_url(url))


def _post_download(
    session: requests.Session,
    manifest: HypedditManifest,
    config: Any,
    skipped: list[str],
    social: bool,
    headers: dict[str, str],
    url: str,
    timeout: float,
    cancel=None,
) -> str:
    """The wire protocol: telemetry ping, then the /gate/download/ul unlock.

    ``skipped`` mirrors the page's own skipper buttons: those steps go into
    ``skip_gate_steps[]``. The page sends ``is_skippable=1`` alongside when a
    fan skips, so a refusal of the first attempt (which carries the manifest's
    own value, as always) is retried exactly once that way before it counts
    as a rejection. Neither attempt writes anything at a provider.
    """

    ajax_headers = {
        **headers,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if manifest.csrf:
        ajax_headers["X-CSRF-TOKEN"] = manifest.csrf

    def current_payload():
        check_gate_action(config, cancel, social=social,
                          profile="email" in _select_steps(manifest, config))
        return _unlock_payload(manifest, config, skipped, social)

    payload = current_payload()
    try:
        _ping_telemetry(session, manifest, ajax_headers, url, timeout)
        payload = current_payload()
        result = _post_unlock(session, payload, ajax_headers, timeout)
        if _refused(result) and skipped and payload["is_skippable"] != "1":
            LOGGER.debug("Hypeddit refused %s; retrying as a skipped gate", redact_url(url))
            result = _post_unlock(session, {**current_payload(), "is_skippable": "1"}, ajax_headers, timeout)
    except requests.RequestException as exc:
        raise GateRejected("Hypeddit download request failed") from exc
    if isinstance(result, dict) and _reply_requests_captcha(result):
        raise GateCaptchaRequired("Hypeddit CAPTCHA requires browser completion")
    if _refused(result):
        raise GateRejected("Hypeddit did not unlock the download")
    cleaned = _clean_url(result.get("URL") or result.get("url"))
    if not cleaned or not is_fetchable(cleaned):
        raise GateProtocolChanged("Hypeddit returned an unsafe download URL")
    return cleaned


def _post_unlock(
    session: requests.Session, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> Any:
    download = session.post(
        "https://hypeddit.com/gate/download/ul",
        data=payload,
        headers=headers,
        timeout=timeout,
    )
    if download.status_code != 200:
        raise GateRejected(f"Hypeddit download returned HTTP {download.status_code}")
    try:
        return download.json()
    except ValueError as exc:
        raise GateProtocolChanged("Hypeddit returned an unreadable download reply") from exc


def _refused(result: Any) -> bool:
    if isinstance(result, dict) and _reply_requests_captcha(result):
        return False
    return not isinstance(result, dict) or not result.get("download_status")


def resolve_toneden_download_url(
    url: str, session: requests.Session, timeout: float = 10.0, config: Any | None = None
) -> str | None:
    """Resolve direct audio download URL from ToneDen fan gate link."""
    match = TONEDEN_RE.search(url)
    if not match:
        return None

    headers = {**REQUEST_HEADERS, "Referer": url}

    try:
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            text = resp.text

            # 1. Extract JSON state from page HTML
            json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});</script>', text, re.DOTALL)
            if json_match:
                for key_name in ("download_url", "free_download_location", "s3_url", "file_url"):
                    dl_match = re.search(rf'"{key_name}"\s*:\s*"([^"]+)"', json_match.group(1))
                    if dl_match:
                        cleaned = _clean_url(dl_match.group(1))
                        if cleaned:
                            return cleaned

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
        _log_gate_failure("ToneDen", url, exc)

    return None


DROPLOUD_RE = re.compile(
    r"https?://(?:www\.)?droploud\.com/(?:track|gate)/([a-f0-9\-]{36}|[a-zA-Z0-9_\-]+)", re.I
)


def resolve_droploud_download_url(
    url: str, session: requests.Session, timeout: float = 10.0, config: Any | None = None
) -> str | None:
    """Resolve direct audio stream/download URL from Droploud track gate."""
    match = DROPLOUD_RE.search(url)
    if not match:
        return None
    track_id = match.group(1)
    api_url = f"https://api.droploud.com/api/tracks/{track_id}"
    try:
        resp = session.get(api_url, headers=REQUEST_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("stream_url"):
                stream_path = data["stream_url"]
                if stream_path.startswith(("http://", "https://")):
                    return stream_path
                return f"https://api.droploud.com{stream_path}"
    except requests.RequestException as exc:
        _log_gate_failure("Droploud", url, exc)
    return None


GATERUSH_RE = re.compile(r"https?://(?:www\.)?gaterush\.me/([a-zA-Z0-9_-]+)", re.I)


def resolve_gaterush_download_url(
    url: str, session: requests.Session, timeout: float = 10.0, config: Any | None = None, cancel=None
) -> str | None:
    """Resolve direct audio download URL from GateRush fan gate link."""
    config = _identity_for(config)
    if not config.has_real_email():
        raise GateProfileRequired(
            "GateRush requires a real email; set it in Settings before downloading"
        )

    match = GATERUSH_RE.search(url)
    if not match:
        return None
    slug = match.group(1)
    try:
        headers = {**REQUEST_HEADERS, "Referer": url}
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            text = resp.text
            csrf_m = re.search(r'name="_csrf"\s+value="([^"]+)"', text) or re.search(r'csrf-token.*content="([^"]+)"', text)
            csrf = csrf_m.group(1) if csrf_m else ""
            ajax_headers = {**headers, "X-CSRF-Token": csrf, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}

            # The email is what the download is for; the comment is posted in
            # your name, so it goes only when you have said it may.
            if getattr(config, "gate_social_actions", True):
                check_gate_action(config, cancel, social=True)
                comment = config.random_comment()
                session.post(f"https://gaterush.me/save-comment/{slug}", data={"commentText": comment, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)
            check_gate_action(config, cancel, profile=True)
            email = config.user_email
            session.post(f"https://gaterush.me/save-email/{slug}", data={"email": email, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)

            steps_m = re.findall(r'id["\']:\s*["\']([^"\']+)["\']', text)
            # The scraped ids are unioned with the six stock providers because
            # the regex misses steps rendered client-side; GateRush ignores a
            # stepId the gate did not declare, so over-posting is harmless.
            for st in set(steps_m) | {"instagram", "spotify", "youtube", "soundcloud", "email", "tiktok"}:
                check_gate_action(config, cancel, profile=True)
                session.post(f"https://gaterush.me/gate-step/{slug}", data={"stepId": st, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)

            dl_resp = session.get(f"https://gaterush.me/download/{slug}", headers=headers, allow_redirects=False, timeout=timeout)
            if dl_resp.status_code in (301, 302) and dl_resp.headers.get("Location"):
                return dl_resp.headers["Location"]
            elif dl_resp.status_code == 200:
                return f"https://gaterush.me/download/{slug}"
    except requests.RequestException as exc:
        _log_gate_failure("GateRush", url, exc)
    return None


def resolve_mediafire_download_url(
    url: str, session: requests.Session, timeout: float = 10.0, config: Any | None = None
) -> str | None:
    """Extract direct download link from MediaFire page."""
    try:
        resp = session.get(url, headers=REQUEST_HEADERS, timeout=timeout)
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


def resolve_dropbox_download_url(
    url: str, session: Any = None, timeout: float = 10.0, config: Any | None = None
) -> str:
    """Convert Dropbox link to direct download link."""
    return url.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("dl=0", "dl=1")


def resolve_google_drive_download_url(
    url: str, session: Any = None, timeout: float = 10.0, config: Any | None = None
) -> str:
    """Convert Google Drive view link to direct download link."""
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', url) or re.search(r'id=([a-zA-Z0-9_-]+)', url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url


# One table drives host routing. Every resolver takes the same
# (url, session, timeout, config) and ignores what it has no use for - the
# dropbox/gdrive ones are pure URL rewrites. The exact host spellings matter:
# www. variants are listed deliberately, and hosts without one
# (drive.google.com) must NOT gain one via normalisation.
_RESOLVERS: dict[str, Callable[..., str | None]] = {
    "hypeddit.com": resolve_hypeddit_download_url,
    "www.hypeddit.com": resolve_hypeddit_download_url,
    "hypd.it": resolve_hypeddit_download_url,
    "www.hypd.it": resolve_hypeddit_download_url,
    "droploud.com": resolve_droploud_download_url,
    "www.droploud.com": resolve_droploud_download_url,
    "gaterush.me": resolve_gaterush_download_url,
    "www.gaterush.me": resolve_gaterush_download_url,
    "toneden.io": resolve_toneden_download_url,
    "www.toneden.io": resolve_toneden_download_url,
    "mediafire.com": resolve_mediafire_download_url,
    "www.mediafire.com": resolve_mediafire_download_url,
    "dropbox.com": resolve_dropbox_download_url,
    "www.dropbox.com": resolve_dropbox_download_url,
    "dl.dropboxusercontent.com": resolve_dropbox_download_url,
    "drive.google.com": resolve_google_drive_download_url,
    "docs.google.com": resolve_google_drive_download_url,
}

# Every host the routing knows how to unwrap. Derived from the table so a
# caller picking a candidate link can never drift from the routing itself.
RESOLVABLE_HOSTS = tuple(_RESOLVERS)


def can_resolve(url: str) -> bool:
    """True when ``resolve_gate_download_url`` has a resolver for this host."""

    try:
        host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return host in RESOLVABLE_HOSTS


def resolve_gate_download_url(
    url: str, session: requests.Session, timeout: float = 10.0, config: Any | None = None, cancel=None
) -> str | None:
    """Inspect and resolve direct download URL from supported gate providers and cloud storage."""
    # Was ``startswith("http")``, which also accepted ``httpfoo://``. is_fetchable
    # parses the URL instead of guessing at its prefix, and refuses an address on
    # the user's own network - every resolver below posts to whatever it is given.
    if not is_fetchable(url):
        return None

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    resolver = _RESOLVERS.get(host)
    if resolver in (resolve_hypeddit_download_url, resolve_gaterush_download_url):
        return resolver(url, session, timeout, config, cancel=cancel)
    if resolver is not None:
        return resolver(url, session, timeout, config)

    # Direct audio file links (S3, R2, CDN, raw audio files). urlparse strips
    # the query and fragment, so "...mp3?sig=..." still matches. Deliberately
    # not part of RESOLVABLE_HOSTS - this is a shape check, not a host route.
    if urllib.parse.urlparse(url).path.lower().endswith(DOWNLOAD_SUFFIXES):
        return url

    return None

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
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from .browser import REQUEST_HEADERS, UnsafeRedirect, follow_redirects, is_fetchable
from .config import DEFAULT_NAME, AppConfig
from .links import SHOP_CATEGORIES, host_of, is_hypeddit_url, redact_url, store_for_url

LOGGER = logging.getLogger(__name__)


class GateError(RuntimeError):
    """A recognised gate could not be completed safely."""


class GateProfileRequired(GateError):
    """The gate would submit contact data that the user has not configured."""


class GateSocialActionsDisabled(GateError):
    """The user declined the social steps declared by this gate."""


class GateAuthenticationRequired(GateError):
    """A provider-owned OAuth flow must be completed in a browser."""


class GateCaptchaRequired(GateError):
    """The gate presented an interactive anti-bot challenge."""


class GateManualActionRequired(GateError):
    """The gate declared a step whose semantics are not known."""


class GateProtocolChanged(GateError):
    """The provider answered, but no longer speaks the supported protocol."""


class GateRejected(GateError):
    """The provider understood the request but refused to unlock the file."""


class GateDownloadError(GateError):
    """A resolved gate file could not be transferred or validated."""


class GateUnavailable(GateError):
    """The provider or the requested gate is unavailable."""


# Failures the HTTP flow cannot get past, but a person in the private browser
# can: a provider login, a CAPTCHA, an unknown step, a changed protocol, a
# gate that refused the unlock, or social steps the user has disabled here.
BROWSER_REQUIRED_ERRORS = (
    GateAuthenticationRequired,
    GateCaptchaRequired,
    GateManualActionRequired,
    GateProtocolChanged,
    GateRejected,
    GateSocialActionsDisabled,
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
    # ``steps_select``: groups of alternatives the page lets the fan choose
    # between, e.g. ("dw",), ("sc",), ("sp","dz"). Empty when the page has none.
    step_groups: tuple[tuple[str, ...], ...] = ()
    sc_comment_required: bool = False
    yt_comment_required: bool = False
    is_skippable: str = "0"
    is_mobile: str = ""
    hypesource: str = ""
    adcode: str = ""


@dataclass(frozen=True)
class HypedditInspection:
    """What one Hypeddit HTML document represents, without executing it."""

    kind: str
    shops: tuple[tuple[str, str], ...] = ()
    nested_gates: tuple[str, ...] = ()
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


def config_or_default(config: Any | None) -> Any:
    """The caller's own config when it passed one, else the one on disk.

    Callers pass their own so that editing your name and email in Settings
    reaches the gate resolvers rather than updating a second copy nobody reads.
    """

    return AppConfig() if config is None else config


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
            return _unlock_hypeddit(inspection.manifest, url, session, timeout, config)
    if inspection.kind == "challenge":
        raise GateCaptchaRequired("Hypeddit CAPTCHA requires browser completion")
    for nested in inspection.nested_gates:
        resolved = resolve_hypeddit_download_url(
            nested,
            session,
            timeout=timeout,
            config=config,
            _visited=frozenset(visited),
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
        session, manifest, config, skipped, social, headers, url, timeout
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

    payload = _unlock_payload(manifest, config, skipped, social)
    try:
        _ping_telemetry(session, manifest, ajax_headers, url, timeout)
        result = _post_unlock(session, payload, ajax_headers, timeout)
        if _refused(result) and skipped and payload["is_skippable"] != "1":
            LOGGER.debug("Hypeddit refused %s; retrying as a skipped gate", redact_url(url))
            result = _post_unlock(session, {**payload, "is_skippable": "1"}, ajax_headers, timeout)
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


# The desktop gate's own controls, read off hypeddit.com/track/aaiohi on
# 2026-09-02. The sidebar's Download reveals a carousel of step slides, one
# current at a time; a slide's kind is its first class (sc, sp, ig, email,
# dw ...), and the slide moves left once its step is done. Everything that
# touches these degrades to the passive watcher when they are not where they
# were.
GATE_START_BUTTON = "#downloadProcess"
GATE_CURRENT_SLIDE = ".fangate-slider-content:not(.move-left):not(.upcomming-slide)"
# A follow/like/repost link the slide wants ticked before its Next appears.
# Each opens the provider's page in a popup, which is closed unread.
GATE_PENDING_ACTION = "a.undone:visible"
GATE_NEXT_BUTTON = ".button-next:visible"
# A provider login's Connect: the gate's own OAuth popup, which comes back on
# its own when the profile is signed in there.
GATE_CONNECT_BUTTON = "a.hype-btn-social:visible"
GATE_EMAIL_INPUT = "#email_address"
# Some gates also want a name on the email slide: hypeddit.com/js/unlimited/
# verify-email-ul.js refuses to move on while an empty #email_name is present
# ("Please enter your name."), checked 2026-09-02.
GATE_NAME_INPUT = "#email_name"
GATE_EMAIL_SUBMIT = ".email_to_downloads"
GATE_CAPTCHA = "#gatePreviewCaptcha"
HYPEDDIT_DOWNLOAD_BUTTON = "#gateDownloadButton, .hype-btn-download, #download-btn"
# Steps whose slide opens a provider login rather than a page to look at. "sp"
# is a click-through for the HTTP flow, but in the browser the gate's Spotify
# slide is a Connect button.
GATE_CONNECT_STEPS = {"sp": "Spotify", **PROVIDER_OAUTH_STEPS}
STEP_KINDS = frozenset({*CLICK_THROUGH_STEPS, *GATE_CONNECT_STEPS, "email", DIRECT_STEP})
MAX_GATE_STEPS = 12
PROVIDER_WAIT_SECONDS = 300
# How long the hidden browser gives a provider popup to come back by itself
# before the row is handed to a window with a person in front of it.
UNATTENDED_PROVIDER_SECONDS = 20
# How long a finished step gets to hand over to the next slide.
STEP_SETTLE_SECONDS = 15
# A popup back on Hypeddit has done its work; a person would close it about now.
CALLBACK_LINGER_SECONDS = 2.0
# How long a Connect gets to show its popup: the page opens it in the click,
# but it reaches the client a moment later.
POPUP_GRACE_SECONDS = 3.0
# The slide's links are clicked without Playwright's hit-target check: on the
# live gate it reports the links' own container as intercepting the pointer,
# although the link is what sits at that point. The click still lands as a
# real mouse event where the link is.
_CLICK = {"timeout": 15_000, "force": True}
StatusCallback = Callable[[str], None]
# The step driver's own way of saying the person gave up, told apart from a
# gate step it could not finish.
CANCELLED = "cancelled"
# Patched by tests that want the waits to pass without sleeping.
_now = time.monotonic


class _NeedsPerson(GateManualActionRequired):
    """A step the driver cannot finish alone: a login, a CAPTCHA, an unknown page."""


@dataclass(frozen=True)
class _Slide:
    """The current step slide, with what it was when it was looked at.

    A Playwright locator resolves afresh on every use, so the kind and group
    are read once here; after the step the page's current slide is compared
    against ``group`` to see that it moved on.
    """

    locator: Any
    kind: str
    group: str


def _drive_gate_steps(
    context: Any,
    page: Any,
    cancel: Any,
    status: StatusCallback | None,
    *,
    social: bool,
    email: str | None,
    name: str | None,
    attended: bool,
) -> bool:
    """Walk the gate's step slides the way a fan does, then press its Download.

    Only elements on the Hypeddit page are ever clicked. A follow or like
    link opens the provider's page in a popup, which is closed unread; a
    Connect opens the provider's login popup, which is waited out. With
    ``attended`` a person is at the window, so that wait lasts
    ``PROVIDER_WAIT_SECONDS`` and is announced through ``status``; without
    one it lasts ``UNATTENDED_PROVIDER_SECONDS`` before ``_NeedsPerson``
    says who wants the person. False means the page did not look like a gate
    this knows: the caller then just watches for a download, as it always has.
    """

    if not social:
        return False
    try:
        start = page.locator(GATE_START_BUTTON).first
        if start.is_visible():
            start.click(timeout=15_000)
        slide = _current_slide(page)
    except Exception:
        return False
    if slide is None:
        return False
    for _ in range(MAX_GATE_STEPS):
        if _cancelled(cancel):
            raise GateManualActionRequired(CANCELLED)
        kind = slide.kind
        if kind == DIRECT_STEP:
            if not _click_gate_download(page):
                raise _NeedsPerson("the gate's download button is not where it was")
            return True
        if kind in GATE_CONNECT_STEPS:
            _connect_provider(context, page, slide.locator, cancel, status, attended=attended)
        elif kind == "email":
            _share_email(slide.locator, email, name)
        elif kind in CLICK_THROUGH_STEPS:
            _click_through(context, page, slide.locator)
        else:
            raise _NeedsPerson(f"the gate's {kind or 'next'} step is not one this program knows")
        slide = _next_slide(page, slide, cancel)
    raise _NeedsPerson("the gate did not reach its download button")


def _current_slide(page: Any) -> _Slide | None:
    slides = page.locator(GATE_CURRENT_SLIDE)
    if not slides.count():
        return None
    first = slides.first
    classes = str(first.get_attribute("class") or "").split()
    kind = next((name for name in classes if name in STEP_KINDS), "")
    return _Slide(first, kind, str(first.get_attribute("data-group") or ""))


def _step_name(kind: str) -> str:
    return GATE_CONNECT_STEPS.get(kind, kind or "next")


def _next_slide(page: Any, slide: _Slide, cancel: Any) -> _Slide:
    """The slide after ``slide`` once the page has moved on, within STEP_SETTLE_SECONDS."""

    deadline = _now() + STEP_SETTLE_SECONDS
    while _now() < deadline:
        if _cancelled(cancel):
            raise GateManualActionRequired(CANCELLED)
        current = _current_slide(page)
        if current is not None and current.group != slide.group:
            return current
        if slide.kind == "email" and page.locator(GATE_CAPTCHA).first.is_visible():
            raise _NeedsPerson("the gate wants a CAPTCHA solved")
        page.wait_for_timeout(250)
    raise _NeedsPerson(f"the {_step_name(slide.kind)} step did not clear")


def _click_through(context: Any, page: Any, slide: Any) -> None:
    """Tick the slide's follow/like links, closing the pages they open, then Next."""

    actions = slide.locator(GATE_PENDING_ACTION)
    before = list(context.pages)
    for _ in range(MAX_GATE_STEPS):
        if not actions.count():
            break
        actions.first.click(**_CLICK)
        _close_popups(context, page, before, wait=True)
    slide.locator(GATE_NEXT_BUTTON).first.click(**_CLICK)
    _close_popups(context, page, before, wait=False)


def _close_popups(context: Any, page: Any, before: list[Any], *, wait: bool) -> None:
    """Close the pages ``page`` opened since ``before``; with ``wait``, give one
    up to POPUP_GRACE_SECONDS to reach the client first."""

    started = _now()
    while True:
        popups = _popups_of(context, page, before)
        for popup in popups:
            with suppress(Exception):
                popup.close()
        if popups or not wait or _now() - started >= POPUP_GRACE_SECONDS:
            return
        page.wait_for_timeout(250)


def _share_email(slide: Any, email: str | None, name: str | None) -> None:
    if not email:
        raise _NeedsPerson("the gate wants an email address and the profile has none")
    if slide.locator(GATE_NAME_INPUT).count():
        if not name:
            raise _NeedsPerson("the gate wants a name and the profile has none")
        slide.locator(GATE_NAME_INPUT).first.fill(name)
    slide.locator(GATE_EMAIL_INPUT).first.fill(email)
    slide.locator(GATE_EMAIL_SUBMIT).first.click(**_CLICK)


def _connect_provider(
    context: Any,
    page: Any,
    slide: Any,
    cancel: Any,
    status: StatusCallback | None,
    *,
    attended: bool,
) -> None:
    before = list(context.pages)
    slide.locator(GATE_CONNECT_BUTTON).first.click(**_CLICK)
    _wait_for_provider(context, page, before, cancel, status, attended=attended)


def _opener(page: Any) -> Any | None:
    """The page that opened this one. Playwright's ``opener`` is a method."""

    try:
        opener = page.opener
        return opener() if callable(opener) else opener
    except Exception:
        return None


def _popups_of(context: Any, page: Any, before: list[Any]) -> list[Any]:
    return [
        popup
        for popup in context.pages
        if popup not in before and _opener(popup) is page and not _page_closed(popup)
    ]


def _wait_for_provider(
    context: Any,
    page: Any,
    before: list[Any],
    cancel: Any,
    status: StatusCallback | None,
    *,
    attended: bool,
) -> None:
    """Wait until the provider popup (or the tab itself) is back at Hypeddit.

    The callback page tells the gate through the browser's storage the
    moment it loads, so a popup that stays on Hypeddit afterwards has nothing
    left to do and is closed the way a person would close it.
    """

    limit = PROVIDER_WAIT_SECONDS if attended else UNATTENDED_PROVIDER_SECONDS
    started = _now()
    deadline = started + limit
    told = ""
    where = "the provider"
    seen = False
    came_home: dict[int, float] = {}
    while _now() < deadline:
        if _cancelled(cancel):
            raise GateManualActionRequired(CANCELLED)
        popups = _popups_of(context, page, before)
        seen = seen or bool(popups)
        for popup in popups:
            if not is_hypeddit_url(str(popup.url)):
                came_home.pop(id(popup), None)
                continue
            if _now() - came_home.setdefault(id(popup), _now()) >= CALLBACK_LINGER_SECONDS:
                with suppress(Exception):
                    popup.close()
        popups = [popup for popup in popups if not _page_closed(popup)]
        off_host = not is_hypeddit_url(str(page.url))
        if not popups and not off_host and (seen or _now() - started >= POPUP_GRACE_SECONDS):
            return
        where = host_of(str(popups[0].url if popups else page.url)) or "the provider"
        message = f"Complete {where} in the browser window, then return to Hypeddit"
        if attended and status is not None and message != told:
            status(message)
            told = message
        page.wait_for_timeout(250)
    if attended:
        raise GateManualActionRequired("provider step was not completed in time")
    raise _NeedsPerson(f"{where} wants you to sign in")


def _page_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return False


def _click_gate_download(page: Any) -> bool:
    try:
        button = page.locator(HYPEDDIT_DOWNLOAD_BUTTON).first
        if not button.is_visible():
            return False
        button.click(**_CLICK)
        return True
    except Exception as exc:
        LOGGER.debug("Gate download button not clicked: %s", type(exc).__name__)
        return False


def _gate_email(config: Any | None) -> str | None:
    """The address the gate's email slide gets, or None while it is the placeholder."""

    config = config_or_default(config)
    return str(config.user_email) if config.has_real_email() else None


def _gate_name(config: Any | None) -> str | None:
    """The name the gate's email slide gets, or None while it is the placeholder."""

    name = str(config_or_default(config).user_name).strip()
    return name if name and name != DEFAULT_NAME else None


def download_hypeddit_in_browser(
    track: Any,
    url: str,
    directory: Path,
    cancel: Any,
    *,
    social: bool = True,
    status: StatusCallback | None = None,
    config: Any | None = None,
) -> Path:
    """Finish a provider-owned Hypeddit flow in the private browser.

    With ``social`` the gate's own steps are walked and provider windows are
    waited out; without it the page is only watched. A batch of one: the
    file comes back, or the failure the batch recorded is raised.
    """

    result = download_hypeddit_batch_in_browser(
        [(track, url)],
        directory,
        cancel,
        social=social,
        status=status,
        config=config,
        time_limit=PROVIDER_WAIT_SECONDS,
    )
    for _key, path in result.completed:
        return path
    for _key, error in result.failures:
        raise error
    raise GateUnavailable("The browser produced neither a file nor a reason")


def _screen_batch(
    items: list[tuple[Any, str]], cancel: Any
) -> tuple[dict[str, tuple[Any, str]], dict[str, GateError], bool]:
    """Rows worth opening, rows refused before any browser starts, and whether
    the whole batch was cancelled before it began."""

    keyed = {track.key: (track, url) for track, url in items}
    if _cancelled(cancel):
        return (
            {},
            {key: GateManualActionRequired("browser batch cancelled") for key in keyed},
            True,
        )
    failures = {
        key: GateProtocolChanged("Refusing an unsafe Hypeddit browser URL")
        for key, (_track, url) in keyed.items()
        if not is_hypeddit_url(url) or not is_fetchable(url)
    }
    pending = {key: value for key, value in keyed.items() if key not in failures}
    return pending, failures, False


class _TabWatch:
    """Binds each tab's download event back to the row that opened the tab.

    A provider popup belongs to the tab that opened it, so its download is
    that tab's too; a tab nobody here opened is not watched at all. A row the
    hidden browser could not finish is ``deferred`` with the reason, settled
    for that pass and opened again in front of a person.
    """

    def __init__(
        self,
        pending: dict[str, tuple[Any, str]],
        directory: Path,
        cancel: Any,
        failures: dict[str, GateError],
    ) -> None:
        self.pending = pending
        self.directory = directory
        self.cancel = cancel
        self.failures = failures
        self.completed: dict[str, Path] = {}
        self.deferred: dict[str, str] = {}
        self._watched: set[int] = set()
        self._owners: dict[int, str] = {}

    def reset_tabs(self) -> None:
        """Forget the previous context's pages before a new one is opened."""

        self._watched.clear()
        self._owners.clear()

    def settled(self, key: str) -> bool:
        return key in self.completed or key in self.failures or key in self.deferred

    def done(self) -> bool:
        return all(self.settled(key) for key in self.pending)

    def label(self, key: str) -> str:
        track, _url = self.pending[key]
        return str(getattr(track, "label", None) or key)

    def save(self, key: str, download: Any) -> None:
        if self.settled(key) or _cancelled(self.cancel):
            return
        from . import soundcloud

        track, _url = self.pending[key]
        try:
            self.completed[key] = soundcloud.save_browser_download(
                download, track, self.directory
            )
        except Exception as exc:
            self.failures[key] = GateDownloadError(str(exc))

    def watch(self, page: Any, key: str) -> None:
        marker = id(page)
        if marker in self._watched:
            return
        self._watched.add(marker)
        self._owners[marker] = key
        page.on("download", lambda download, owner=key: self.save(owner, download))
        page.on("popup", lambda popup, owner=key: self.watch(popup, owner))

    def watch_popup(self, page: Any) -> None:
        opener = _opener(page)
        key = self._owners.get(id(opener)) if opener is not None else None
        if key is not None:
            self.watch(page, key)

    def open_tabs(self, context: Any) -> list[Any]:
        return [page for page in context.pages if id(page) in self._owners]

    def fail_unsettled(self, reason: str) -> None:
        for key in self.pending:
            if not self.settled(key):
                self.failures[key] = GateManualActionRequired(reason)

    def fail_deferred(self, reason: str) -> None:
        for key in self.deferred:
            self.failures.setdefault(key, GateManualActionRequired(reason))
        self.deferred.clear()

    def result(self, cancelled: bool) -> HypedditBrowserBatchResult:
        return HypedditBrowserBatchResult(
            completed=tuple(self.completed.items()),
            failures=tuple(self.failures.items()),
            cancelled=cancelled,
        )


def _open_gate_tabs(
    context: Any, pending: dict[str, tuple[Any, str]], watch: _TabWatch
) -> list[tuple[str, Any]]:
    """One watched tab per row, every one created before the first navigation.

    A slow page therefore cannot serialize the user's whole queue into one
    short-lived context per track. Navigation stops early when the batch is
    cancelled; ``_await_downloads`` then records the cancellation.
    """

    existing = list(context.pages)
    pages: list[tuple[str, Any]] = []
    for index, key in enumerate(pending):
        page = existing[index] if index < len(existing) else context.new_page()
        watch.watch(page, key)
        pages.append((key, page))

    for key, page in pages:
        if _cancelled(watch.cancel):
            break
        _track, url = pending[key]
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if not is_hypeddit_url(str(page.url)):
                watch.failures[key] = GateProtocolChanged(
                    "Hypeddit redirected outside its canonical hosts"
                )
        except Exception as exc:
            watch.failures[key] = GateUnavailable(
                f"Could not open Hypeddit in Chromium: {exc}"
            )
    return pages


def _drive_tab(
    context: Any,
    key: str,
    page: Any,
    watch: _TabWatch,
    status: StatusCallback | None,
    *,
    social: bool,
    email: str | None,
    name: str | None,
    attended: bool,
) -> bool:
    """Drive one tab's steps. True when the batch was cancelled meanwhile.

    What the driver cannot finish is deferred to a window when nobody is at
    this one, and left to the person - with a word about what stopped - when
    somebody is.
    """

    try:
        if not _drive_gate_steps(
            context, page, watch.cancel, status, social=social, email=email, name=name, attended=attended
        ) and not attended:
            raise _NeedsPerson("the gate page has no step controls this program knows")
    except Exception as exc:
        if str(exc) == CANCELLED:
            return True
        reason = str(exc) or type(exc).__name__
        if attended:
            if status is not None:
                status(f"{watch.label(key)}: {reason}; finish it in the browser window")
        else:
            watch.deferred[key] = reason
    return False


def _await_downloads(
    context: Any,
    pages: list[tuple[str, Any]],
    watch: _TabWatch,
    status: StatusCallback | None,
    *,
    social: bool,
    email: str | None,
    name: str | None,
    attended: bool,
    time_limit: float | None,
) -> bool:
    """Drive each tab's steps, then wait for every row to settle. True if cancelled.

    The wait ends when every row has a file or a failure, when the person
    closes the last watched tab, when the batch is cancelled, or - with a
    ``time_limit`` in seconds, counted from the end of the driving - when it
    runs out.
    """

    cancel = watch.cancel
    cancelled = False
    for key, page in pages:
        if watch.settled(key):
            continue
        if _cancelled(cancel):
            cancelled = True
            break
        if _drive_tab(
            context, key, page, watch, status, social=social, email=email, name=name, attended=attended
        ):
            cancelled = True
            break

    deadline = None if time_limit is None else _now() + time_limit
    timed_out = False
    while not cancelled and not watch.done():
        if _cancelled(cancel):
            cancelled = True
            break
        if deadline is not None and _now() >= deadline:
            timed_out = True
            break
        try:
            open_pages = watch.open_tabs(context)
        except Exception:
            break
        if not open_pages:
            break
        try:
            open_pages[0].wait_for_timeout(250)
        except Exception:
            # Closing the whole window races the last short wait. Treat it
            # exactly like context.pages becoming empty.
            break

    if cancelled:
        reason = "browser batch cancelled"
    elif timed_out:
        reason = "the browser download did not finish in time"
    else:
        reason = "browser tab closed before the download finished"
    watch.fail_unsettled(reason)
    return cancelled


def _browser_pass(
    watch: _TabWatch,
    rows: dict[str, tuple[Any, str]],
    status: StatusCallback | None,
    *,
    hidden: bool,
    social: bool,
    email: str | None,
    name: str | None,
    time_limit: float | None,
) -> bool:
    """Open ``rows`` in one context, hidden or in a window, and see them through.

    True when the batch was cancelled. A hidden pass always has a time limit:
    nobody can close its tabs.
    """

    from . import auth, browser_session

    watch.reset_tabs()
    try:
        with browser_session.sync_browser_context(
            auth.soundcloud_browser_profile_path(), accept_downloads=True, headless=hidden
        ) as context:
            context.on("page", watch.watch_popup)
            pages = _open_gate_tabs(context, rows, watch)
            return _await_downloads(
                context,
                pages,
                watch,
                status,
                social=social,
                email=email,
                name=name,
                attended=not hidden,
                time_limit=PROVIDER_WAIT_SECONDS if hidden else time_limit,
            )
    except browser_session.ChromiumMissing:
        error = GateUnavailable("Playwright Chromium is not installed")
    except browser_session.AutomationError as exc:
        error = GateUnavailable(str(exc))
    for key in rows:
        if not watch.settled(key):
            watch.failures[key] = error
    return False


def _needs_you(reasons: dict[str, str]) -> str:
    count = len(reasons)
    what = "; ".join(sorted(set(reasons.values())))
    return f"Opening the browser window for {count} gate{'s' if count != 1 else ''}: {what}"


def download_hypeddit_batch_in_browser(
    items: list[tuple[Any, str]],
    directory: Path,
    cancel: Any,
    *,
    social: bool = True,
    status: StatusCallback | None = None,
    config: Any | None = None,
    time_limit: float | None = None,
) -> HypedditBrowserBatchResult:
    """Finish every manual Hypeddit gate in the private profile, hidden first.

    Each gate opens as a tab of one hidden Chromium and has its steps walked
    (see ``_drive_gate_steps``). Rows that stop at something only a person
    can do - a provider asking for a login, a CAPTCHA, an email the profile
    lacks - are opened again together in a visible window, where the same
    driver carries on around the person. Without a ``time_limit`` (seconds)
    that window stays as long as a tab stays open.
    """

    pending, failures, cancelled = _screen_batch(items, cancel)
    if not pending:
        return HypedditBrowserBatchResult(
            failures=tuple(failures.items()), cancelled=cancelled
        )

    from . import auth

    email, name = _gate_email(config), _gate_name(config)
    watch = _TabWatch(pending, directory, cancel, failures)
    if not auth.BROWSER_PROFILE_LOCK.acquire(blocking=False):
        error = GateUnavailable("The private browser profile is already in use")
        watch.failures.update({key: error for key in pending})
        return watch.result(cancelled=False)
    try:
        cancelled = _browser_pass(
            watch, pending, status, hidden=True, social=social, email=email, name=name, time_limit=None
        )
        if watch.deferred and not cancelled:
            reasons, watch.deferred = watch.deferred, {}
            if status is not None:
                status(_needs_you(reasons))
            cancelled = _browser_pass(
                watch,
                {key: pending[key] for key in reasons},
                status,
                hidden=False,
                social=social,
                email=email,
                name=name,
                time_limit=time_limit,
            )
        watch.fail_deferred("browser batch cancelled")
    finally:
        auth.BROWSER_PROFILE_LOCK.release()

    return watch.result(cancelled)


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
                session.post(f"https://gaterush.me/save-comment/{slug}", data={"commentText": comment, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)
            session.post(f"https://gaterush.me/save-email/{slug}", data={"email": email, "_csrf": csrf}, headers=ajax_headers, timeout=timeout)

            steps_m = re.findall(r'id["\']:\s*["\']([^"\']+)["\']', text)
            # The scraped ids are unioned with the six stock providers because
            # the regex misses steps rendered client-side; GateRush ignores a
            # stepId the gate did not declare, so over-posting is harmless.
            for st in set(steps_m) | {"instagram", "spotify", "youtube", "soundcloud", "email", "tiktok"}:
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


# A hub page listing more than this many wrapped links is a page we have misread.
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


def _offers_a_download(soup: BeautifulSoup) -> bool:
    """Whether the page offers to hand over a file at all.

    A real follow-to-download gate says so on the thing you press, and a page
    that says it keeps its gate badge rather than being replaced by the shop it
    also happens to link to. A pure link list never says it.

    ponytail: the words are a fixed list in eight languages, and only the action
    elements are read. A gate whose button is an image, or whose language is not
    here, still reads as a hub. Widening it means the word list, not the shape.
    """

    for tag in soup.find_all(ACTION_TAGS):
        # An <input type=submit> carries its label in value=, not in its text.
        candidates = (tag.get_text(" ", strip=True), tag.get("value") or "", tag.get("title") or "")
        haystack = " ".join(candidates).lower()
        if any(word in haystack for word in DOWNLOAD_WORDS):
            return True
    return False


def _read_page(
    url: str, session: requests.Session, timeout: float
) -> tuple[str, str | None] | None:
    """The page behind a link as ``(landed_url, body)``.

    ``None`` when the host never answered. A ``None`` body when something did
    answer but there is nothing to read: an HTTP error, or a redirect towards
    an address this program refuses to request. This is the function that
    issues the request, so it is the one that has to refuse an address inside
    the user's network - the caller filters too, but not for that.
    """

    try:
        response, landed = follow_redirects(
            session, url, headers=REQUEST_HEADERS, timeout=timeout
        )
    except UnsafeRedirect as exc:
        LOGGER.debug("Refusing to read %s: %s", redact_url(url), exc)
        return url, None
    except requests.RequestException as exc:
        LOGGER.debug(
            "Could not read %s (%s)", redact_url(url), type(exc).__name__
        )
        return None
    if response.status_code >= 400:
        return landed, None
    return landed, response.text


def _unwrap_hub_links(
    session: requests.Session,
    landed: str,
    soup: BeautifulSoup,
    timeout: float,
    seen: set[str],
) -> list[tuple[str, str]]:
    """Shops linked from the page, directly or behind the hub's own redirects.

    Hubs wrap each shop in their own redirect (ampsuite's link-redirect, most
    smart-link services), so the real destination only shows up in a Location
    header.
    """

    hub_host = host_of(landed)
    found: list[tuple[str, str]] = []
    wrapped: list[tuple[str, str]] = []
    for href, text in _page_anchors(landed, soup, seen):
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
                headers=REQUEST_HEADERS,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            location = hop.headers.get("Location", "")
            hop.close()
        except requests.RequestException:
            continue
        location = urllib.parse.urljoin(href, location)
        if (
            location
            and is_fetchable(location)
            and store_for_url(location) in SHOP_CATEGORIES
        ):
            found.append((location, text))
    return found


def _classify_link_page(
    soup: BeautifulSoup, hypeddit: HypedditInspection | None, shops: bool
) -> tuple[bool, bool]:
    """``(keep_original, recognized)`` for the page behind a purchase link."""

    if hypeddit:
        # Unknown means protocol drift, not a proven empty wrapper. Keep it so
        # the caller has a diagnostic/manual fallback instead of losing a link.
        keep_original = hypeddit.kind in {
            "gate",
            "hybrid",
            "challenge",
            "unknown",
        } or (_offers_a_download(soup) and not hypeddit.nested_gates)
        return keep_original, hypeddit.kind != "unknown"
    keep_original = _offers_a_download(soup)
    return keep_original, shops or keep_original


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

    page = _read_page(url, session, timeout)
    if page is None:
        return None
    landed, text = page
    if text is None:
        return LinkPageInspection()

    # The landed URL, not url: a hub reached through a redirect wraps its shops
    # in links relative to where it landed, not where it was asked for.
    soup = BeautifulSoup(text, "html.parser")
    hypeddit = _inspect_hypeddit(landed, text, soup) if is_hypeddit_url(landed) else None
    found: list[tuple[str, str]] = list(hypeddit.shops) if hypeddit else []
    seen: set[str] = {
        *(pair[0] for pair in found),
        *((hypeddit.nested_gates if hypeddit else ())),
    }
    found.extend(_unwrap_hub_links(session, landed, soup, timeout, seen))

    # A shop linked both directly and through a wrapper is still one shop.
    unique: dict[str, tuple[str, str]] = {}
    for pair in found:
        unique.setdefault(pair[0], pair)
    keep_original, recognized = _classify_link_page(soup, hypeddit, bool(unique))
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
    resolver = _RESOLVERS.get(host)
    if resolver is not None:
        return resolver(url, session, timeout, config)

    # Direct audio file links (S3, R2, CDN, raw audio files). urlparse strips
    # the query and fragment, so "...mp3?sig=..." still matches. Deliberately
    # not part of RESOLVABLE_HOSTS - this is a shape check, not a host route.
    if urllib.parse.urlparse(url).path.lower().endswith(DOWNLOAD_SUFFIXES):
        return url

    return None

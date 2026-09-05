"""Bypass and resolver module for download gates (Hypeddit, ToneDen, etc.).

Extracts direct file download URLs from gate pages without requiring manual
social media login steps.
"""

import time
import urllib.parse
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dj_digger import automation_errors
from dj_digger.gate_models import (
    GateDownloadError,
    GateError,
    GateManualActionRequired,
    GateProfileRequired,
    GateProtocolChanged,
    GateSocialActionsDisabled,
    GateUnavailable,
    HypedditBrowserBatchResult,
)

from ..config import DEFAULT_NAME
from ..http import is_fetchable
from ..links import host_of, is_hypeddit_url
from ..models import Cancelled
from .providers import (
    CLICK_THROUGH_STEPS,
    DIRECT_STEP,
    LOGGER,
    PROVIDER_OAUTH_STEPS,
    _cancelled,
    check_gate_action,
    config_or_default,
)

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
# Set by a download and dropped by the page a few seconds later; while it is
# there the unlock endpoint answers the next gate with download_status false
# (seen on every second tab of a hidden batch, 2026-09-02).
GATE_DOWNLOAD_COOKIE = "filedownloading"
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
    config=None,
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

    def guard(*, profile=False):
        if _cancelled(cancel):
            raise GateManualActionRequired(CANCELLED)
        if config is not None:
            check_gate_action(config, social=True, profile=profile)

    if not social:
        return False
    guard()
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
        guard()
        kind = slide.kind
        if kind == DIRECT_STEP:
            if not _click_gate_download(page):
                raise _NeedsPerson("the gate's download button is not where it was")
            return True
        if kind in GATE_CONNECT_STEPS:
            _connect_provider(context, page, slide.locator, cancel, status, attended=attended)
        elif kind == "email":
            _share_email(slide.locator, email, name, guard=guard, config=config)
        elif kind in CLICK_THROUGH_STEPS:
            _click_through(context, page, slide.locator, guard=guard)
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


def _click_through(context: Any, page: Any, slide: Any, *, guard=lambda: None) -> None:
    """Tick the slide's follow/like links, closing the pages they open, then Next."""

    actions = slide.locator(GATE_PENDING_ACTION)
    before = list(context.pages)
    for _ in range(MAX_GATE_STEPS):
        if not actions.count():
            break
        guard()
        actions.first.click(**_CLICK)
        _close_popups(context, page, before, wait=True)
    guard()
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


def _share_email(slide: Any, email: str | None, name: str | None, *, guard=lambda **_: None, config=None) -> None:
    guard()
    if config is not None:
        email, name = _gate_email(config), _gate_name(config)
    if not email:
        raise _NeedsPerson("the gate wants an email address and the profile has none")
    if slide.locator(GATE_NAME_INPUT).count():
        if not name:
            raise _NeedsPerson("the gate wants a name and the profile has none")
        slide.locator(GATE_NAME_INPUT).first.fill(name)
    slide.locator(GATE_EMAIL_INPUT).first.fill(email)
    guard(profile=True)
    if config is not None and (email, name) != (_gate_email(config), _gate_name(config)):
        raise GateProfileRequired("The gate profile changed before submission")
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
        page.context.clear_cookies(name=GATE_DOWNLOAD_COOKIE)
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
    if result.cancelled:
        raise Cancelled()
    raise GateUnavailable("The browser produced neither a file nor a reason")


def _screen_batch(
    items: list[tuple[Any, str]], cancel: Any
) -> tuple[dict[str, tuple[Any, str]], dict[str, GateError], bool]:
    """Rows worth opening, rows refused before any browser starts, and whether
    the whole batch was cancelled before it began."""

    keyed = {track.key: (track, url) for track, url in items}
    if _cancelled(cancel):
        return {}, {}, True
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
        config=None,
    ) -> None:
        self.config = config
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
        from .. import files

        track, _url = self.pending[key]
        try:
            self.completed[key] = files.save_browser_download(
                download, track, self.directory, self.cancel
            )
        except Cancelled:
            return
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
        if _on_hot_or_not(page):
            # The first visit to a gate after a download in this profile is
            # sent to Hypeddit's hot-or-not poll instead; the next visit gets
            # the gate (seen on a hidden pass, 2026-09-02).
            _track, url = watch.pending[key]
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if not _drive_gate_steps(
            context, page, watch.cancel, status, social=social, email=email, name=name, attended=attended, config=watch.config
        ) and not attended:
            raise _NeedsPerson("the gate page has no step controls this program knows")
    except (GateSocialActionsDisabled, GateProfileRequired) as exc:
        watch.failures[key] = exc
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


def _on_hot_or_not(page: Any) -> bool:
    url = str(page.url)
    return is_hypeddit_url(url) and urllib.parse.urlsplit(url).path.startswith("/hot-or-not/")


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
        return True
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

    from .. import auth, browser_session

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
    except automation_errors.ChromiumMissing:
        error = GateUnavailable("Playwright Chromium is not installed")
    except automation_errors.AutomationError as exc:
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

    from .. import auth

    email, name = _gate_email(config), _gate_name(config)
    watch = _TabWatch(pending, directory, cancel, failures, config_or_default(config))
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
        if cancelled:
            watch.deferred.clear()
        else:
            watch.fail_deferred("browser tab closed before the download finished")
    finally:
        auth.BROWSER_PROFILE_LOCK.release()

    return watch.result(cancelled)



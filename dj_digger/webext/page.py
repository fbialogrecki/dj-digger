"""Playwright-shaped pages over the browser-extension protocol (plan sections 1, 3 and 5).

``ExtensionContext``/``ExtensionPage``/``Locator``/``Download`` are the blocking
flavour (gates, auth); the ``Async*`` classes are the native asyncio flavour
(cart). Method names and semantics follow the Playwright calls this program
used: waiting actions, immediate ``count``/``is_visible``, role queries that
skip hidden elements, and ``page.on`` events.

Both flavours share one implementation. Every operation is a ``_Cmd`` (one
protocol command plus how to turn its result into the return value) built by a
``_name`` method; ``_sync``/``_asynchronous`` expose it as ``name`` and only
differ in how the command is run and how events are dispatched:

* sync: events of the session are queued and dispatched on the calling thread
  inside every command and in ``wait_for_timeout`` (like Playwright's sync API);
* async: events are handed to the event loop with ``call_soon_threadsafe``. A
  cancelled command (``asyncio.wait_for``) makes the link send ``cancel`` and
  keeps its tab busy until the extension's late answer arrives, so commands of
  one tab never overlap (plan K2).

Errors: ``PageError`` (``.code`` from the extension) is what a Playwright
``Error`` was, deliberately *not* an ``AutomationError``; ``PageTimeout`` is its
timeout. A dead or restarted browser raises ``AutomationError`` as the link does.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import shutil
import stat
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

from ..automation_errors import AutomationError
from .link import DEFAULT_TIMEOUT, ExtensionError, Listener, Session

LOGGER = logging.getLogger(__name__)

ACTION_TIMEOUT_MS = 15_000  # was browser_session.ACTION_TIMEOUT_MS
GOTO_TIMEOUT_MS = 30_000
REPLY_SLACK_S = 2.0  # Python waits this much longer than the extension's own timeout
CLOSE_TIMEOUT_S = 5.0
CLEARABLE_COOKIE = "filedownloading"
UNQUEUED = frozenset({"tab.close", "tab.info", "tab.focus"})  # as in background.js
PAGE_EVENTS = frozenset({"download", "popup", "close", "download_blocked", "download_failed"})
CONTEXT_EVENTS = frozenset({"page", "close"})


class PageError(Exception):
    """One command failed in the extension (what a Playwright ``Error`` was)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PageTimeout(PageError):
    """An element or page did not appear in time (Playwright's ``TimeoutError``)."""


@dataclass(frozen=True)
class Response:
    """What ``goto``/``reload`` return: the final main-frame status, if the browser reported one."""

    status: int | None
    url: str


# -- locator steps ---------------------------------------------------------------------

# Python-only regex syntax; the extension compiles patterns with JavaScript's RegExp.
_NOT_JS = re.compile(r"\(\?(?:P|[aiLmsux#-])|\\[AZ]")


def _matcher(key: str, value: Any, exact: bool) -> dict:
    """A string is a case-insensitive substring (``exact``: whole, case-sensitive);
    a compiled pattern is sent as its source plus ``flags``."""

    if not isinstance(value, re.Pattern):
        return {key: str(value), "exact": bool(exact)}
    extra = value.flags & ~re.UNICODE  # str patterns always carry re.UNICODE
    if extra & ~re.IGNORECASE:
        raise ValueError("only re.IGNORECASE can be carried over to the browser's RegExp")
    if _NOT_JS.search(value.pattern):
        raise ValueError("regular expression uses syntax JavaScript does not have")
    return {key: value.pattern, "flags": "i" if extra else ""}


def _selector(selector: str) -> dict:
    return {"xpath": selector[6:]} if selector.startswith("xpath=") else {"css": selector}


def _positive(timeout: float | None) -> int:
    if timeout is None:
        return ACTION_TIMEOUT_MS
    if timeout <= 0:
        raise ValueError("timeout must be positive (no unlimited waits)")
    return int(timeout)


# The two page scripts stores/bandcamp.py evaluates; anything else is refused (plan section 4,
# point 5). Compared with whitespace collapsed; dom.js holds the matching named functions.
_EVALUATE = {
    "() => { const t = globalThis.TralbumData || {}; const current = t.current || {}; return { "
    'id: String(current.track_id || current.id || ""), '
    'title: String(current.title || "").slice(0, 500), '
    'artist: String(current.artist || t.artist || "").slice(0, 500), '
    "minimum: current.minimum_price, "
    'currency: String(current.currency || t.currency || "").slice(0, 12), }; }': "bandcampTralbum",
    "() => (globalThis.TralbumData || {}).current?.set_price ?? null": "bandcampSuggestedPrice",
}


# -- one protocol command and how to run it ------------------------------------------


class _Cmd(NamedTuple):
    cmd: str
    args: dict
    wait_ms: int | None = None  # the extension waits this long (goto, waiting actions)
    tab: int | None = None
    post: Callable[[Any], Any] | None = None
    ignore: tuple[str, ...] = ()  # error codes that count as success (result None)

    def reply_timeout(self) -> float:
        return DEFAULT_TIMEOUT if self.wait_ms is None else self.wait_ms / 1000 + REPLY_SLACK_S


def _sync(*names: str):
    def decorate(cls):
        for name in names:
            def method(self, *args, _op="_" + name, **kwargs):
                owner = getattr(self, "_ctx", self)
                return owner._run(getattr(self, _op)(*args, **kwargs))

            method.__name__ = name
            setattr(cls, name, method)
        return cls

    return decorate


def _asynchronous(*names: str):
    def decorate(cls):
        for name in names:
            async def method(self, *args, _op="_" + name, **kwargs):
                owner = getattr(self, "_ctx", self)
                return await owner._run(getattr(self, _op)(*args, **kwargs))

            method.__name__ = name
            setattr(cls, name, method)
        return cls

    return decorate


def _listen(listeners: dict, allowed: frozenset, event: str, callback: Callable) -> None:
    if event not in allowed:
        raise ValueError(f"unsupported event {event!r}")
    listeners.setdefault(event, []).append(callback)


def _fire(listeners: dict, event: str, *payload: Any) -> None:
    for callback in list(listeners.get(event, ())):
        try:
            callback(*payload)
        except Exception as exc:  # one handler must not stop the others or the dispatcher
            LOGGER.warning("webext %s handler failed: %s", event, type(exc).__name__)


# -- download -------------------------------------------------------------------------


def _copy_validated(source: str, target: str) -> None:
    """Copy a finished download: a regular file, never a symlink, within the size limit."""

    from ..files import MAX_DOWNLOAD_BYTES  # not at import time: files -> gates -> this package

    if os.path.islink(source):
        raise AutomationError("the downloaded file is a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise AutomationError("the downloaded file cannot be read") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise AutomationError("the downloaded file is not a regular file")
        if info.st_size > MAX_DOWNLOAD_BYTES:
            raise AutomationError(f"Download exceeded {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB")
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "rb") as incoming, open(target, "wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)


class _Download:
    """A finished, attributed download (``download.done``). The file is the browser's
    own; ``save_as`` copies it away and asks the extension to delete the original."""

    def __init__(self, ctx: _Context, page: _Page, frame: dict):
        self._ctx = ctx
        self.page = page
        self.id: int = frame["downloadId"]
        self.path: str = frame["path"]
        self.mime = frame.get("mime")
        self.bytes = frame.get("bytes")

    @property
    def suggested_filename(self) -> str:
        return re.split(r"[\\/]", self.path)[-1]

    def _discard(self) -> _Cmd:
        return _Cmd("download.discard", {"downloadId": self.id})


class Download(_Download):
    def save_as(self, path: str | os.PathLike) -> None:
        _copy_validated(self.path, os.fspath(path))
        try:
            self._ctx._run(self._discard())
        except (PageError, AutomationError) as exc:  # the copy is kept either way
            LOGGER.debug("webext download not discarded: %s", type(exc).__name__)


class AsyncDownload(_Download):
    async def save_as(self, path: str | os.PathLike) -> None:
        await asyncio.to_thread(_copy_validated, self.path, os.fspath(path))
        try:
            await self._ctx._run(self._discard())
        except (PageError, AutomationError) as exc:
            LOGGER.debug("webext download not discarded: %s", type(exc).__name__)


# -- locator --------------------------------------------------------------------------


class _Locator:
    _ctx: _Context

    def __init__(self, page: _Page, steps: tuple = ()):
        self._page = page
        self._ctx = page._ctx
        self._steps = tuple(steps)

    def _with(self, step: dict) -> _Locator:
        return type(self)(self._page, (*self._steps, step))

    @property
    def first(self) -> _Locator:
        return self._with({"first": True})

    def nth(self, index: int) -> _Locator:
        return self._with({"nth": int(index)})

    def locator(self, selector: str) -> _Locator:
        return self._with(_selector(selector))

    def get_by_role(self, role: str, *, name: Any = None, exact: bool = False) -> _Locator:
        step = {"role": role} if name is None else {"role": role, **_matcher("name", name, exact)}
        return self._with(step)

    def get_by_text(self, text: Any, *, exact: bool = False) -> _Locator:
        return self._with(_matcher("text", text, exact))

    def get_by_placeholder(self, text: Any, *, exact: bool = False) -> _Locator:
        return self._with(_matcher("placeholder", text, exact))

    def _dom(self, op: str, *, timeout: float | None = None, waits: bool = False,
             post: Callable[[Any], Any] | None = None, **extra: Any) -> _Cmd:
        args = {"tabId": self._page._tab_id, "locator": list(self._steps), **extra}
        wait_ms = None
        if waits:
            wait_ms = args["timeoutMs"] = _positive(timeout)
        return _Cmd(f"dom.{op}", args, wait_ms, self._page._tab_id, post)

    def _count(self) -> _Cmd:
        return self._dom("count", post=int)

    def _is_visible(self) -> _Cmd:
        return self._dom("visible", post=bool)

    def _click(self, *, timeout: float | None = None, force: bool = False) -> _Cmd:
        # ponytail: force is accepted and moot - dom.js never checks occlusion, it only
        # waits for a visible, enabled element and dispatches the full mouse sequence
        return self._dom("click", waits=True, timeout=timeout, post=lambda _: None)

    def _fill(self, value: str, *, timeout: float | None = None) -> _Cmd:
        return self._dom("fill", waits=True, timeout=timeout, value=str(value), post=lambda _: None)

    def _inner_text(self, *, timeout: float | None = None) -> _Cmd:
        return self._dom("text", waits=True, timeout=timeout)

    # ponytail: innerText falls back to textContent in dom.js, close enough for the SVG badge
    _text_content = _inner_text

    def _get_attribute(self, name: str, *, timeout: float | None = None) -> _Cmd:
        return self._dom("attr", waits=True, timeout=timeout, name=name)

    def _input_value(self, *, timeout: float | None = None) -> _Cmd:
        return self._dom("value", waits=True, timeout=timeout)

    def _wait_for(self, *, state: str = "visible", timeout: float | None = None) -> _Cmd:
        if state != "visible":
            raise ValueError("only wait_for(state='visible') is supported")
        return self._dom("waitVisible", waits=True, timeout=timeout, post=lambda _: None)

    def _all_texts_and_attrs(self, attrs: list[str], text: bool = True, limit: int = 500) -> _Cmd:
        """Every match in one command: ``[{"attrs": {name: value | None}, "text": str}]``."""

        names = [str(name) for name in attrs]
        return self._dom("all", attrs=names, text=bool(text), limit=min(int(limit), 500))


_LOCATOR_OPS = ("count is_visible click fill inner_text text_content get_attribute input_value "
                "wait_for all_texts_and_attrs").split()


@_sync(*_LOCATOR_OPS)
class Locator(_Locator):
    pass


@_asynchronous(*_LOCATOR_OPS)
class AsyncLocator(_Locator):
    pass


# -- page -----------------------------------------------------------------------------


class _Page:
    _locator_cls: type[_Locator]

    def __init__(self, ctx: _Context, tab_id: int, url: str = "about:blank", opener: _Page | None = None):
        self._ctx = ctx
        self._tab_id = tab_id
        self._url = url
        self._opener = opener
        self._closed = False
        self._listeners: dict[str, list[Callable]] = {}

    @property
    def url(self) -> str:
        return self._url

    @property
    def context(self) -> _Context:
        return self._ctx

    def on(self, event: str, callback: Callable) -> None:
        _listen(self._listeners, PAGE_EVENTS, event, callback)

    def is_closed(self) -> bool:
        return self._closed

    def _mark_closed(self) -> None:
        if not self._closed:
            self._closed = True
            _fire(self._listeners, "close", self)

    def _opener_page(self) -> _Page | None:
        return self._opener if self._opener is not None and not self._opener.is_closed() else None

    def locator(self, selector: str) -> _Locator:
        return self._locator_cls(self, (_selector(selector),))

    def get_by_role(self, role: str, *, name: Any = None, exact: bool = False) -> _Locator:
        return self._locator_cls(self).get_by_role(role, name=name, exact=exact)

    def get_by_text(self, text: Any, *, exact: bool = False) -> _Locator:
        return self._locator_cls(self).get_by_text(text, exact=exact)

    def get_by_placeholder(self, text: Any, *, exact: bool = False) -> _Locator:
        return self._locator_cls(self).get_by_placeholder(text, exact=exact)

    def _navigated(self, result: dict) -> Response:
        self._url = str(result.get("url") or self._url)
        return Response(result.get("status"), self._url)

    def _tab(self, cmd: str, wait_ms: int | None = None, post: Callable | None = None, **extra: Any) -> _Cmd:
        return _Cmd(cmd, {"tabId": self._tab_id, **extra}, wait_ms, self._tab_id, post)

    def _goto(self, url: str, *, wait_until: str | None = "domcontentloaded",
              timeout: float | None = None) -> _Cmd:
        if wait_until not in (None, "domcontentloaded"):
            raise ValueError("only wait_until='domcontentloaded' is supported")
        ms = GOTO_TIMEOUT_MS if timeout is None else _positive(timeout)
        return self._tab("tab.goto", ms, self._navigated, url=url, timeoutMs=ms)

    def _reload(self, *, wait_until: str | None = "domcontentloaded", timeout: float | None = None) -> _Cmd:
        if wait_until not in (None, "domcontentloaded"):
            raise ValueError("only wait_until='domcontentloaded' is supported")
        ms = GOTO_TIMEOUT_MS if timeout is None else _positive(timeout)
        return self._tab("tab.reload", ms, self._navigated, timeoutMs=ms)

    @staticmethod
    def _html(result: dict) -> str:
        if result.get("truncated"):
            raise AutomationError("store page is too large to inspect safely")
        return str(result.get("html") or "")

    def _content(self) -> _Cmd:
        return self._tab("tab.html", post=self._html)

    def _evaluate(self, expression: str, arg: Any = None) -> _Cmd:
        name = _EVALUATE.get(" ".join(str(expression).split()))
        if name is None or arg is not None:
            raise ValueError("only the page scripts of stores/bandcamp.py can be evaluated")
        return self._tab("dom.call", name=name)

    def _announce_download(self) -> _Cmd:
        """Tell the extension this page's next download is the one (not Playwright API): a gate
        file from a CDN carries only the gate's origin, which other gate tabs share."""
        return self._tab("download.expect", post=lambda _: None)

    def _bring_to_front(self) -> _Cmd:
        return self._tab("tab.focus", post=lambda _: None)

    def _release(self) -> _Cmd:
        """Hand the tab over to the person (not Playwright API): the extension stops owning it, so
        it stays open after the session and checkout from it is theirs."""
        return self._tab("tab.release", post=lambda _: self._mark_closed())

    def _screenshot(self, *, path: str | os.PathLike | None = None, full_page: bool = False) -> _Cmd:
        # ponytail: best effort, diagnostics only - the visible part of the active tab
        def save(result: dict) -> bytes:
            png = base64.b64decode(result["png"])
            if path is not None:
                Path(path).write_bytes(png)
            return png

        return self._tab("tab.capture", post=save)

    def _close(self) -> _Cmd:
        def closed(_result: Any) -> None:
            self._mark_closed()

        # a tab that is already gone is a closed page, not an error
        cmd = self._tab("tab.close", post=closed)
        return cmd._replace(ignore=("tab_closed", "not_owned"))


_PAGE_OPS = "goto reload content evaluate announce_download bring_to_front release screenshot".split()


@_sync(*_PAGE_OPS)
class ExtensionPage(_Page):
    _locator_cls = Locator

    def opener(self) -> ExtensionPage | None:
        return self._opener_page()

    def close(self) -> None:
        if not self._closed:
            self._ctx._run(self._close())

    def wait_for_timeout(self, timeout: float) -> None:
        self._ctx._wait(timeout / 1000, self)


@_asynchronous(*_PAGE_OPS)
class AsyncExtensionPage(_Page):
    _locator_cls = AsyncLocator

    async def opener(self) -> AsyncExtensionPage | None:
        return self._opener_page()

    async def close(self) -> None:
        if not self._closed:
            await self._ctx._run(self._close())

    async def wait_for_timeout(self, timeout: float) -> None:
        if self._closed:
            raise AutomationError("the page is closed")
        await asyncio.sleep(timeout / 1000)


# -- context --------------------------------------------------------------------------


class _Context:
    _page_cls: type[_Page]
    _download_cls: type[_Download]

    def __init__(self, session: Session):
        self._session = session
        self._pages: dict[int, _Page] = {}
        self._listeners: dict[str, list[Callable]] = {}
        self._downloads: dict[int, _Page] = {}  # announced by download.created, not yet done
        self._blocked: set[int] = set()
        self._closed = False
        self._visible = False
        self._offscreen = False  # hidden pass without a browser window (Chromium, plan F4b)
        self._window_id: int | None = None  # this session's own work window (several may be open)

    # -- state -------------------------------------------------------------------

    @property
    def pages(self) -> list[_Page]:
        return [page for page in self._pages.values() if not page._closed]

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def offscreen(self) -> bool:
        """True when the session runs in the extension's offscreen document: it has no browser
        window, cannot be shown, and its tabs are frames (no popups, no ``bring_to_front``)."""

        return self._offscreen

    def on(self, event: str, callback: Callable) -> None:
        _listen(self._listeners, CONTEXT_EVENTS, event, callback)

    def is_closed(self) -> bool:
        return self._closed

    def _make_page(self, tab_id: int, url: str = "about:blank", opener: _Page | None = None) -> _Page:
        page = self._pages[tab_id] = self._page_cls(self, tab_id, url, opener)
        return page

    def _finish(self) -> None:
        """The window is gone (closed here, or the browser went away)."""

        if self._closed:
            return
        self._closed = True
        for page in list(self._pages.values()):
            page._mark_closed()
        self._release()
        self._session.close()
        _fire(self._listeners, "close", self)

    def _release(self) -> None:
        """Hook: the async flavour frees tabs waiting for a late reply."""

    # -- events ------------------------------------------------------------------

    def _dispatch(self, item: Any) -> None:
        if isinstance(item, Exception):  # the session ended (browser or extension restarted)
            self._finish()
            return
        tab_id = item.get("tabId")
        page = self._pages.get(tab_id) if isinstance(tab_id, int) else None
        match item.get("event"):
            case "tab.created":
                opener = self._pages.get(item.get("openerTabId"))
                if opener is None or not isinstance(tab_id, int) or tab_id in self._pages:
                    return  # a popup of somebody else's tab
                popup = self._make_page(tab_id, str(item.get("url") or "about:blank"), opener)
                _fire(self._listeners, "page", popup)
                _fire(opener._listeners, "popup", popup)
            case "tab.updated" if page is not None:
                page._url = str(item.get("url") or page._url)
            case "tab.removed" if page is not None:
                page._mark_closed()
            case "window.removed" if self._window_id is not None and item.get("windowId") == self._window_id:
                self._finish()  # closed by hand (or by window.close): the tabs were reported already
            case "tab.error" if page is not None:  # every session of the connection gets the event
                LOGGER.warning("webext tab refused: %s", item.get("code"))
            case "download.created" if page is not None and isinstance(item.get("downloadId"), int):
                self._downloads[item["downloadId"]] = page
            case "download.blocked" if page is not None:
                self._blocked.add(item.get("downloadId"))
                _fire(page._listeners, "download_blocked", item)
            case "download.failed":
                if (owner := self._downloads.pop(item.get("downloadId"), None)) is not None:
                    _fire(owner._listeners, "download_failed", item)
            case "download.done":
                self._download_done(item)

    def _download_done(self, item: dict) -> None:
        download_id = item.get("downloadId")
        path = item.get("path")
        # Only a download announced for one of our tabs is ever handed to the program.
        owner = self._downloads.pop(download_id, None)
        if owner is None or download_id in self._blocked or not isinstance(path, str) or not os.path.isabs(path):
            return
        _fire(owner._listeners, "download", self._download_cls(self, owner, item))

    # -- operations --------------------------------------------------------------

    def _complete(self, cmd: _Cmd, result: Any) -> Any:
        return cmd.post(result) if cmd.post else result

    def _failed(self, cmd: _Cmd, exc: ExtensionError) -> None:
        """Playwright-style error for a refused command (returns only for ignored codes)."""

        if exc.code in cmd.ignore:
            return
        if exc.code in ("tab_closed", "not_owned") and (page := self._pages.get(cmd.tab)) is not None:
            page._mark_closed()
        error = PageTimeout if exc.code == "timeout" else PageError
        raise error(exc.code, str(exc)) from None

    def _opened(self, visible: bool, *, new_window: bool = False) -> Callable:
        def post(result: Any) -> None:
            if new_window:
                self._window_id = int(result["windowId"])
                self._offscreen = bool(result.get("offscreen"))  # an old or Firefox extension: False
            self._visible = bool(visible)

        return post

    def _registered(self, result: dict) -> _Page:
        page = self._make_page(int(result["tabId"]))
        _fire(self._listeners, "page", page)
        return page

    def _new_page(self) -> _Cmd:
        return _Cmd("tab.new", {"windowId": self._window_id}, post=self._registered)

    def _set_visible(self, visible: bool) -> _Cmd:
        return _Cmd("window.show" if visible else "window.hide", {"windowId": self._window_id},
                    post=self._opened(visible))

    def _show(self) -> _Cmd:
        return self._set_visible(True)

    def _hide(self) -> _Cmd:
        return self._set_visible(False)

    def _cookies(self, urls: list[str] | None = None) -> _Cmd:
        # Only the one cookie the program ever reads (auth.py), not a cookie jar.
        if not urls or any(urlparse(url).hostname != "soundcloud.com" for url in urls):
            raise ValueError("only the SoundCloud oauth_token cookie is available")

        def shaped(token: str | None) -> list[dict]:
            return [{"name": "oauth_token", "value": token, "domain": ".soundcloud.com", "path": "/"}] if token else []

        return _Cmd("cookie.soundcloudToken", {}, post=shaped)

    def _clear_cookies(self, name: str | None = None, **filters: Any) -> _Cmd:
        if name != CLEARABLE_COOKIE or filters:
            raise ValueError(f"only the {CLEARABLE_COOKIE!r} cookie can be cleared")
        return _Cmd("cookie.clearGateDownload", {}, post=lambda _: None)


def _open_args(visible: bool, offscreen: bool, background: bool = False) -> dict:
    # offscreen is asked for only by a hidden session; the reply says whether it was granted.
    # background: the session's tabs are inactive tabs of the person's own window (carts, plan Z1).
    hidden = {"offscreen": True} if offscreen and not visible else {"background": True} if background and not visible else {}
    return {"visible": bool(visible), **hidden}


_CONTEXT_OPS = "new_page show hide cookies clear_cookies".split()
_session_lock = threading.Lock()
_default_listener: Listener | None = None


def set_listener(listener: Listener | None) -> None:
    """Make ``listener`` the one sessions use when none is passed (GUI services, CLI)."""

    global _default_listener
    with _session_lock:
        _default_listener = listener


def _listener_for(listener: Listener | None) -> Listener:
    global _default_listener
    if listener is not None:
        return listener
    with _session_lock:
        if _default_listener is None:
            # ponytail: started on first use; callers hold the InstanceLock (link.py) and
            # nothing closes it - set_listener() when an owner wants to manage it
            listener = Listener()
            listener.start()  # may refuse (another app owns the socket): then keep none
            _default_listener = listener
        return _default_listener


@_sync(*_CONTEXT_OPS)
class ExtensionContext(_Context):
    """The work window of one automation batch, driven synchronously."""

    _page_cls = ExtensionPage
    _download_cls = Download

    def __init__(self, session: Session):
        super().__init__(session)
        self._pumping = False

    @classmethod
    def open(
        cls, visible: bool = False, listener: Listener | None = None, *, offscreen: bool = False,
        background: bool = False,
    ) -> ExtensionContext:
        context = cls(_listener_for(listener).session())
        try:
            context._run(_Cmd("window.open", _open_args(visible, offscreen, background),
                              post=context._opened(visible, new_window=True)))
        except BaseException:
            context._finish()
            raise
        return context

    def _pump(self) -> None:
        """Dispatch the queued events here, on the calling thread; not re-entrant."""

        if self._pumping:
            return
        self._pumping = True
        try:
            while (item := self._session.next_event(0)) is not None:
                self._dispatch(item)
        except AutomationError as exc:
            self._dispatch(exc)
            raise
        finally:
            self._pumping = False

    def _run(self, cmd: _Cmd) -> Any:
        self._pump()
        try:
            result = self._session.request(cmd.cmd, cmd.args, cmd.reply_timeout())
        except ExtensionError as exc:
            result = self._failed(cmd, exc)
        value = self._complete(cmd, result)
        self._pump()
        return value

    def _wait(self, seconds: float, page: ExtensionPage | None = None) -> None:
        """``wait_for_timeout``: dispatch events for the whole duration."""

        deadline = time.monotonic() + seconds
        while True:
            if page is not None and page._closed:
                raise AutomationError("the page is closed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                item = self._session.next_event(remaining)
            except AutomationError as exc:
                self._dispatch(exc)
                raise
            if item is not None:
                outer, self._pumping = self._pumping, True
                try:
                    self._dispatch(item)
                finally:
                    self._pumping = outer

    def close(self, reason: str | None = None) -> None:
        if self._closed:
            return
        if self._window_id is not None:
            with contextlib.suppress(AutomationError):  # best effort: the browser may be gone
                self._session.request("window.close", {"windowId": self._window_id}, CLOSE_TIMEOUT_S)
        self._finish()


@_asynchronous(*_CONTEXT_OPS)
class AsyncExtensionContext(_Context):
    """The work window of one automation batch, native asyncio (no threads per command)."""

    _page_cls = AsyncExtensionPage
    _download_cls = AsyncDownload

    def __init__(self, session: Session, loop: asyncio.AbstractEventLoop):
        super().__init__(session)
        self._loop = loop
        self._tails: dict[int, asyncio.Future] = {}  # tab -> its last command's reply arrived

    @classmethod
    async def open(
        cls, visible: bool = False, listener: Listener | None = None, *, offscreen: bool = False,
        background: bool = False,
    ) -> AsyncExtensionContext:
        loop = asyncio.get_running_loop()
        session = await asyncio.to_thread(_listener_for(listener).session)  # may wait for hello
        context = cls(session, loop)
        session.on_event = context._from_reader_thread
        try:
            while (item := session.next_event(0)) is not None:  # arrived before on_event was set
                context._dispatch(item)
            await context._run(
                _Cmd("window.open", _open_args(visible, offscreen, background),
                     post=context._opened(visible, new_window=True)))
        except BaseException:
            context._finish()
            raise
        return context

    def _from_reader_thread(self, item: Any) -> None:
        with contextlib.suppress(RuntimeError):  # the loop is already closed
            self._loop.call_soon_threadsafe(self._dispatch, item)

    def _resolve(self, tail: asyncio.Future) -> None:
        if not tail.done():
            tail.set_result(None)

    def _release(self) -> None:
        for tail in self._tails.values():
            self._resolve(tail)

    async def _run(self, cmd: _Cmd) -> Any:
        tail = None
        if cmd.tab is not None and cmd.cmd not in UNQUEUED:
            while (busy := self._tails.get(cmd.tab)) is not None and not busy.done():
                await asyncio.wait({busy})  # unlike awaiting it, a cancel here leaves it alone
            tail = self._tails[cmd.tab] = self._loop.create_future()

        def late_reply(_frame: dict) -> None:  # reader thread, also for a cancelled command
            if tail is not None:
                with contextlib.suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(self._resolve, tail)

        try:
            _, future = self._session.submit(cmd.cmd, cmd.args, on_reply=late_reply)
        except BaseException:
            if tail is not None:
                self._resolve(tail)
            raise
        try:
            result = await asyncio.wait_for(asyncio.wrap_future(future), cmd.reply_timeout())
        except ExtensionError as exc:
            result = self._failed(cmd, exc)
        except TimeoutError:  # cancel was sent by the link; the tab stays busy until the answer
            raise AutomationError(f"browser extension did not answer {cmd.cmd} in time") from None
        finally:
            if tail is not None and future.done() and not future.cancelled():
                self._resolve(tail)  # answered (or the session ended): the tab is free
        return self._complete(cmd, result)

    async def close(self, reason: str | None = None) -> None:
        if self._closed:
            return
        if self._window_id is not None:
            try:
                _, future = self._session.submit("window.close", {"windowId": self._window_id})
                await asyncio.wait_for(asyncio.wrap_future(future), CLOSE_TIMEOUT_S)
            except (AutomationError, TimeoutError):  # best effort: the browser may be gone
                pass
        self._finish()


@contextmanager
def session(
    visible: bool = False, listener: Listener | None = None, *, offscreen: bool = False,
    background: bool = False,
) -> Iterator[ExtensionContext]:
    """One work window for a batch; leaving the block closes its tabs and the window (only its own:
    other sessions keep theirs). ``offscreen`` asks for a hidden session without any browser window
    (Chromium); check ``context.offscreen``, as other browsers answer with a normal hidden window.
    ``background`` puts the session's tabs, inactive, in the person's own window: no new window."""

    context = ExtensionContext.open(visible, listener, offscreen=offscreen, background=background)
    try:
        yield context
    finally:
        context.close()


@asynccontextmanager
async def async_session(
    visible: bool = False, listener: Listener | None = None, *, offscreen: bool = False
) -> AsyncIterator[AsyncExtensionContext]:
    context = await AsyncExtensionContext.open(visible, listener, offscreen=offscreen)
    try:
        yield context
    finally:
        await context.close()

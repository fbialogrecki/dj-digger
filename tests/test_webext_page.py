"""Offline tests of the Playwright-shaped adapter against a FakeExtension.

The fake speaks the real protocol (plan section 3) over a real ``link.Listener`` and a
``multiprocessing.connection`` client; no browser, no network, no real HOME.
"""

import ast
import asyncio
import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from multiprocessing import connection as mpc
from pathlib import Path

import pytest

from dj_digger import files
from dj_digger.automation_errors import AutomationError
from dj_digger.models import Track
from dj_digger.stores import bandcamp as bandcamp_adapter
from dj_digger.webext import link
from dj_digger.webext import page as webpage

POSIX = os.name != "nt"
ROOT = Path(__file__).resolve().parents[1]
KEY = b"k" * 32
HELLO = {"event": "hello", "browser": "chrome", "version": "1", "protocol": 1, "instance": "a"}
HOLD = object()  # a handler result meaning "do not answer yet"


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    for name in ("HOME", "USERPROFILE", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "home"))


@pytest.fixture
def address():
    if not POSIX:
        yield rf"\\.\pipe\dj-digger-test-{uuid.uuid4().hex}"
        return
    with tempfile.TemporaryDirectory(prefix="djp", dir="/tmp") as directory:  # short: sun_path
        yield os.path.join(directory, "webext.sock")


class FakeError(Exception):
    def __init__(self, code, message=""):
        self.code, self.message = code, message


class FakeExtension:
    """Answers commands with ``handlers[cmd](args)``; ``HOLD`` leaves one pending."""

    def __init__(self, address):
        self.client = mpc.Client(address, authkey=KEY)
        self._lock = threading.Lock()
        self._changed = threading.Condition()
        self.requests: list[dict] = []
        self.cancels: list[int] = []
        tabs = itertools.count(100)
        self.handlers = {
            "window.open": lambda args: {"windowId": 1},
            "window.close": lambda args: None,
            "tab.new": lambda args: {"tabId": next(tabs)},
        }
        self.send(HELLO)
        threading.Thread(target=self._serve, daemon=True).start()

    def send(self, frame):
        with self._lock:
            self.client.send_bytes(json.dumps(frame).encode())

    def emit(self, event, **fields):
        self.send({"event": event, **fields})

    def fail(self, request_id, code, message=""):
        self.send({"id": request_id, "ok": False, "error": {"code": code, "message": message}})

    def answer(self, request_id, result=None):
        self.send({"id": request_id, "ok": True, "result": result})

    def commands(self, name):
        return [r for r in list(self.requests) if r["cmd"] == name]

    def _serve(self):
        try:
            while True:
                frame = json.loads(self.client.recv_bytes())
                with self._changed:
                    if frame["cmd"] == "cancel":
                        self.cancels.append(frame["args"]["id"])
                    else:
                        self.requests.append(frame)
                    self._changed.notify_all()
                if frame["cmd"] != "cancel":
                    self._reply(frame)
        except Exception:  # the connection was closed (possibly under this thread's read)
            pass

    def _reply(self, frame):
        try:
            result = self.handlers.get(frame["cmd"], lambda args: None)(frame["args"])
        except FakeError as error:
            return self.fail(frame["id"], error.code, error.message)
        if result is not HOLD:
            self.answer(frame["id"], result)

    def wait_for(self, name, count=1, timeout=3.0):
        deadline = time.monotonic() + timeout
        with self._changed:
            while len(self.commands(name)) < count:
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"{name} was never sent"
                self._changed.wait(remaining)
        return self.commands(name)[count - 1]


@pytest.fixture
def world(address):
    with link.Listener(address, KEY) as listener:
        ext = FakeExtension(address)
        try:
            yield listener, ext
        finally:
            ext.client.close()


def raising(code, message=""):
    def handler(_args):
        raise FakeError(code, message)

    return handler


def step_list(ext, command):
    return ext.commands(command)[-1]["args"]["locator"]


# -- goto, status, url ------------------------------------------------------------------


def test_goto_returns_the_status_and_keeps_the_url_fresh(world):
    listener, ext = world
    ext.handlers["tab.goto"] = lambda a: {"url": "https://hypeddit.com/x", "status": 200}
    with webpage.session(False, listener) as context:
        page = context.new_page()
        assert context.pages == [page] and page.url == "about:blank" and page.context is context
        response = page.goto("https://hypeddit.com/x", wait_until="domcontentloaded", timeout=5000)
        assert response.status == 200 and page.url == "https://hypeddit.com/x"
        assert ext.commands("tab.goto")[0]["args"] == {
            "tabId": page._tab_id, "url": "https://hypeddit.com/x", "timeoutMs": 5000}
        page.goto("https://hypeddit.com/y")
        assert ext.commands("tab.goto")[1]["args"]["timeoutMs"] == webpage.GOTO_TIMEOUT_MS
        with pytest.raises(ValueError):
            page.goto("https://hypeddit.com/x", wait_until="load")
    assert ext.commands("window.open")[0]["args"] == {"visible": False}
    ext.wait_for("window.close")


def test_a_refused_navigation_is_a_page_error_not_an_automation_error(world):
    listener, ext = world
    ext.handlers["tab.goto"] = raising("refused", "host is not allowlisted: https://evil.test/")
    with webpage.session(False, listener) as context:
        page = context.new_page()
        with pytest.raises(webpage.PageError) as info:
            page.goto("https://evil.test/")
        assert info.value.code == "refused" and not isinstance(info.value, AutomationError)


def test_a_missing_tab_marks_the_page_closed_and_close_is_quiet(world):
    listener, ext = world
    ext.handlers["dom.count"] = raising("tab_closed", "tab no longer exists")
    ext.handlers["tab.close"] = raising("not_owned", "tab is not owned by dj-digger")
    with webpage.session(False, listener) as context:
        page = context.new_page()
        closed = []
        page.on("close", closed.append)
        with pytest.raises(webpage.PageError):
            page.locator("a").count()
        assert page.is_closed() and closed == [page] and context.pages == []
        page.close()  # already closed: nothing to send
        assert not ext.commands("tab.close")
        other = context.new_page()
        other.close()
        assert other.is_closed()


# -- waiting, timeouts -----------------------------------------------------------------


def test_a_waiting_timeout_is_a_page_timeout(world):
    listener, ext = world
    ext.handlers["dom.text"] = raising("timeout", "timeout 1500ms waiting for element")
    ext.handlers["dom.count"] = lambda a: 0
    with webpage.session(False, listener) as context:
        page = context.new_page()
        with pytest.raises(webpage.PageTimeout) as info:
            page.locator("#a").inner_text(timeout=1500)
        assert info.value.code == "timeout" and not isinstance(info.value, AutomationError)
        args = ext.commands("dom.text")[0]["args"]
        assert args["timeoutMs"] == 1500 and args["locator"] == [{"css": "#a"}]
        with pytest.raises(webpage.PageTimeout):
            page.locator("#a").inner_text()
        assert ext.commands("dom.text")[1]["args"]["timeoutMs"] == webpage.ACTION_TIMEOUT_MS
        page.locator("#a").count()  # immediate: no wait
        assert "timeoutMs" not in ext.commands("dom.count")[0]["args"]
        with pytest.raises(ValueError):
            page.locator("#a").click(timeout=0)


def test_python_waits_two_seconds_longer_than_the_extension(world):
    listener, _ext = world
    with webpage.session(False, listener) as context:
        page = context.new_page()
        assert page.locator("a")._click(timeout=1000).reply_timeout() == pytest.approx(3.0)
        assert page.locator("a")._count().reply_timeout() == link.DEFAULT_TIMEOUT
        assert page._goto("https://hypeddit.com/")._asdict()["wait_ms"] == webpage.GOTO_TIMEOUT_MS


def test_every_waiting_and_immediate_operation_maps_to_its_command(world):
    listener, ext = world
    ext.handlers.update({
        "dom.count": lambda a: 2, "dom.visible": lambda a: True, "dom.attr": lambda a: None,
        "dom.text": lambda a: "text", "dom.value": lambda a: "7", "dom.click": lambda a: True,
        "dom.fill": lambda a: True, "dom.waitVisible": lambda a: True,
        "dom.all": lambda a: [{"attrs": {"href": "/a"}, "text": ""}],
        "tab.focus": lambda a: None,
    })
    with webpage.session(False, listener) as context:
        page = context.new_page()
        box = page.locator("input").first
        assert page.locator("a").count() == 2
        assert box.is_visible() is True
        assert box.get_attribute("min") is None
        assert box.inner_text() == "text" and box.text_content() == "text"
        assert box.input_value() == "7"
        assert box.click(timeout=100, force=True) is None
        assert box.fill(5, timeout=100) is None
        assert box.wait_for(state="visible", timeout=100) is None
        with pytest.raises(ValueError):
            box.wait_for(state="hidden")
        assert page.locator("a").all_texts_and_attrs(["href"], limit=9999) == [
            {"attrs": {"href": "/a"}, "text": ""}]
        assert page.bring_to_front() is None
    assert ext.commands("dom.fill")[0]["args"]["value"] == "5"
    assert ext.commands("dom.attr")[0]["args"]["name"] == "min"
    everything = ext.commands("dom.all")[0]["args"]
    assert everything["attrs"] == ["href"] and everything["text"] is True and everything["limit"] == 500
    assert ext.commands("dom.click")[0]["args"]["locator"] == [{"css": "input"}, {"first": True}]


# -- cancellation and the busy tab (plan K2) -----------------------------------------


def test_cancelled_command_keeps_the_tab_busy_until_the_late_reply(world):
    listener, ext = world
    ext.handlers["dom.text"] = lambda args: HOLD
    ext.handlers["dom.count"] = lambda args: 3

    async def scenario():
        async with webpage.async_session(False, listener) as context:
            page = await context.new_page()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(page.locator("#slow").inner_text(), 0.1)
            first = ext.commands("dom.text")[0]["id"]
            await asyncio.sleep(0.1)
            assert ext.cancels == [first]
            # A second wait_for on the same page: its command must not even be sent.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(page.locator("#other").inner_text(), 0.1)
            follow = asyncio.ensure_future(page.locator("#x").count())
            await asyncio.sleep(0.2)
            assert len(ext.commands("dom.text")) == 1 and not ext.commands("dom.count")
            assert not follow.done()
            ext.fail(first, "cancelled", "command cancelled")  # the late answer
            assert await asyncio.wait_for(follow, 3) == 3
            # another tab is never held up by this one
            other = await context.new_page()
            assert await other.locator("#y").count() == 3

    asyncio.run(scenario())


def test_close_and_focus_do_not_wait_for_a_hung_command(world):
    listener, ext = world
    ext.handlers["dom.text"] = lambda args: HOLD

    async def scenario():
        async with webpage.async_session(False, listener) as context:
            page = await context.new_page()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(page.locator("#slow").inner_text(), 0.1)
            await asyncio.wait_for(page.bring_to_front(), 3)
            await asyncio.wait_for(page.close(), 3)
            assert page.is_closed()

    asyncio.run(scenario())


def test_the_browser_going_away_ends_the_async_context(world):
    listener, ext = world
    def vanish(args):
        ext.client.close()  # from the serving thread, so the peer really sees the end
        return HOLD

    ext.handlers["dom.text"] = vanish

    async def scenario():
        context = await webpage.AsyncExtensionContext.open(False, listener)
        page = await context.new_page()
        closed = []
        context.on("close", closed.append)
        pending = asyncio.ensure_future(page.locator("#slow").inner_text())
        with pytest.raises(AutomationError, match="restarted"):
            await asyncio.wait_for(pending, 3)
        await asyncio.sleep(0.1)
        assert context.is_closed() and page.is_closed() and closed == [context]
        with pytest.raises(AutomationError):
            await page.goto("https://hypeddit.com/")
        await context.close()

    asyncio.run(scenario())


# -- events: order, thread, popups -------------------------------------------------------


def test_sync_events_are_dispatched_in_order_on_the_calling_thread(world):
    listener, ext = world
    seen = []
    here = threading.get_ident()

    def note(kind):
        return lambda *payload: seen.append((kind, threading.get_ident()))

    ext.handlers["dom.count"] = lambda a: 0
    with webpage.session(False, listener) as context:
        page = context.new_page()
        context.on("page", lambda popup: (note("page")(), popup.on("close", note("close"))))
        page.on("popup", note("popup"))
        ext.emit("tab.updated", tabId=page._tab_id, url="https://hypeddit.com/a")
        ext.emit("tab.created", tabId=500, openerTabId=page._tab_id, url="https://accounts.spotify.com/")
        ext.emit("tab.removed", tabId=500)
        time.sleep(0.2)
        assert seen == [] and page.url == "about:blank"  # nothing runs behind the caller's back
        page.locator("a").count()  # a command dispatches what is queued
        assert [kind for kind, _ in seen] == ["page", "popup", "close"]
        assert {thread for _, thread in seen} == {here}
        assert page.url == "https://hypeddit.com/a"
        ext.emit("tab.updated", tabId=page._tab_id, url="https://hypeddit.com/b")
        page.wait_for_timeout(150)
        assert page.url == "https://hypeddit.com/b"


def test_wait_for_timeout_waits_the_whole_time_and_fails_on_a_closed_page(world):
    listener, ext = world
    with webpage.session(False, listener) as context:
        page = context.new_page()
        started = time.monotonic()
        ext.emit("tab.updated", tabId=page._tab_id, url="https://hypeddit.com/a")  # an event ends no wait
        page.wait_for_timeout(300)
        assert time.monotonic() - started >= 0.29
        ext.emit("tab.removed", tabId=page._tab_id)
        with pytest.raises(AutomationError, match="closed"):
            page.wait_for_timeout(2000)
        with pytest.raises(AutomationError, match="closed"):
            page.wait_for_timeout(10)


def test_popup_of_an_owned_tab_becomes_a_page_with_both_callbacks(world):
    listener, ext = world
    with webpage.session(False, listener) as context:
        page = context.new_page()
        pages, popups = [], []
        context.on("page", pages.append)
        page.on("popup", popups.append)
        ext.emit("tab.created", tabId=700, openerTabId=12345, url="https://x.test/")  # not ours
        ext.emit("tab.created", tabId=501, openerTabId=page._tab_id, url="https://accounts.spotify.com/")
        page.wait_for_timeout(100)
        assert len(pages) == len(popups) == 1 and pages[0] is popups[0]
        popup = popups[0]
        assert popup.url == "https://accounts.spotify.com/" and popup.opener() is page
        assert page.opener() is None and context.pages == [page, popup]
        ext.emit("tab.removed", tabId=page._tab_id)
        time.sleep(0.1)
        context._pump()
        assert popup.opener() is None  # Playwright: no opener once it is closed
        ext.emit("tab.removed", tabId=501)
        time.sleep(0.1)
        context._pump()
        assert popup.is_closed() and context.pages == []


def test_new_page_fires_the_context_page_event(world):
    listener, _ext = world
    with webpage.session(False, listener) as context:
        pages = []
        context.on("page", pages.append)
        page = context.new_page()
        assert pages == [page]
        with pytest.raises(ValueError):
            context.on("response", print)
        with pytest.raises(ValueError):
            page.on("console", print)


def test_async_events_are_dispatched_on_the_event_loop(world):
    listener, ext = world

    async def scenario():
        loop_thread = threading.get_ident()
        async with webpage.async_session(False, listener) as context:
            page = await context.new_page()
            threads = []
            page.on("popup", lambda popup: threads.append(threading.get_ident()))
            ext.emit("tab.created", tabId=502, openerTabId=page._tab_id, url="https://hypeddit.com/")
            for _ in range(100):
                if threads:
                    break
                await asyncio.sleep(0.02)
            assert threads == [loop_thread] and len(context.pages) == 2

    asyncio.run(scenario())


# -- downloads --------------------------------------------------------------------------


def _announce(ext, page, download_id, path, **fields):
    ext.emit("download.created", downloadId=download_id, tabId=page._tab_id, danger="safe")
    ext.emit("download.done", downloadId=download_id, path=str(path), filename=str(path),
             bytes=3, mime="audio/mpeg", **fields)


def test_only_an_announced_download_reaches_the_page_and_can_be_saved(world, tmp_path):
    listener, ext = world
    source = tmp_path / "Downloads" / "track.mp3"
    source.parent.mkdir()
    source.write_bytes(b"ID3" + bytes(64))
    with webpage.session(False, listener) as context:
        page = context.new_page()
        got = []
        page.on("download", got.append)
        ext.emit("download.done", downloadId=99, path=str(source))  # never announced
        ext.emit("download.created", downloadId=98, tabId=424242, danger="safe")  # not our tab
        ext.emit("download.done", downloadId=98, path=str(source))
        ext.emit("download.created", downloadId=97, tabId=page._tab_id, danger="safe")
        ext.emit("download.done", downloadId=97, path="relative/track.mp3")  # not absolute
        _announce(ext, page, 7, source)
        ext.emit("download.done", downloadId=7, path=str(source))  # a second done: already consumed
        page.wait_for_timeout(200)
        assert len(got) == 1 and got[0].suggested_filename == "track.mp3" and got[0].page is page

        target = tmp_path / "out.part"
        got[0].save_as(target)
        assert target.read_bytes() == source.read_bytes()
        assert ext.commands("download.discard")[0]["args"] == {"downloadId": 7}


def test_a_download_of_a_popup_belongs_to_the_popup_and_blocked_ones_are_never_accepted(world, tmp_path):
    listener, ext = world
    source = tmp_path / "a.zip"
    source.write_bytes(b"PK")
    with webpage.session(False, listener) as context:
        page = context.new_page()
        popups = []
        page.on("popup", popups.append)
        ext.emit("tab.created", tabId=503, openerTabId=page._tab_id, url="https://hypeddit.com/")
        page.wait_for_timeout(100)
        popup = popups[0]
        parent, child, blocked, failed = [], [], [], []
        page.on("download", parent.append)
        popup.on("download", child.append)
        popup.on("download_blocked", blocked.append)
        popup.on("download_failed", failed.append)
        _announce(ext, popup, 11, source)
        ext.emit("download.created", downloadId=12, tabId=popup._tab_id, danger="unsafe")
        ext.emit("download.blocked", downloadId=12, tabId=popup._tab_id, reason="danger: unsafe")
        ext.emit("download.done", downloadId=12, path=str(source))
        ext.emit("download.created", downloadId=13, tabId=popup._tab_id, danger="safe")
        ext.emit("download.failed", downloadId=13, error="NETWORK_FAILED")
        ext.emit("download.done", downloadId=13, path=str(source))
        page.wait_for_timeout(200)
        assert [d.id for d in child] == [11] and parent == []
        assert [b["reason"] for b in blocked] == ["danger: unsafe"]
        assert [f["error"] for f in failed] == ["NETWORK_FAILED"]


def test_save_as_refuses_symlinks_oversized_and_missing_files(world, tmp_path, monkeypatch):
    listener, ext = world
    real = tmp_path / "real.mp3"
    real.write_bytes(b"ID3abcdef")
    with webpage.session(False, listener) as context:
        page = context.new_page()
        got = []
        page.on("download", got.append)
        link_path = tmp_path / "link.mp3"
        if POSIX:
            link_path.symlink_to(real)
            _announce(ext, page, 1, link_path)
        _announce(ext, page, 2, real)
        _announce(ext, page, 3, tmp_path / "gone.mp3")
        _announce(ext, page, 4, tmp_path)  # a directory
        page.wait_for_timeout(200)
        by_id = {d.id: d for d in got}
        if POSIX:
            with pytest.raises(AutomationError, match="symlink"):
                by_id[1].save_as(tmp_path / "x1")
        with pytest.raises(AutomationError, match="cannot be read"):
            by_id[3].save_as(tmp_path / "x3")
        with pytest.raises(AutomationError, match="not a regular file"):
            by_id[4].save_as(tmp_path / "x4")
        monkeypatch.setattr(files, "MAX_DOWNLOAD_BYTES", 3)
        with pytest.raises(AutomationError, match="exceeded"):
            by_id[2].save_as(tmp_path / "x2")
        assert not ext.commands("download.discard")  # nothing was copied, nothing is discarded
        assert not (tmp_path / "x2").exists()


def test_files_save_browser_download_accepts_the_adapter_download(world, tmp_path):
    listener, ext = world
    source = tmp_path / "Downloads" / "Some Track.WAV"
    source.parent.mkdir()
    source.write_bytes(b"RIFF" + bytes(200))
    with webpage.session(False, listener) as context:
        page = context.new_page()
        got = []
        page.on("download", got.append)
        _announce(ext, page, 5, source)
        page.wait_for_timeout(200)
        track = Track(title="Some Track", artist="A", permalink_url="https://soundcloud.com/a/b", id=1)
        kept = files.save_browser_download(got[0], track, tmp_path / "crate", None)
        assert kept.suffix == ".wav" and kept.read_bytes().startswith(b"RIFF")
        assert ext.commands("download.discard")


def test_async_download_save_as(world, tmp_path):
    listener, ext = world
    source = tmp_path / "t.mp3"
    source.write_bytes(b"ID3xyz")

    async def scenario():
        async with webpage.async_session(False, listener) as context:
            page = await context.new_page()
            got = []
            page.on("download", got.append)
            _announce(ext, page, 6, source)
            for _ in range(100):
                if got:
                    break
                await asyncio.sleep(0.02)
            await got[0].save_as(tmp_path / "copy")
            assert (tmp_path / "copy").read_bytes() == b"ID3xyz"
            assert ext.commands("download.discard")

    asyncio.run(scenario())


# -- content, evaluate, screenshot, cookies ------------------------------------------


def test_truncated_content_is_the_store_parse_error(world, tmp_path):
    listener, ext = world
    ext.handlers["tab.html"] = lambda a: {"html": "<p>hi</p>", "truncated": False}
    ext.handlers["tab.capture"] = lambda a: {"png": "iVBORw0K"}
    with webpage.session(False, listener) as context:
        page = context.new_page()
        assert page.content() == "<p>hi</p>"
        ext.handlers["tab.html"] = lambda a: {"html": "", "truncated": True}
        with pytest.raises(AutomationError, match="store page is too large to inspect safely"):
            page.content()
        shot = tmp_path / "shot.png"
        assert page.screenshot(path=str(shot), full_page=False) == shot.read_bytes()


def _bandcamp_scripts():
    tree = ast.parse((ROOT / "dj_digger" / "stores" / "bandcamp.py").read_text(encoding="utf-8"))
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate" and node.args and isinstance(node.args[0], ast.Constant)
    ]


def test_evaluate_accepts_only_the_two_bandcamp_scripts(world):
    listener, ext = world
    ext.handlers["dom.call"] = lambda a: {"name": a["name"]}
    scripts = _bandcamp_scripts()
    assert len(scripts) == 2
    dom_js = (ROOT / "dj_digger" / "webext" / "extension" / "dom.js").read_text(encoding="utf-8")
    with webpage.session(False, listener) as context:
        page = context.new_page()
        names = {page.evaluate(script)["name"] for script in scripts}
        assert names == {"bandcampTralbum", "bandcampSuggestedPrice"}
        assert all(f"function {name}(" in dom_js for name in names)
        for refused in ("() => document.cookie", scripts[0] + "; fetch('x')", ""):
            with pytest.raises(ValueError):
                page.evaluate(refused)
        with pytest.raises(ValueError):
            page.evaluate(scripts[0], 1)
    assert len(ext.commands("dom.call")) == 2


def test_clear_cookies_refuses_every_other_name(world):
    listener, ext = world
    ext.handlers["cookie.clearGateDownload"] = lambda a: {"removed": 1}
    with webpage.session(False, listener) as context:
        for bad in ({"name": "oauth_token"}, {}, {"name": "filedownloading", "domain": "x"}):
            with pytest.raises(ValueError):
                context.clear_cookies(**bad)
        assert not ext.commands("cookie.clearGateDownload")
        page = context.new_page()
        assert page.context.clear_cookies(name="filedownloading") is None
        assert len(ext.commands("cookie.clearGateDownload")) == 1


def test_cookies_only_offer_the_soundcloud_token(world):
    listener, ext = world
    tokens = iter(["secret-token", None])
    ext.handlers["cookie.soundcloudToken"] = lambda a: next(tokens)
    with webpage.session(False, listener) as context:
        assert context.cookies(["https://soundcloud.com"]) == [
            {"name": "oauth_token", "value": "secret-token", "domain": ".soundcloud.com", "path": "/"}]
        assert context.cookies(["https://soundcloud.com"]) == []
        for bad in (None, [], ["https://example.com"], ["https://soundcloud.com", "https://spotify.com"]):
            with pytest.raises(ValueError):
                context.cookies(bad)


# -- locator steps and regular expressions ----------------------------------------------


def test_locator_chaining_and_xpath(world):
    listener, ext = world
    ext.handlers["dom.count"] = lambda a: 0
    with webpage.session(False, listener) as context:
        page = context.new_page()
        page.locator("li.item").nth(2).locator("xpath=ancestor::h4[1]").first.count()
        assert step_list(ext, "dom.count") == [
            {"css": "li.item"}, {"nth": 2}, {"xpath": "ancestor::h4[1]"}, {"first": True}]
        page.get_by_placeholder("Search for artist, album, or track").count()
        assert step_list(ext, "dom.count") == [
            {"placeholder": "Search for artist, album, or track", "exact": False}]
        page.get_by_role("link", name="Buy", exact=True).locator("span").count()
        assert step_list(ext, "dom.count") == [
            {"role": "link", "name": "Buy", "exact": True}, {"css": "span"}]
        page.get_by_role("button").count()
        assert step_list(ext, "dom.count") == [{"role": "button"}]
        page.get_by_text("Add to cart", exact=True).count()
        assert step_list(ext, "dom.count") == [{"text": "Add to cart", "exact": True}]


def _regex_calls(path):
    """Every ``re.compile`` whose result reaches get_by_role(name=), get_by_text() or _role_pair()."""

    tree = ast.parse(path.read_text(encoding="utf-8"))

    def is_compile(node):
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "compile" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re")

    named = {
        target.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and is_compile(node.value)
        for target in node.targets if isinstance(target, ast.Name)
    }
    patterns = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        arguments = {
            "get_by_role": [kw.value for kw in node.keywords if kw.arg == "name"],
            "get_by_text": node.args[:1],
            "_role_pair": node.args[1:2],
        }.get(name, [])
        for argument in arguments:
            call = argument if is_compile(argument) else named.get(getattr(argument, "id", None))
            if call is not None:
                flags = 0
                if len(call.args) > 1:
                    flags = eval(compile(ast.Expression(call.args[1]), "<flags>", "eval"), {"re": re})
                patterns[(ast.literal_eval(call.args[0]), flags)] = re.compile(
                    ast.literal_eval(call.args[0]), flags)
    return list(patterns.values())


BANDCAMP_PATTERNS = _regex_calls(ROOT / "dj_digger" / "stores" / "bandcamp.py")
GATE_PATTERNS = _regex_calls(ROOT / "dj_digger" / "gates" / "browser.py")
ALL_PATTERNS = BANDCAMP_PATTERNS + GATE_PATTERNS


def test_the_walker_finds_the_bandcamp_patterns():
    sources = {pattern.pattern for pattern in BANDCAMP_PATTERNS}
    assert {r"^accept necessary only$", r"^buy digital (?:track|album)$", r"^add to cart$",
            r"^(remove|x)$", r"^buy the full digital album$"} <= sources


@pytest.mark.parametrize("pattern", ALL_PATTERNS, ids=[p.pattern for p in ALL_PATTERNS])
def test_every_store_and_gate_regex_becomes_a_javascript_step(world, pattern):
    listener, ext = world
    ext.handlers["dom.count"] = lambda a: 1
    with webpage.session(False, listener) as context:
        page = context.new_page()
        page.get_by_role("button", name=pattern).count()
        assert step_list(ext, "dom.count") == [
            {"role": "button", "name": pattern.pattern, "flags": "i" if pattern.flags & re.I else ""}]
        page.get_by_text(pattern, exact=True).count()
        assert step_list(ext, "dom.count") == [
            {"text": pattern.pattern, "flags": "i" if pattern.flags & re.I else ""}]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_store_and_gate_regex_is_a_valid_javascript_regexp():
    sources = json.dumps([p.pattern for p in ALL_PATTERNS])
    program = "for (const p of JSON.parse(process.argv[1])) new RegExp(p, 'i')"
    subprocess.run(["node", "-e", program, sources], check=True, timeout=30)


@pytest.mark.parametrize(
    "bad",
    [re.compile("a", re.DOTALL), re.compile("a", re.IGNORECASE | re.MULTILINE), re.compile("a", re.ASCII),
     re.compile("(?P<n>a)"), re.compile(r"\Aa"), re.compile("a\\Z"), re.compile("(?i)a")],
)
def test_other_regex_flags_and_python_only_syntax_are_refused(world, bad):
    listener, ext = world
    with webpage.session(False, listener) as context:
        page = context.new_page()
        with pytest.raises(ValueError):
            page.get_by_role("button", name=bad)
        with pytest.raises(ValueError):
            page.get_by_text(bad)
        with pytest.raises(ValueError):
            page.get_by_placeholder(bad)


# -- context: window, session factory, bandcamp loops -------------------------------------


def test_session_factory_opens_a_window_and_closes_it(world):
    listener, ext = world
    ext.handlers["window.show"] = lambda a: None
    ext.handlers["window.hide"] = lambda a: None
    closed = []
    with webpage.session(True, listener) as context:
        context.on("close", closed.append)
        assert ext.commands("window.open")[0]["args"] == {"visible": True} and context.visible
        context.hide()
        assert not context.visible
        context.show()
        assert context.visible and not context.is_closed()
    assert context.is_closed() and closed == [context]
    ext.wait_for("window.close")
    context.close()  # idempotent
    assert len(ext.commands("window.close")) == 1


def test_a_failing_window_open_raises_and_leaves_nothing_behind(world):
    listener, ext = world
    ext.handlers["window.open"] = raising("no_window", "no")
    with pytest.raises(webpage.PageError):
        webpage.ExtensionContext.open(False, listener)
    assert listener.connections()[0]._sessions == []


def _product_page(ext):
    ext.handlers["tab.goto"] = lambda a: {"url": "https://artist.bandcamp.com/track/signal", "status": 200}
    ext.handlers["dom.call"] = lambda a: {"title": "", "id": "", "currency": ""}
    ext.handlers["dom.text"] = raising("timeout", "timeout")
    ext.handlers["dom.all"] = lambda a: [
        {"attrs": {"href": "/track/signal?x=1"}, "text": " Signal "},
        {"attrs": {"href": "https://evil.test/track/a"}, "text": "No"},
    ]


def test_bandcamp_dom_products_makes_one_dom_all_call(world):
    listener, ext = world
    _product_page(ext)

    async def scenario():
        async with webpage.async_session(False, listener) as context:
            page = await context.new_page()
            await page.goto("https://artist.bandcamp.com/track/signal")
            return await bandcamp_adapter._bandcamp_dom_products(page)

    products = asyncio.run(scenario())
    assert [p.url for p in products] == ["https://artist.bandcamp.com/track/signal?x=1"]
    assert len(ext.commands("dom.all")) == 1
    args = ext.commands("dom.all")[0]["args"]
    assert args["attrs"] == ["href"] and args["locator"] == [{"css": 'a[href*="/track/"]'}]
    assert args["limit"] == 500


def test_bandcamp_search_candidates_make_one_dom_all_call(world):
    listener, ext = world
    ext.handlers["dom.count"] = lambda a: 1
    ext.handlers["dom.visible"] = lambda a: True
    ext.handlers["dom.fill"] = lambda a: True
    ext.handlers["tab.goto"] = lambda a: {"url": "https://bandcamp.com/", "status": 200}
    ext.handlers["dom.all"] = lambda a: [
        {"attrs": {"href": "https://artist.bandcamp.com/track/signal?from=search"},
         "text": "Signal\nby Artist"},
        {"attrs": {"href": "https://artist.bandcamp.com/album/lp?from=search"}, "text": "LP"},
    ]

    async def scenario():
        async with webpage.async_session(False, listener) as context:
            page = await context.new_page()
            await page.goto("https://bandcamp.com/")
            return await bandcamp_adapter._bandcamp_search_candidates_async(
                page, Track(title="Signal", artist="Artist", permalink_url="https://soundcloud.com/a/b", id=1),
                asyncio.Event())

    tracks, albums = asyncio.run(scenario())
    assert [t.title for t in tracks] == ["Signal"] and tracks[0].artist == "Artist"
    assert albums == ["https://artist.bandcamp.com/album/lp"]
    assert len(ext.commands("dom.all")) == 1 and ext.commands("dom.all")[0]["args"]["limit"] == 40

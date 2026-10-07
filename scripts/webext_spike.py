#!/usr/bin/env python3
"""Throwaway spike kit for phase F0 of docs/implementation/browser-extension-plan.md.

Run it by hand, with the GUI and every other dj-digger command closed (it takes the
same InstanceLock), after ``uv run dj-digger browser register`` and loading the
extension.  It starts the app side of the channel (``link.Listener``), waits for the
extension's ``hello`` and then sends raw protocol commands, printing every command,
reply and event with a timestamp.  See docs/implementation/browser-extension-spike.md.

    uv run python scripts/webext_spike.py hello
    uv run python scripts/webext_spike.py open --visible https://hypeddit.com/...
    uv run python scripts/webext_spike.py probe-gate https://hypeddit.com/...
    uv run python scripts/webext_spike.py probe-store https://www.beatport.com/release/...

Safety: store pages are only read (``probe-store`` never clicks), ``probe-gate`` asks
y/N before every click and never types an email unless ``--email`` is given, and
nothing prints a cookie value or a whole page.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dj_digger.automation_errors import AutomationError
from dj_digger.gates import browser as gate
from dj_digger.gates.providers import CLICK_THROUGH_STEPS, DIRECT_STEP
from dj_digger.instance import InstanceLock
from dj_digger.paths import data_dir
from dj_digger.store_parse import products_from_html
from dj_digger.webext import link

# Selectors mirrored from dj_digger/stores/bandcamp.py (not imported: that module is
# under active change and a spike must not depend on it).
BUY = r"^buy digital (?:track|album)$"
ADD = r"^add to cart$"
LOGIN = r"^(log ?in|sign ?in)$"
BANDCAMP = {
    "buy control (button)": [{"role": "button", "name": BUY, "flags": "i"}],
    "buy control (link)": [{"role": "link", "name": BUY, "flags": "i"}],
    "buy control (text)": [{"text": BUY, "flags": "i", "exact": True}],
    "price input": [{"css": 'input#userPrice, input[name="userPrice"]'}],
    "add to cart (button)": [{"role": "button", "name": ADD, "flags": "i"}],
    "add to cart (text)": [{"text": ADD, "flags": "i", "exact": True}],
    "sidecart": [{"css": "#sidecart"}],
    "sidecart rows": [{"css": "#sidecartContents #item_list .item, #item_list .item"}],
    "cart link": [{"css": '[data-test="mb-cart"] a[title="cart"], a[href="https://bandcamp.com/cart"]'}],
    "cart counter": [{"css": '[data-test="mb-cart"] .menubar-cart-icon text'}],
}
BEATPORT = {
    "links to /cart": [{"css": 'a[href*="/cart"]'}],
    "cart-ish elements": [
        {"css": '[class*="cart" i], [data-testid*="cart" i], [aria-label*="cart" i]'}
    ],
}
ANY_STORE = {
    "anchors /track/": [{"css": 'a[href*="/track/"]'}],
    "log in / sign in links": [{"role": "link", "name": LOGIN, "flags": "i"}],
}
BUTTONISH = re.compile(r"cart|buy|add|purchase|basket|\d[.,]\d\d", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HELP = "commands: show | hide | info | timers | quit   (Ctrl+C also quits)"


# ------------------------------------------------------------------ output


def say(text: str) -> None:
    now = time.time()
    stamp = f"{time.strftime('%H:%M:%S', time.localtime(now))}.{int(now * 1000) % 1000:03d}"
    print(f"{stamp}  {text}", flush=True)


def clip(value: Any, limit: int = 400) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else f"{text[:limit]}...(+{len(text) - limit} chars)"


def shown(cmd: str, result: Any) -> Any:
    """What may be printed of a result: never a cookie value, never a whole page."""

    if cmd.startswith("cookie.") and cmd != "cookie.clearGateDownload":
        return "<present, value not printed>" if result else "<none>"
    if cmd == "tab.html" and isinstance(result, dict):
        return {"bytes": len(result.get("html") or ""), "truncated": result.get("truncated")}
    if cmd == "tab.capture" and isinstance(result, dict):
        return {"png_base64_chars": len(result.get("png") or "")}
    return result


def ask(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


# ------------------------------------------------------------------ rig


class Rig:
    """One session plus the little state the probes share."""

    def __init__(self, session: Any, args: argparse.Namespace, hello: dict | None = None):
        self.session = session
        self.args = args
        self.hello = hello or {}
        self.tab: int | None = None
        self.window: int | None = None
        self.clicked_at: float | None = None
        self.stop = threading.Event()

    def call(self, cmd: str, **args: Any) -> Any:
        started = time.monotonic()
        wait = args.get("timeoutMs", 15000) / 1000 + 2
        hidden = {k: ("<hidden>" if k == "value" else v) for k, v in args.items()}
        say(f"-> {cmd} {clip(hidden, 300)}")
        try:
            result = self.session.request(cmd, args, timeout=wait)
        except AutomationError as exc:
            ms = (time.monotonic() - started) * 1000
            say(f"<- {cmd} FAILED {getattr(exc, 'code', '')} {exc} ({ms:.0f} ms)")
            raise
        say(f"<- {cmd} ok ({(time.monotonic() - started) * 1000:.0f} ms) {clip(shown(cmd, result))}")
        return result

    def attempt(self, cmd: str, **args: Any) -> Any:
        """Like call, but a failed command is printed and answered with None."""

        try:
            return self.call(cmd, **args)
        except AutomationError:
            return None

    def dom(self, op: str, locator: list, **args: Any) -> Any:
        return self.attempt(f"dom.{op}", tabId=self.tab, locator=locator, **args)

    def click(self, locator: list) -> bool:
        self.clicked_at = time.monotonic()
        return self.dom("click", locator, timeoutMs=5000) is not None

    def pump(self) -> None:
        while not self.stop.is_set():
            try:
                event = self.session.next_event(0.2)
            except AutomationError as exc:
                say(f"!! session ended: {exc}")
                return
            if event is None:
                continue
            since = ""
            if self.clicked_at is not None:
                since = f"   (+{time.monotonic() - self.clicked_at:.2f}s after the last click)"
            say(f"EVENT {clip(event, 500)}{since}")


@contextlib.contextmanager
def work_window(rig: Rig, visible: bool):
    rig.window = rig.call("window.open", visible=visible)["windowId"]
    threading.Thread(target=rig.pump, daemon=True).start()
    try:
        yield
    finally:
        rig.stop.set()
        if not rig.args.keep:
            rig.attempt("window.close", windowId=rig.window)


def open_tab(rig: Rig, url: str) -> None:
    rig.tab = rig.call("tab.new", windowId=rig.window)["tabId"]
    result = rig.call("tab.goto", tabId=rig.tab, url=url, timeoutMs=30000)
    if "/hot-or-not/" in str(result.get("url")):
        # Hypeddit's first visit after a download lands on its poll; the app goes back once too.
        result = rig.call("tab.goto", tabId=rig.tab, url=url, timeoutMs=30000)
    say(f"loaded: status={result.get('status')} url={result.get('url')}")


def timers(rig: Rig) -> None:
    """R2: a wait for an element that never appears should take 2.0 s; throttled timers add up to 1 s."""

    for _ in range(5):
        started = time.monotonic()
        rig.attempt("dom.waitVisible", tabId=rig.tab, locator=[{"css": "#djd-timer-probe"}], timeoutMs=2000)
        say(f"timer probe: {time.monotonic() - started:.2f}s (2.0x = not throttled, 3.x = throttled)")


def idle(rig: Rig) -> None:
    """Keep printing events and take a few commands until quit / Ctrl+C."""

    say(f"watching events.  {HELP}")
    while True:
        try:
            line = input("spike> ").strip().lower()
        except EOFError:
            return
        try:
            if line in ("quit", "q", "exit"):
                return
            if line in ("show", "hide"):
                rig.call(f"window.{line}", windowId=rig.window)
            elif line == "info" and rig.tab is not None:
                rig.call("tab.info", tabId=rig.tab)
            elif line == "timers" and rig.tab is not None:
                timers(rig)
            elif line:
                say(HELP)
        except AutomationError:
            pass  # call() already printed it


# ------------------------------------------------------------------ commands


def cmd_hello(rig: Rig, args: argparse.Namespace) -> None:
    say(f"hello: {clip(rig.hello, 1000)}")
    say(f"permissions granted to the extension: {rig.hello.get('permissions')}")
    token = rig.attempt("cookie.soundcloudToken")
    say(f"SoundCloud oauth_token present in this browser: {bool(token)} (value never printed)")


def cmd_reload(rig: Rig, args: argparse.Namespace) -> None:
    rig.call("extension.reload")
    say("extension reloading from disk; it says hello again within a few seconds")


def cmd_open(rig: Rig, args: argparse.Namespace) -> None:
    with work_window(rig, args.visible):
        open_tab(rig, args.url)
        rig.attempt("tab.info", tabId=rig.tab)
        idle(rig)


def cmd_window(rig: Rig, args: argparse.Namespace) -> None:
    rig.attempt(f"window.{args.action}", windowId=args.window_id)


def cmd_close(rig: Rig, args: argparse.Namespace) -> None:
    rig.attempt("window.close", windowId=args.window_id)


def _locate(*css: str) -> list:
    return [*({"css": c} for c in css), {"first": True}]


def _slide_state(rig: Rig) -> dict | None:
    slides = [{"css": gate.GATE_CURRENT_SLIDE}]
    if not rig.dom("count", slides):
        return None
    slide = _locate(gate.GATE_CURRENT_SLIDE)

    def count(selector: str) -> int:
        return rig.dom("count", [*slide, {"css": selector}]) or 0

    classes = (rig.dom("attr", slide, name="class", timeoutMs=2000) or "").split()
    state = {
        "kind": next((c for c in classes if c in gate.STEP_KINDS), ""),
        "classes": classes,
        "group": rig.dom("attr", slide, name="data-group", timeoutMs=2000) or "",
        "pending": count(gate.GATE_PENDING_ACTION),
        "next": count(gate.GATE_NEXT_BUTTON),
        "connect": count(gate.GATE_CONNECT_BUTTON),
        "email": count(gate.GATE_EMAIL_INPUT),
        "name": count(gate.GATE_NAME_INPUT),
        "submit": count(gate.GATE_EMAIL_SUBMIT),
    }
    say(f"SLIDE {clip(state, 600)}")
    return state


def _group(rig: Rig) -> str:
    return rig.dom("attr", _locate(gate.GATE_CURRENT_SLIDE), name="data-group", timeoutMs=500) or ""


def _await_next_slide(rig: Rig, group: str, seconds: float = 15) -> None:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if _group(rig) not in ("", group):
            say(f"slide moved on after {time.monotonic() - started:.1f}s")
            return
        time.sleep(0.25)
    say(f"slide did NOT move on within {seconds:.0f}s")


def _gate_download(rig: Rig) -> None:
    button = _locate(gate.HYPEDDIT_DOWNLOAD_BUTTON)
    if not rig.dom("visible", button):
        say("download button not visible")
        return
    if not ask("click the gate's DOWNLOAD button? It saves a file into the browser's download folder "
               "(first clears the 'filedownloading' cookie of Hypeddit, as the app does)"):
        return
    rig.attempt("cookie.clearGateDownload")
    if rig.click(button):
        say("download clicked: watch for download.created / download.done (R5); "
            "also look at chrome://downloads or about:downloads")


def _step(rig: Rig, args: argparse.Namespace, state: dict) -> bool:
    """One step, with a y/N before every click. False stops the walk."""

    kind, slide = state["kind"], _locate(gate.GATE_CURRENT_SLIDE)
    if kind == DIRECT_STEP:
        _gate_download(rig)
        return False
    if kind in gate.GATE_CONNECT_STEPS:
        if not state["connect"] or not ask(
            f"click Connect ({gate.GATE_CONNECT_STEPS[kind]})? [side effect: if Hypeddit is already "
            "authorised on YOUR real account it follows/saves there; opens the OAuth popup]"
        ):
            return False
        rig.click([*slide, {"css": gate.GATE_CONNECT_BUTTON}, {"first": True}])
    elif kind in CLICK_THROUGH_STEPS and state["pending"]:
        if not ask(f"click the first pending follow/like link of the '{kind}' slide? "
                   "[side effect: may follow/like on YOUR real account; may open a popup]"):
            return False
        rig.click([*slide, {"css": gate.GATE_PENDING_ACTION}, {"first": True}])
        time.sleep(1.5)
        return True
    elif kind in CLICK_THROUGH_STEPS:
        if not state["next"] or not ask(f"click Next on the '{kind}' slide?"):
            return False
        rig.click([*slide, {"css": gate.GATE_NEXT_BUTTON}, {"first": True}])
    elif kind == "email":
        if rig.dom("visible", _locate(gate.GATE_CAPTCHA)):
            say("CAPTCHA visible on the email step: stop here (a person must solve it)")
            return False
        if not args.email:
            say("email slide reached; not filled (pass --email to let the probe fill and submit it)")
            return False
        if not ask("fill the email field (and submit it)? [side effect: Hypeddit gets that address]"):
            return False
        if state["name"] and args.name:
            rig.dom("fill", [*slide, {"css": gate.GATE_NAME_INPUT}, {"first": True}],
                    value=args.name, timeoutMs=5000)
        rig.dom("fill", [*slide, {"css": gate.GATE_EMAIL_INPUT}, {"first": True}],
                value=args.email, timeoutMs=5000)
        rig.click([*slide, {"css": gate.GATE_EMAIL_SUBMIT}, {"first": True}])
    else:
        say(f"slide kind {kind!r} is not one the app knows: stop here")
        return False
    _await_next_slide(rig, state["group"])
    return True


def cmd_probe_gate(rig: Rig, args: argparse.Namespace) -> None:
    with work_window(rig, args.visible):
        open_tab(rig, args.url)
        start = _locate(gate.GATE_START_BUTTON)
        say(f"start button: count={rig.dom('count', start[:1])} visible={rig.dom('visible', start)}")
        if rig.dom("visible", start) and ask(
            "click the start button (#downloadProcess)? It only reveals the step slides"
        ):
            rig.click(start)
            time.sleep(1.0)
        for _ in range(gate.MAX_GATE_STEPS):
            state = _slide_state(rig)
            if state is None:
                say("no current slide; looking for a download button")
                _gate_download(rig)
                break
            if not _step(rig, args, state):
                break
        idle(rig)


def _html_report(html: str, url: str, store: str) -> None:
    ids = {m.group(2): m.group(1) for m in re.finditer(r'href="[^"]*?/track/([^"/?#]+)/(\d+)', html)}
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    say(f"html: <title>={title.group(1).strip()[:80] if title else ''!r}")
    say(f"anchors /track/<slug>/<id>: {len(ids)} unique ids, first: {list(ids.items())[:5]}")
    blocks = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I)
    types = []
    for block in blocks:
        with contextlib.suppress(ValueError):
            data = json.loads(block)
            types.append([d.get("@type") for d in (data if isinstance(data, list) else [data])
                          if isinstance(d, dict)])
    say(f"ld+json blocks: {len(blocks)}, @type per block: {types}")
    nxt = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not nxt:
        say("__NEXT_DATA__: absent")
    else:
        try:
            found = _trackish(json.loads(nxt.group(1)))
            say(f"__NEXT_DATA__: {len(nxt.group(1))} chars; track-like lists: {found[:8]}")
        except ValueError:
            say("__NEXT_DATA__: present but not JSON")
    try:
        products = products_from_html(html, url, store)
        say(f"app parser (products_from_html): {len(products)} products, first: "
            f"{[(p.product_id, p.title[:40], str(p.price)) for p in products[:5]]}")
    except AutomationError as exc:
        say(f"app parser (products_from_html) refused: {type(exc).__name__}: {exc}")


def _trackish(node: Any, path: str = "$") -> list:
    """(path, count, sample) of lists whose items look like store tracks."""

    found = []
    if isinstance(node, list):
        items = [x for x in node if isinstance(x, dict) and "id" in x
                 and any(k in x for k in ("slug", "mix_name", "isrc", "bpm", "name", "title"))]
        if items:
            found.append((path, len(items), [(x["id"], str(x.get("name") or x.get("title"))[:30])
                                             for x in items[:2]]))
        for i, item in enumerate(node[:50]):
            found += _trackish(item, f"{path}[{i}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            found += _trackish(value, f"{path}.{key}")
    return found


def cmd_probe_store(rig: Rig, args: argparse.Namespace) -> None:
    host = urlparse(args.url).hostname or ""
    store = "bandcamp" if host.endswith("bandcamp.com") else "beatport" if host.endswith("beatport.com") else ""
    if not store:
        raise SystemExit("probe-store needs a bandcamp.com or beatport.com URL")
    with work_window(rig, args.visible):
        open_tab(rig, args.url)
        for label, locator in {**ANY_STORE, **(BANDCAMP if store == "bandcamp" else BEATPORT)}.items():
            say(f"count {label}: {rig.dom('count', locator)}")
        if store == "beatport":
            _beatport_candidates(rig)
        else:  # MAIN-world, read-only named functions of dom.js (R6/R9)
            for name in ("bandcampTralbum", "bandcampSuggestedPrice"):
                rig.attempt("dom.call", tabId=rig.tab, name=name)
        page = rig.attempt("tab.html", tabId=rig.tab) or {}
        if page.get("truncated"):
            say("tab.html truncated: the page is above the 2 MB limit")
        _html_report(page.get("html", ""), args.url, store)
        if args.save and page.get("html"):
            Path(args.save).write_text(EMAIL.sub("user@example.invalid", page["html"]), encoding="utf-8")
            Path(args.save).chmod(0o600)
            say(f"saved to {args.save} (emails scrubbed; names, account ids and tokens are NOT: "
                "review before it goes into tests/fixtures)")
        rig.attempt("tab.info", tabId=rig.tab)


def _beatport_candidates(rig: Rig) -> None:
    attrs = ["aria-label", "data-testid", "title", "id", "class"]
    buttons = rig.dom("all", [{"role": "button"}], attrs=attrs, text=True, limit=150) or []
    say(f"buttons on the page: {len(buttons)}; the cart/buy/price-looking ones:")
    for button in buttons:
        a, text = button["attrs"], " ".join((button["text"] or "").split())
        if BUTTONISH.search(f"{text} {a.get('aria-label')} {a.get('data-testid')}"):
            say(f"   button text={text[:50]!r} aria-label={a['aria-label']!r} "
                f"data-testid={a['data-testid']!r} class={str(a['class'])[:60]!r}")
    cartish = rig.dom("all", BEATPORT["cart-ish elements"], attrs=[*attrs, "href"], text=True, limit=20) or []
    for element in cartish:
        a = element["attrs"]
        say(f"   cart-ish text={' '.join((element['text'] or '').split())[:50]!r} "
            f"testid={a['data-testid']!r} aria-label={a['aria-label']!r} href={a['href']!r}")


# ------------------------------------------------------------------ main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--browser", choices=["chrome", "firefox", "edge"],
                        help="use the connection that said hello as this browser (default: most recent)")
    parser.add_argument("--wait", type=float, default=30, help="seconds to wait for the extension's hello")
    parser.add_argument("--keep", action="store_true", help="leave the work window open on exit")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func, *, url: bool = False, visible: bool = False, help: str = ""):
        p = sub.add_parser(name, help=help)
        p.set_defaults(func=func)
        if url:
            p.add_argument("url")
        if visible:
            p.add_argument("--visible", action="store_true", help="open the work window visible (default: hidden)")
        return p

    add("hello", cmd_hello, help="print the hello and whether a SoundCloud token exists")
    add("reload", cmd_reload, help="reload the (unpacked) extension from disk")
    add("open", cmd_open, url=True, visible=True, help="open a page, stream events")
    gate_p = add("probe-gate", cmd_probe_gate, url=True, visible=True,
                 help="walk a Hypeddit gate, y/N before each click")
    gate_p.add_argument("--email", help="only then the email step is filled and submitted")
    gate_p.add_argument("--name", help="name for gates that ask for one (with --email)")
    store_p = add("probe-store", cmd_probe_store, url=True, visible=True, help="read-only store page probe")
    store_p.add_argument("--save", metavar="PATH", help="write the page HTML (emails scrubbed) to PATH")
    win = add("window", cmd_window, help="show or hide the leftover work window (after --keep)")
    win.add_argument("action", choices=["show", "hide"])
    win.add_argument("window_id", type=int, help="windowId printed by window.open")
    close_p = add("close", cmd_close, help="close the leftover work window")
    close_p.add_argument("window_id", type=int, help="windowId printed by window.open")
    return parser


def pick(listener: link.Listener, browser: str | None, wait: float) -> link.Connection:
    say(f"listening on {listener.address}; waiting up to {wait:.0f}s for the extension "
        f"(browser={browser or 'any'})")
    deadline = time.monotonic() + wait
    while True:
        for conn in listener.connections():
            if conn.compatible and browser in (None, (conn.hello or {}).get("browser")):
                return conn
        if time.monotonic() >= deadline:
            seen = [(c.hello or {}).get("browser") for c in listener.connections()]
            raise link.ExtensionMissing(
                f"no matching extension said hello (seen: {seen}). Check: `uv run dj-digger browser register` "
                "ran, the extension is loaded (welcome.html), then reload the extension and retry"
            )
        time.sleep(0.25)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        lock = InstanceLock(data_dir() / "instance.lock")
    except RuntimeError as exc:
        print(f"{exc}: close the GUI and other dj-digger commands first", file=sys.stderr)
        return 2
    listener = link.Listener()
    try:
        listener.start()
        conn = pick(listener, args.browser, args.wait)
        say(f"connected to {conn.hello.get('browser')} {conn.hello.get('browserVersion')}")
        with conn.session() as session:
            args.func(Rig(session, args, conn.hello), args)
    except KeyboardInterrupt:
        say("interrupted")
    except AutomationError as exc:
        say(f"stopped: {exc}")
        return 1
    finally:
        listener.close()
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

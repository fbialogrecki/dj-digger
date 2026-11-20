# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.52,<2"]
# ///
"""Exercise the built site's consent flow in isolated Chromium; intercept all external traffic."""
import os
import shutil
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


class SiteHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def wait_js(page, predicate):
    # Playwright's wait_for_function evaluates code in the page. Keep the site's strict CSP enabled.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if page.evaluate(predicate):
            return
        time.sleep(0.05)
    raise AssertionError(predicate)


def check(browser, origin):
    context = browser.new_context(locale="pl-PL", permissions=["clipboard-read", "clipboard-write"])
    external = []
    errors = []

    def intercept(route):
        url = urlsplit(route.request.url)
        if url.hostname == "127.0.0.1":
            return route.continue_()
        external.append(url.hostname)
        if url.hostname == "www.googletagmanager.com" and url.path == "/gtag/js":
            return route.fulfill(content_type="text/javascript", body="document.cookie='_ga=test; Path=/';")
        if url.hostname == "github.com":
            return route.fulfill(status=204)
        route.abort()

    context.route("**/*", intercept)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(origin + "/pl/download/")
    panel = page.locator("#consent")
    expect(panel).to_be_visible()
    measurement_id = panel.get_attribute("data-ga")
    assert not external and not context.cookies()
    assert page.evaluate("Object.keys(sessionStorage).length === 0 && Object.keys(localStorage).length === 0")
    page.locator('[data-choice="deny"]').focus()
    page.keyboard.press("Enter")
    expect(panel).to_be_hidden()
    page.reload()
    expect(page.locator("[data-settings]")).to_be_visible()
    expect(panel).to_be_hidden()
    assert not external
    other = context.new_page()
    other.goto(origin + "/de/privacy/")
    expect(other.locator("#consent")).to_be_hidden()
    page.locator("[data-settings]").click()
    assert page.evaluate("document.activeElement.id") == "consent-title"
    page.locator('[data-choice="allow"]').click()
    for tab in (page, other):
        wait_js(tab, "document.cookie.includes('_ga=test')")
        assert tab.evaluate("dataLayer.filter(x => x[0] === 'config' && x[2]?.send_page_view !== false).length") == 1
        assert tab.evaluate("dataLayer.every(x => x[1] !== 'page_view')")
        assert tab.evaluate("""dataLayer.some((x, i) => x[0] === 'set' && x[1].cookie_update === false
            && x[1].cookie_expires === 180*86400 && x[1].cookie_domain === 'none'
            && i < dataLayer.findIndex(y => y[0] === 'js'))""")
    assert external == ["www.googletagmanager.com"] * 2
    page.locator("#tab-linux").click()
    page.locator("#linux [data-copy]").click()
    wait_js(page, "dataLayer.some(x => x[1] === 'copy_linux_command')")
    page.locator("#tab-windows").click()
    page.locator('[data-installer="windows-x64"]').click()
    assert page.evaluate("dataLayer.filter(x => x[1] === 'download_windows_x64').length") == 1
    page.locator("[data-settings]").click()
    page.locator('[data-choice="deny"]').click()
    for tab in (page, other):
        wait_js(tab, "!window.dataLayer && !document.cookie.includes('_ga=')")
        expect(tab.locator("#consent")).to_be_hidden()
    # An expired choice prompts again; a quota error must not preserve an earlier opt-in.
    page.evaluate("""const key = 'djdAnalyticsConsent';
        localStorage.setItem(key, JSON.stringify({...JSON.parse(localStorage.getItem(key)), choice:'allow', until:1}))""")
    page.reload()
    expect(panel).to_be_visible()
    page.locator('[data-choice="allow"]').click()
    wait_js(page, "document.cookie.includes('_ga=test')")
    page.evaluate("() => { Storage.prototype.setItem = () => { throw new DOMException('quota', 'QuotaExceededError') } }")
    page.locator("[data-settings]").click()
    page.locator('[data-choice="deny"]').click()
    for tab in (page, other):
        wait_js(tab, "!window.dataLayer && !document.cookie.includes('_ga=')")
    assert not errors, errors
    context.close()

    # A public DNS hostname can hold separate host-only and domain-scoped GA cookies.
    legacy = browser.new_context()
    def local_site(route):
        url = urlsplit(route.request.url)
        assert url.hostname == "site.example.test"
        route.fulfill(response=route.fetch(url=origin + url.path))
    legacy.route("**/*", local_site)
    for domain in ("site.example.test", ".site.example.test"):
        legacy.add_cookies([{"name": name, "value": "legacy", "domain": domain, "path": "/"}
                            for name in ("_ga", "_ga_" + measurement_id[2:])])
    tab = legacy.new_page()
    tab.goto("https://site.example.test/pl/privacy/")
    expect(tab.locator("#consent")).to_be_visible()
    assert not legacy.cookies()
    legacy.close()

    # A disabled storage backend and a no-JS browser must still be able to download.
    private = browser.new_context(locale="de-DE", color_scheme="dark", is_mobile=True, has_touch=True,
                                  viewport={"width": 375, "height": 740})
    private.route("**/*", intercept)
    private.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw Error('blocked')}})")
    tab = private.new_page()
    tab.goto(origin + "/de/download/")
    expect(tab.locator("#consent")).to_be_visible()
    for button in tab.locator("#consent button").all():
        box = button.bounding_box()
        assert box and 0 <= box["x"] and box["x"] + box["width"] <= 375
    tab.locator('[data-choice="allow"]').click()
    assert tab.evaluate("!window.dataLayer")
    # The cropped record must stay in view while a phone scrolls, without widening the page.
    assert tab.evaluate("document.documentElement.scrollWidth === 375 && innerWidth === 375")
    disc = tab.locator(".record .disc")
    tab.evaluate("scrollTo({top: 480, left: 1000, behavior: 'instant'})")
    wait_js(tab, "scrollY === 480 && scrollX === 0")
    record = tab.locator(".record")
    top = record.bounding_box()["y"]
    assert 0 <= top < 740
    angle = disc.evaluate("el => getComputedStyle(el).rotate")
    tab.evaluate("scrollTo({top: 800, behavior: 'instant'})")
    wait_js(tab, f"scrollY === 800 && getComputedStyle(document.querySelector('.disc')).rotate !== '{angle}'")
    assert abs(record.bounding_box()["y"] - top) < 1
    menu = tab.locator("#site-menu")
    toggle = tab.locator(".menu-toggle")
    expect(menu).to_be_hidden()
    toggle.tap()
    first_link = menu.locator(".links a").first
    expect(first_link).to_be_focused()
    first_link.tap(trial=True)  # Wait for the slide-in animation before measuring.
    box = menu.bounding_box()
    assert box["x"] >= 44 and abs(box["x"] + box["width"] - 375) < 1
    assert not toggle.inner_text().strip() and toggle.get_attribute("aria-label")
    expect(menu.locator(".theme")).to_be_visible()
    menu.locator(".theme").tap()
    assert tab.locator("html").get_attribute("data-theme") == "light"
    tab.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    expect(toggle).to_be_focused()
    toggle.tap()
    menu.locator(".lang summary").tap()
    menu.locator('[data-lang="pl"]').tap()
    expect(tab).to_have_url(origin + "/pl/download/")
    toggle.tap()
    tab.set_viewport_size({"width": 1280, "height": 800})
    expect(toggle).to_be_hidden()
    expect(menu.locator(".links a").first).to_be_visible()
    assert not tab.locator("main").evaluate("el => !!el.closest('[inert]')")
    tab.set_viewport_size({"width": 375, "height": 740})
    expect(menu).to_be_hidden()
    toggle.tap()
    first_link.tap(trial=True)
    tab.touchscreen.tap(20, 30)  # Above the underlying home link.
    expect(menu).to_be_hidden()
    expect(tab).to_have_url(origin + "/pl/download/")
    toggle.tap()
    first_link.tap(trial=True)
    touch = private.new_cdp_session(tab)

    def swipe(x, y, dx, dy):
        for step in range(6):
            touch.send("Input.dispatchTouchEvent", {
                "type": "touchStart" if step == 0 else "touchMove",
                "touchPoints": [{"x": x + dx * step / 5, "y": y + dy * step / 5}],
            })
        touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    tab.set_viewport_size({"width": 375, "height": 460})
    menu.locator(".lang summary").tap()
    swipe(300, 300, 10, -160)
    expect(menu).to_be_visible()
    wait_js(tab, "document.querySelector('#site-menu').scrollTop > 0")
    first_link.scroll_into_view_if_needed()
    box = first_link.bounding_box()
    swipe(box["x"] + 12, box["y"] + box["height"] / 2, 90, 4)
    expect(menu).to_be_hidden()
    expect(tab).to_have_url(origin + "/pl/download/")
    private.close()
    nojs = browser.new_context(java_script_enabled=False)
    tab = nojs.new_page()
    tab.goto(origin + "/es/download/")
    expect(tab.locator('[data-installer="macos-arm64"]')).to_be_visible()
    expect(tab.locator("#consent")).to_be_hidden()
    tab.set_viewport_size({"width": 375, "height": 740})
    tab.locator(".menu-toggle").click()
    tab.mouse.click(24, 30)
    expect(tab.locator("#site-menu")).to_be_hidden()
    tab.locator(".menu-toggle").click()
    expect(tab).to_have_url(origin + "/es/download/")
    tab.locator("#site-menu .lang summary").click()
    tab.locator('#site-menu [data-lang="de"]').click()
    expect(tab).to_have_url(origin + "/de/download/")
    nojs.close()


if __name__ == "__main__":
    directory = Path(__file__).resolve().parents[1] / "dist"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SiteHandler, directory=directory))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            executable = os.environ.get("CHROME_BIN") or shutil.which("google-chrome") or shutil.which("chromium-browser") or shutil.which("chromium")
            browser = playwright.chromium.launch(executable_path=executable)
            check(browser, f"http://127.0.0.1:{server.server_port}")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    print("Consent, downloads, mobile navigation, blocked storage and no-JS checks passed.")

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
        assert tab.evaluate("dataLayer.filter(x => x[1] === 'page_view').length") == 1
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
    page.evaluate("localStorage.setItem('djdAnalyticsConsent', JSON.stringify({choice:'allow',revision:1,until:1}))")
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

    # A disabled storage backend and a no-JS browser must still be able to download.
    private = browser.new_context(locale="de-DE", color_scheme="dark", viewport={"width": 375, "height": 740})
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
    private.close()
    nojs = browser.new_context(java_script_enabled=False)
    tab = nojs.new_page()
    tab.goto(origin + "/es/download/")
    expect(tab.locator('[data-installer="macos-arm64"]')).to_be_visible()
    expect(tab.locator("#consent")).to_be_hidden()
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
    print("Consent, downloads, withdrawal, blocked storage and no-JS checks passed.")

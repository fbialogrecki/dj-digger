"""Which links are allowed to reach the operating system, and which are not."""

import pytest

from dj_digger import browser


class RecordingController:
    """Stands in for a real browser, so a test never opens a tab."""

    def __init__(self) -> None:
        self.opened = []

    def open_new_tab(self, url):
        self.opened.append(url)
        return True


WEB_LINKS = [
    "https://bandcamp.com/album/x",
    "http://example.com/",
    "https://example.com:8443/path?q=1#frag",
    "HTTPS://EXAMPLE.COM/shouting",
]

# Everything a purchase_url, a scraped anchor or a summary file might carry that
# is not a web page. Each one asks the OS for something it should not be asked.
NOT_WEB_LINKS = [
    "javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "file:///etc/passwd",
    "file://bandcamp.com/etc/passwd",
    r"\\attacker\share\payload",
    "//evil.com/protocol-relative",
    "ftp://example.com/x",
    "C:/Windows/System32/calc.exe",
    "http:///etc/passwd",
    "",
    "   ",
]


@pytest.mark.parametrize("url", WEB_LINKS)
def test_web_links_are_openable(url):
    assert browser.is_openable(url) is True


@pytest.mark.parametrize("url", NOT_WEB_LINKS)
def test_anything_that_is_not_a_web_link_is_refused(url):
    assert browser.is_openable(url) is False


def test_open_url_refuses_without_even_resolving_a_browser(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("a refused link must not reach the browser layer")

    monkeypatch.setattr(browser, "resolve_controller", explode)
    assert browser.open_url("file:///etc/passwd") is False


def test_open_urls_skips_the_bad_ones_and_still_opens_the_rest():
    controller = RecordingController()
    errors = []
    urls = [
        "https://bandcamp.com/a",
        "file:///etc/passwd",
        "https://beatport.com/b",
        r"\\attacker\share",
    ]

    opened = browser.open_urls(
        urls, pause=0, controller=controller, on_error=errors.append
    )

    assert controller.opened == ["https://bandcamp.com/a", "https://beatport.com/b"]
    assert opened == 2
    assert len(errors) == 2
    assert "/etc/passwd" in errors[0]

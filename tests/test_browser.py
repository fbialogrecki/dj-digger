"""Which links are allowed to reach the operating system, and which are not."""

import subprocess

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


def test_a_browser_named_in_the_config_file_is_never_executed_unchecked(monkeypatch):
    """webbrowser.get takes a command line, not just a name.

    `webbrowser.get("/bin/sh -c evil %s")` returns something that runs it, so a
    config file - a plain text file anyone can edit or sync - must not be able
    to name the program we launch. Only a value this machine reported gets by.
    """

    monkeypatch.setattr(browser, "available_browsers", lambda: [("", "System default")])

    for hostile in ("/bin/sh -c 'touch /tmp/pwned' %s", "/bin/echo PWNED %s", "netscape"):
        assert browser.resolve_choice(hostile) == browser.SYSTEM_DEFAULT


def test_a_browser_the_machine_reported_is_kept(monkeypatch):
    monkeypatch.setattr(
        browser, "available_browsers", lambda: [("", "System default"), ("firefox", "Firefox")]
    )

    assert browser.resolve_choice("firefox") == "firefox"


def test_the_old_default_spelling_still_means_the_system_default():
    assert browser.resolve_choice("default") == browser.SYSTEM_DEFAULT
    assert browser.resolve_choice("") == browser.SYSTEM_DEFAULT


def test_the_system_default_is_always_offered_and_comes_first():
    offered = browser.available_browsers()

    assert offered[0] == ("", "System default")
    assert len({value for value, _ in offered}) == len(offered), "no duplicates"


def test_wsl_is_recognised_from_the_environment(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Debian")
    assert browser.is_wsl() is True


def test_wsl_is_recognised_from_proc_version(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    version = tmp_path / "version"
    version.write_text("Linux version 6.6-microsoft-standard-WSL2", encoding="utf-8")
    monkeypatch.setattr(browser, "Path", lambda _p: version)

    assert browser.is_wsl() is True


def test_a_plain_linux_box_is_not_wsl(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    version = tmp_path / "version"
    version.write_text("Linux version 6.6.0-generic", encoding="utf-8")
    monkeypatch.setattr(browser, "Path", lambda _p: version)

    assert browser.is_wsl() is False


def test_under_wsl_the_windows_browser_is_offered(monkeypatch):
    monkeypatch.setattr(browser, "is_wsl", lambda: True)
    monkeypatch.setattr(browser.shutil, "which", lambda name: "/usr/bin/wslview" if name == "wslview" else None)

    offered = dict((value, label) for value, label in browser.available_browsers())

    assert browser.WINDOWS in offered
    assert "wslview" in offered[browser.WINDOWS]


def test_handing_a_link_to_windows_never_goes_through_a_shell(monkeypatch):
    """A URL is attacker-controlled text; a shell would make that a command."""

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["shell"] = kwargs.get("shell")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(browser, "_windows_opener", lambda: ["wslview"])
    monkeypatch.setattr(browser.subprocess, "run", fake_run)

    assert browser._open_on_windows("https://bandcamp.com/a") is True
    assert seen["command"] == ["wslview", "https://bandcamp.com/a"]
    assert seen["shell"] is False


def test_a_windows_choice_still_refuses_a_link_that_is_not_a_web_address(monkeypatch):
    monkeypatch.setattr(browser, "resolve_choice", lambda _c: browser.WINDOWS)
    monkeypatch.setattr(
        browser, "_open_on_windows", lambda url: pytest.fail(f"{url} should not get here")
    )

    assert browser.open_url("file:///etc/passwd", browser.WINDOWS) is False

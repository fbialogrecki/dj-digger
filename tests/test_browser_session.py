"""Starting, classifying and installing the managed Chromium."""

import os
import signal
from threading import Event

import pytest

from dj_digger import browser_session


def test_store_profile_lives_in_private_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    path = browser_session.store_profile_path()

    assert path == tmp_path / "dj-digger" / "store-browser"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o700


class FakeBrowserContext:
    def set_default_timeout(self, _timeout):
        pass

    def close(self):
        pass


class FakeChromium:
    def __init__(self, executable_path):
        self.executable_path = str(executable_path)
        self.launched = False

    def launch_persistent_context(self, *_args, **_kwargs):
        self.launched = True
        return FakeBrowserContext()


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class FakePlaywrightManager:
    def __init__(self, chromium):
        self.playwright = FakePlaywright(chromium)

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


def test_browser_context_does_not_reclassify_errors_after_launch(tmp_path, monkeypatch):
    import playwright.sync_api

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(FakeChromium(executable)),
    )

    with pytest.raises(RuntimeError, match="downstream mentioned playwright install"):
        with browser_session.sync_browser_context(tmp_path):
            raise RuntimeError("downstream mentioned playwright install")


def test_browser_context_reports_a_missing_chromium_before_launch(tmp_path, monkeypatch):
    import playwright.sync_api

    chromium = FakeChromium(tmp_path / "missing-chromium")
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(chromium),
    )

    with pytest.raises(browser_session.ChromiumMissing):
        with browser_session.sync_browser_context(tmp_path):
            pass

    assert not chromium.launched


def test_chromium_installer_uses_the_current_python_without_a_shell(monkeypatch):
    calls = []

    class FinishedInstaller:
        returncode = 0

        def poll(self):
            return self.returncode

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FinishedInstaller()

    monkeypatch.setattr(browser_session.subprocess, "Popen", popen)
    monkeypatch.setattr(
        browser_session.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("the cancellable installer must use Popen"),
    )

    browser_session.install_chromium(Event())

    popen_options = {
        "stdout": browser_session.subprocess.DEVNULL,
        "stderr": browser_session.subprocess.DEVNULL,
        "shell": False,
    }
    if os.name == "nt":
        popen_options["creationflags"] = browser_session.subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    assert calls == [
        (
            [browser_session.sys.executable, "-m", "playwright", "install", "chromium"],
            popen_options,
        )
    ]


def test_chromium_installer_terminates_its_child_when_cancelled(monkeypatch):
    cancel = Event()

    class RunningInstaller:
        returncode = None
        terminated = False
        polls = 0
        pid = 123
        signals = []

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                cancel.set()
                return None
            self.returncode = 0
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def send_signal(self, sent):
            self.signals.append(sent)
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    process = RunningInstaller()
    monkeypatch.setattr(browser_session.subprocess, "Popen", lambda *_args, **_kwargs: process)
    group_signals = []
    if os.name != "nt":
        def killpg(pid, sent):
            group_signals.append((pid, sent))
            process.returncode = -15

        monkeypatch.setattr(browser_session.os, "killpg", killpg)

    with pytest.raises(browser_session.AutomationError, match="cancelled"):
        browser_session.install_chromium(cancel)

    assert not process.terminated
    if os.name == "nt":
        assert process.signals == [signal.CTRL_BREAK_EVENT]
    else:
        assert group_signals == [(process.pid, signal.SIGTERM)]


class BrokenChromium(FakeChromium):
    def launch_persistent_context(self, *_args, **_kwargs):
        raise RuntimeError("missing shared library")


class MissingLibrariesChromium(FakeChromium):
    def launch_persistent_context(self, *_args, **_kwargs):
        raise RuntimeError("please run playwright install-deps")


def test_linux_launch_failure_explains_how_to_install_system_dependencies(
    tmp_path, monkeypatch
):
    import playwright.sync_api

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(browser_session.sys, "platform", "linux")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(BrokenChromium(executable)),
    )

    with pytest.raises(browser_session.AutomationError, match=r"install --with-deps chromium"):
        with browser_session.sync_browser_context(tmp_path):
            pass


def test_install_deps_message_is_not_mistaken_for_a_missing_browser(tmp_path, monkeypatch):
    import playwright.sync_api

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(browser_session.sys, "platform", "linux")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(MissingLibrariesChromium(executable)),
    )

    with pytest.raises(browser_session.AutomationError) as caught:
        with browser_session.sync_browser_context(tmp_path):
            pass

    assert not isinstance(caught.value, browser_session.ChromiumMissing)
    assert "install --with-deps chromium" in str(caught.value)


def test_a_locked_profile_is_retried_before_it_is_reported(monkeypatch):
    import asyncio

    attempts = []

    class Context:
        def set_default_timeout(self, _timeout):
            pass

    class Chromium:
        executable_path = __file__

        async def launch_persistent_context(self, *_args, **_kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("ProcessSingleton: user data directory is already in use")
            return Context()

    class Playwright:
        chromium = Chromium()

    monkeypatch.setattr(browser_session, "PROFILE_LOCK_WAIT", 0)

    asyncio.run(browser_session.launch_persistent_context(Playwright(), None, headless=True))

    assert len(attempts) == 3


def test_the_viewer_needs_a_display_and_gets_the_cookies(monkeypatch):
    import asyncio

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(browser_session.sys, "platform", "linux")

    with pytest.raises(browser_session.AutomationError, match="desktop window"):
        asyncio.run(browser_session.launch_viewer(object(), []))

    monkeypatch.setenv("DISPLAY", ":1")
    added = []

    class Context:
        async def add_cookies(self, cookies):
            added.extend(cookies)

        def set_default_timeout(self, _timeout):
            pass

    class Browser:
        async def new_context(self, **_kwargs):
            return Context()

    class Chromium:
        executable_path = __file__

        async def launch(self, **kwargs):
            assert kwargs["headless"] is False
            return Browser()

    class Playwright:
        chromium = Chromium()

    asyncio.run(browser_session.launch_viewer(Playwright(), [{"name": "a", "value": "b"}]))
    assert added == [{"name": "a", "value": "b"}]

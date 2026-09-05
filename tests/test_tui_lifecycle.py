"""Leaving the crate browser: ctrl+c, and what happens when a thread will not stop."""

import signal
import threading
from threading import Event

from dj_digger import tui


def test_run_tui_returns_normally_when_no_thread_lingers(monkeypatch, no_real_exit):
    monkeypatch.setattr(tui.DiggerApp, "run", lambda self: None)

    tui.run_tui(grace=0.05)

    assert no_real_exit == []


def test_run_tui_forces_exit_when_a_thread_lingers(monkeypatch, no_real_exit):
    release = Event()
    started = Event()

    def stuck_worker():
        started.set()
        release.wait(5)

    def run(self):
        threading.Thread(target=stuck_worker, name="stuck-download", daemon=False).start()
        started.wait(2)

    monkeypatch.setattr(tui.DiggerApp, "run", run)
    try:
        tui.run_tui(grace=0.05)
    finally:
        release.set()

    assert no_real_exit == [0]
    no_real_exit.clear()  # the exit was the point; conftest's teardown expects none


def test_a_sigint_after_the_terminal_is_restored_hard_exits(monkeypatch, no_real_exit):
    before = signal.getsignal(signal.SIGINT)

    def run(self):
        # What a second ctrl+c does once Textual has given the terminal back.
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr(tui.DiggerApp, "run", run)
    tui.run_tui(grace=0.05)

    assert no_real_exit == [130]
    no_real_exit.clear()
    assert signal.getsignal(signal.SIGINT) is before, "the handler is put back"


def test_shutdown_deadline_runs_while_asyncio_is_draining_io(monkeypatch, no_real_exit):
    from textual.app import App

    release, entered = Event(), Event()

    def blocked():
        entered.set()
        release.wait(3)

    class Probe(tui.DiggerApp):
        async def on_mount(self):
            self.run_worker(self.services.io(blocked))
            self.set_timer(.03, self.exit)

        async def on_unmount(self):
            self.shutdown_started()
            self.services.stop()

        def run(self):
            return App.run(self, headless=True)

    def timed_exit(code):
        no_real_exit.append(code)
        release.set()

    monkeypatch.setattr(tui, "DiggerApp", Probe)
    monkeypatch.setattr(tui, "HARD_EXIT", timed_exit)
    try:
        tui.run_tui(grace=.1)
        assert entered.is_set()
        assert no_real_exit == [0]
    finally:
        release.set()
        no_real_exit.clear()

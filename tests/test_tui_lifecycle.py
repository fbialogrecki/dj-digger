"""Leaving the crate browser: ctrl+c, and what happens when a thread will not stop."""

import signal
import threading
from threading import Event

import pytest

from dj_digger import tui


@pytest.fixture(autouse=True)
def no_real_exit(monkeypatch):
    """A regression here must fail a test, not end the pytest process."""

    exits = []
    monkeypatch.setattr(tui, "HARD_EXIT", lambda code: exits.append(code))
    return exits


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


def test_a_sigint_after_the_terminal_is_restored_hard_exits(monkeypatch, no_real_exit):
    before = signal.getsignal(signal.SIGINT)

    def run(self):
        # What a second ctrl+c does once Textual has given the terminal back.
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr(tui.DiggerApp, "run", run)
    tui.run_tui(grace=0.05)

    assert no_real_exit == [130]
    assert signal.getsignal(signal.SIGINT) is before, "the handler is put back"

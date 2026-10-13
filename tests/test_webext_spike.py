"""Offline checks of the F0 spike kit: argument parsing and its safety promises."""

from __future__ import annotations

import os

import pytest

from dj_digger.webext import link
from scripts import webext_spike as spike

SECRET = "SECRET-COOKIE-VALUE"
EMAIL = "someone@example.com"
RELEASE = f"""<html><head><title>Release</title></head><body>
<a href="https://www.beatport.com/track/some-track/123456">Some Track</a>
<p>{EMAIL} {SECRET}</p></body></html>"""


class FakeSession:
    """Answers the commands the probes send; remembers every one."""

    def __init__(self, html: str = RELEASE):
        self.html = html
        self.calls: list[tuple[str, dict]] = []
        self.clicks = 0

    def names(self) -> list[str]:
        return [cmd for cmd, _ in self.calls]

    def request(self, cmd, args, timeout=15):
        self.calls.append((cmd, args))
        if cmd == "dom.click":
            self.clicks += 1
            return True
        if cmd == "dom.attr" and args["name"] == "class":
            return "fangate-slider-content " + ("sp" if self.clicks == 0 else "email")
        if cmd == "dom.attr" and args["name"] == "data-group":
            return f"g{self.clicks}"
        if cmd == "dom.count":
            return 1
        if cmd == "dom.visible":
            return False
        return {
            "window.open": {"windowId": 1},
            "tab.new": {"tabId": 7},
            "tab.goto": {"url": "https://www.beatport.com/release/x/1", "status": 200},
            "tab.html": {"html": self.html, "truncated": False},
            "cookie.soundcloudToken": SECRET,
            "dom.all": [],
        }.get(cmd)

    def next_event(self, timeout=None):
        return None


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(spike, "idle", lambda rig: None)
    monkeypatch.setattr(spike, "ask", lambda prompt: False)


def run(argv, session=None):
    session = session or FakeSession()
    args = spike.build_parser().parse_args(argv)
    args.func(spike.Rig(session, args, {"browser": "chrome", "permissions": True}), args)
    return session


def test_every_subcommand_parses():
    parse = spike.build_parser().parse_args
    assert parse(["hello"]).func is spike.cmd_hello
    assert parse(["--browser", "firefox", "open", "--visible", "https://hypeddit.com/x"]).visible
    gate = parse(["probe-gate", "https://hypeddit.com/x", "--email", "a@b.c", "--name", "N"])
    assert (gate.email, gate.name, gate.visible) == ("a@b.c", "N", False)
    assert parse(["probe-store", "https://bandcamp.com/x", "--save", "p.html"]).save == "p.html"
    assert parse(["window", "show", "5"]).action == "show"
    assert parse(["close", "5"]).func is spike.cmd_close
    with pytest.raises(SystemExit):
        parse(["window", "explode", "5"])


def test_hello_never_prints_the_token(capsys):
    run(["hello"])
    out = capsys.readouterr().out
    assert "True" in out and SECRET not in out


def test_probe_store_reads_only_and_prints_no_page_content(capsys, tmp_path):
    saved = tmp_path / "page.html"
    session = run(["probe-store", "https://www.beatport.com/release/x/1", "--save", str(saved)])
    out = capsys.readouterr().out
    assert not {"dom.click", "dom.fill"} & set(session.names())
    assert "123456" in out  # ids parsed locally from tab.html are fine
    assert SECRET not in out
    assert EMAIL not in out and EMAIL not in saved.read_text()
    if os.name == "posix":  # Windows has no owner-only mode bits
        assert oct(saved.stat().st_mode & 0o777) == "0o600"


def test_probe_store_bandcamp_only_reads_named_page_data():
    session = run(["probe-store", "https://artist.bandcamp.com/track/x"])
    names = session.names()
    assert "dom.call" in names and not {"dom.click", "dom.fill"} & set(names)


def test_probe_store_refuses_other_hosts():
    with pytest.raises(SystemExit):
        run(["probe-store", "https://example.com/"])


def test_probe_gate_clicks_nothing_without_a_yes():
    session = run(["probe-gate", "https://hypeddit.com/x"])
    assert not {"dom.click", "dom.fill", "cookie.clearGateDownload"} & set(session.names())


def test_probe_gate_never_fills_the_email_without_the_flag(monkeypatch):
    monkeypatch.setattr(spike, "ask", lambda prompt: True)
    session = run(["probe-gate", "https://hypeddit.com/x"])
    assert session.clicks == 1  # the Spotify slide's Connect, then the email slide stops the walk
    assert "dom.fill" not in session.names()


def test_a_filled_value_is_hidden_when_the_command_is_echoed(capsys):
    args = spike.build_parser().parse_args(["probe-gate", "https://hypeddit.com/x", "--email", EMAIL])
    rig = spike.Rig(FakeSession(), args)
    rig.call("dom.fill", tabId=7, locator=[{"css": "#email_address"}], value=EMAIL)
    assert EMAIL not in capsys.readouterr().out


def test_missing_extension_is_reported_not_raised(monkeypatch):
    class Empty:
        address = "x"

        def connections(self):
            return []

    monkeypatch.setattr(spike.time, "sleep", lambda s: None)
    with pytest.raises(link.ExtensionMissing):
        spike.pick(Empty(), None, 0)


def test_window_commands_carry_the_window_id():
    session = run(["open", "https://hypeddit.com/x"])
    sent = {cmd: args for cmd, args in session.calls}
    assert sent["tab.new"] == {"windowId": 1}
    assert sent["window.close"] == {"windowId": 1}

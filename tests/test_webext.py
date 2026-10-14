"""Offline tests for the browser-extension channel: framing, relay, socket, registration."""

import base64
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from multiprocessing import connection as mpc
from pathlib import Path

import pytest

from dj_digger.automation_errors import AutomationError
from dj_digger.webext import host, link, register

POSIX = os.name != "nt"
ROOT = Path(__file__).resolve().parents[1]


def frame(obj) -> bytes:
    data = json.dumps(obj).encode()
    return struct.pack("=I", len(data)) + data


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    """Never reach the real HOME or runtime directory (the conftest covers XDG data/config)."""
    for name in ("HOME", "USERPROFILE", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "home"))


@pytest.fixture
def address():
    if not POSIX:
        yield rf"\\.\pipe\dj-digger-test-{uuid.uuid4().hex}"
        return
    # short path: sun_path is only 104 bytes on macOS, pytest's tmp_path can exceed it
    with tempfile.TemporaryDirectory(prefix="djw", dir="/tmp") as directory:
        yield os.path.join(directory, "webext.sock")


OFFLINE = struct.pack("=I", 23) + b'{"event":"app-offline"}'
KEY = b"k" * 32
HELLO = {"event": "hello", "browser": "chrome", "version": "1", "protocol": 1, "instance": "a"}


@pytest.fixture
def listener(address):
    with link.Listener(address, KEY) as listener:
        yield listener


def connect(address, hello=HELLO, key=KEY):
    client = mpc.Client(address, authkey=key)
    if hello is not None:
        client.send_bytes(json.dumps(hello).encode())
    return client


def recv(client, timeout=3):
    assert client.poll(timeout), "nothing received"
    return json.loads(client.recv_bytes())


# -- Native Messaging framing and relay --------------------------------------


def test_native_messaging_frames_round_trip():
    stream = io.BytesIO()
    host.write_frame(stream, b'{"a":1}')
    host.write_frame(stream, "żółć".encode())
    stream.seek(0)
    assert host.read_frame(stream) == b'{"a":1}'
    assert host.read_frame(stream) == "żółć".encode()
    assert host.read_frame(stream) is None


def test_truncated_frame_is_eof():
    assert host.read_frame(io.BytesIO(struct.pack("=I", 10) + b"abc")) is None
    assert host.read_frame(io.BytesIO(b"\x01")) is None


def test_host_returns_on_stdin_eof_without_touching_stdout():
    stdout = io.BytesIO()
    assert host.run(io.BytesIO(), stdout) == 0
    assert stdout.getvalue() in (b"", OFFLINE)  # the retry thread may speak once


def test_host_process_exits_on_stdin_eof(tmp_path):
    home = str(tmp_path)
    env = {
        **os.environ,
        "HOME": home, "USERPROFILE": home, "XDG_CONFIG_HOME": home, "XDG_DATA_HOME": home,
        "XDG_RUNTIME_DIR": home, "XDG_STATE_HOME": home, "PYTHONPATH": str(ROOT),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "dj_digger.webext.host"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    try:
        process.stdin.close()
        assert process.wait(10) == 0
        # no app and no authkey: the only thing it may have said is the offline event
        out = process.stdout.read()
        assert out in (b"", OFFLINE)
    finally:
        process.kill()
        for stream in (process.stdout, process.stderr):
            stream.close()


def test_oversized_app_frames_are_refused_not_forwarded():
    stdout = io.BytesIO()
    relay = host.Relay(stdout)

    class App:
        def __init__(self, frames):
            self.frames = list(frames)

        def recv_bytes(self):
            if not self.frames:
                raise EOFError
            return self.frames.pop(0)

    big = json.dumps({"id": 7, "cmd": "x", "args": {"p": "x" * link.MAX_TO_BROWSER}}).encode()
    unnumbered = b"x" * (link.MAX_TO_BROWSER + 1)
    relay._pump(App([big, unnumbered, b'{"id":8,"ok":true}']))
    stdout.seek(0)
    first = json.loads(host.read_frame(stdout))
    second = json.loads(host.read_frame(stdout))
    third = json.loads(host.read_frame(stdout))
    assert first["id"] == 7 and first["ok"] is False and first["error"]["code"] == "frame-too-large"
    assert second["event"] == "host-error"
    assert third == {"id": 8, "ok": True}
    assert host.read_frame(stdout) is None


def test_host_relays_both_ways_and_replays_hello(listener, address, monkeypatch):
    monkeypatch.setattr(host, "socket_address", lambda: address)
    monkeypatch.setattr(host, "load_authkey", lambda: KEY)
    monkeypatch.setattr(host, "RETRY_SECONDS", 0.05)
    read_fd, write_fd = os.pipe()
    browser_in = os.fdopen(read_fd, "rb")
    browser_out = io.BytesIO()
    out_lock = threading.Lock()

    class SharedOut:
        def write(self, data):
            with out_lock:
                browser_out.write(data)

        def flush(self):
            pass

    done = threading.Thread(target=host.run, args=(browser_in, SharedOut()), daemon=True)
    done.start()
    with os.fdopen(write_fd, "wb") as to_host:
        to_host.write(frame(HELLO))  # sent before the app is reachable: the host replays it
        to_host.flush()
        connection = listener.connection(5)
        assert connection.hello["instance"] == "a"
        rid, future = connection.submit("ping", {"n": 1})
        # the host passes the app's command to the browser verbatim ...
        deadline = time.monotonic() + 5
        data = b""
        while b"ping" not in data and time.monotonic() < deadline:
            time.sleep(0.02)
            with out_lock:
                data = browser_out.getvalue()
        frames, stream = [], io.BytesIO(data)
        while (item := host.read_frame(stream)) is not None:
            frames.append(json.loads(item))
        assert {"id": rid, "cmd": "ping", "args": {"n": 1}} in frames
        # ... and the browser's answer back to the app
        to_host.write(frame({"id": rid, "ok": True, "result": "pong"}))
        to_host.flush()
        assert future.result(5) == "pong"
    done.join(5)  # closing the browser side (EOF) ends the relay
    assert not done.is_alive()


# -- Listener ---------------------------------------------------------------


def test_request_response_round_trip(listener, address):
    client = connect(address)
    session = listener.session(2)

    def answer():
        request = recv(client)
        client.send_bytes(json.dumps({"id": request["id"], "ok": True, "result": request["args"]}).encode())

    threading.Thread(target=answer, daemon=True).start()
    assert session.request("echo", {"x": 1}, timeout=3) == {"x": 1}
    client.close()


def test_error_reply_raises_extension_error(listener, address):
    client = connect(address)
    session = listener.session(2)
    rid, future = session.submit("dom.click", {})
    request = recv(client)
    client.send_bytes(json.dumps(
        {"id": request["id"], "ok": False, "error": {"code": "refused", "message": "no"}}).encode())
    with pytest.raises(link.ExtensionError) as info:
        future.result(3)
    assert info.value.code == "refused" and rid == request["id"]


def test_timeout_cancels_the_command(listener, address):
    client = connect(address)
    session = listener.session(2)
    with pytest.raises(AutomationError, match="in time"):
        session.request("slow", {}, timeout=0.1)
    sent = recv(client)
    cancel = recv(client)
    assert cancel == {"cmd": "cancel", "args": {"id": sent["id"]}}


def test_cancelled_future_sends_cancel_and_late_reply_reaches_on_reply(listener, address):
    client = connect(address)
    session = listener.session(2)
    replies = []
    rid, future = session.submit("slow", {}, on_reply=replies.append)
    assert future.cancel()
    assert recv(client)["id"] == rid
    assert recv(client) == {"cmd": "cancel", "args": {"id": rid}}
    client.send_bytes(json.dumps({"id": rid, "ok": False, "error": {"code": "cancelled", "message": ""}}).encode())
    deadline = time.monotonic() + 3
    while not replies and time.monotonic() < deadline:
        time.sleep(0.01)
    assert replies and replies[0]["id"] == rid


def test_events_are_routed_to_the_session_of_the_connection(listener, address):
    first, second = connect(address), connect(address, {**HELLO, "instance": "b"})
    deadline = time.monotonic() + 3
    while len(listener.connections()) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(listener.connections()) == 2
    relay = next(c for c in listener.connections() if c.instance == "b")
    before = relay.last_active
    second.send_bytes(b'{"event":"noop"}')  # make `second` the most recently active
    # Wait until the relay has read it: a slow runner can take longer than any fixed sleep.
    deadline = time.monotonic() + 3
    while relay.last_active == before and time.monotonic() < deadline:
        time.sleep(0.01)
    session = listener.session(1)
    other = [c for c in listener.connections() if c is not session.connection][0].session()
    second.send_bytes(json.dumps({"event": "tab.created", "tabId": 5}).encode())
    first.send_bytes(json.dumps({"event": "tab.updated", "tabId": 9}).encode())
    assert session.next_event(3) == {"event": "tab.created", "tabId": 5}
    assert other.next_event(3) == {"event": "tab.updated", "tabId": 9}
    assert session.next_event(0) is None
    first.close()
    second.close()


def test_on_event_callback_replaces_the_queue(listener, address):
    client = connect(address)
    session = listener.session(2)
    seen = []
    session.on_event = seen.append
    client.send_bytes(b'{"event":"download.created","downloadId":1}')
    deadline = time.monotonic() + 3
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen == [{"event": "download.created", "downloadId": 1}]
    assert session.next_event(0) is None


def test_player_commands_reach_the_callback_not_the_sessions(listener, address):
    client = connect(address)
    session = listener.session(2)
    seen = []
    for op in ("next", "stop", ["toggle"], None):  # nothing registered yet (the CLI): ignored
        client.send_bytes(json.dumps({"event": "player.command", "op": op}).encode())
    client.send_bytes(b'{"event":"tab.created","tabId":0}')
    assert session.next_event(3) == {"event": "tab.created", "tabId": 0}  # all of them read
    listener.on_player_command = seen.append
    for op in ("prev", "eval", {"op": "next"}, "toggle", "next"):
        client.send_bytes(json.dumps({"event": "player.command", "op": op}).encode())
    client.send_bytes(b'{"event":"tab.created","tabId":1}')
    assert session.next_event(3) == {"event": "tab.created", "tabId": 1}  # other events still go to sessions
    assert seen == [{"op": "prev"}, {"op": "toggle"}, {"op": "next"}]
    assert session.next_event(0) is None


def test_player_commands_carry_only_their_own_checked_arguments():
    url = "https://soundcloud.com/a/sets/b"
    accepted = [
        ({"op": "cancel", "id": 4}, {"op": "cancel"}),
        ({"op": "seek", "fraction": 0}, {"op": "seek", "fraction": 0.0}),
        ({"op": "seek", "fraction": 0.5, "seconds": 9}, {"op": "seek", "fraction": 0.5}),
        ({"op": "gates", "choice": "open"}, {"op": "gates", "choice": "open"}),
        ({"op": "import", "url": url, "kind": "x"}, {"op": "import", "url": url}),
    ]
    for frame, command in accepted:
        assert link.player_command(frame) == command
    for frame in (
        {"op": "seek", "fraction": 1.01}, {"op": "seek", "fraction": -0.1}, {"op": "seek", "fraction": True},
        {"op": "seek", "fraction": float("nan")}, {"op": "seek", "fraction": "0.5"}, {"op": "seek"},
        {"op": "gates", "choice": "all"}, {"op": "gates"}, {"op": "import", "url": ["x"]},
        {"op": "import", "url": "https://soundcloud.com/" + "a" * 500}, {"op": "eval"}, {"op": None}, {},
    ):  # fmt: skip
        assert link.player_command(frame) is None, frame


def test_player_state_goes_to_every_compatible_browser_and_to_later_hellos(listener, address):
    first, old = connect(address), connect(address, {**HELLO, "instance": "o", "protocol": 99})
    deadline = time.monotonic() + 3
    while len(listener.connections()) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    state = {"title": "T", "artist": "A", "playing": True, "seed": 7, "artwork": "data:image/jpeg;base64,AA=="}
    listener.publish_player(state)
    assert recv(first) == {"cmd": "player.state", "args": state}  # no id: nothing waits for a reply
    assert not old.poll(0.3)  # an incompatible extension gets nothing
    big = dict(state, artwork="data:image/png;base64," + "A" * link.MAX_ARTWORK)
    listener.publish_player(big)
    assert recv(first)["args"] == dict(state, artwork="")
    late = connect(address, {**HELLO, "instance": "l"})
    assert recv(late) == {"cmd": "player.state", "args": dict(state, artwork="")}
    for client in (first, old, late):
        client.close()


def test_wrong_authkey_is_rejected(listener, address):
    with pytest.raises(mpc.AuthenticationError):
        mpc.Client(address, authkey=b"x" * 32)
    assert listener.connections() == []
    with pytest.raises(link.ExtensionMissing):
        listener.connection(0.2)


def test_no_connection_or_no_hello_is_extension_missing(listener, address):
    start = time.monotonic()
    with pytest.raises(link.ExtensionMissing, match="not connected"):
        listener.connection(0.2)
    assert time.monotonic() - start < 2
    silent = connect(address, hello=None)  # connected, authenticated, never says hello
    with pytest.raises(link.ExtensionMissing):
        listener.connection(0.3)
    silent.close()


def test_protocol_mismatch_asks_for_a_reload(listener, address):
    client = connect(address, {**HELLO, "protocol": 99})
    with pytest.raises(link.ExtensionMissing, match="Reload or update"):
        listener.connection(1)
    client.close()


def test_connection_drop_ends_the_session(listener, address):
    client = connect(address)
    session = listener.session(2)
    rid, future = session.submit("slow", {})
    recv(client)
    client.close()
    with pytest.raises(AutomationError, match="browser extension restarted"):
        future.result(3)
    with pytest.raises(AutomationError, match="browser extension restarted"):
        session.next_event(3)
    with pytest.raises(AutomationError, match="browser extension restarted"):
        session.request("again")


def test_new_instance_ends_the_session_but_keeps_the_connection(listener, address):
    client = connect(address)
    session = listener.session(2)
    rid, future = session.submit("slow", {})
    recv(client)
    client.send_bytes(json.dumps({**HELLO, "instance": "restarted"}).encode())
    with pytest.raises(AutomationError, match="browser extension restarted"):
        future.result(3)
    with pytest.raises(AutomationError, match="restarted"):
        session.request("x")
    fresh = listener.session(1)  # same connection, new session works again
    assert fresh.connection is session.connection
    client.close()


def test_same_instance_hello_keeps_the_session(listener, address):
    client = connect(address)
    session = listener.session(2)
    client.send_bytes(json.dumps(HELLO).encode())
    time.sleep(0.1)
    assert session.error is None
    client.close()


def test_stale_socket_file_is_replaced(address):
    if not POSIX:
        pytest.skip("unix sockets only")
    import socket

    stale = socket.socket(socket.AF_UNIX)
    stale.bind(address)
    stale.close()  # leaves the socket file behind, like a crash
    with link.Listener(address, KEY) as listener:
        client = connect(address)
        assert listener.connection(2).hello["browser"] == "chrome"
        client.close()


def test_a_live_socket_is_never_taken_over(listener, address):
    if not POSIX:
        pytest.skip("unix sockets only")
    client = connect(address)
    with pytest.raises(AutomationError, match="another dj-digger is already connected"):
        link.Listener(address, KEY).start()
    assert os.path.exists(address)  # the first owner keeps its socket ...
    assert listener.connection(2).hello["browser"] == "chrome"  # ... and its connections
    session = listener.session(2)
    client.send_bytes(json.dumps({"event": "tab.removed", "tabId": 1}).encode())
    assert session.next_event(2) == {"event": "tab.removed", "tabId": 1}
    client.close()


def test_a_second_pipe_server_is_reported_as_another_app(address, monkeypatch):
    def denied(*_args):
        raise PermissionError("first pipe instance exists")

    monkeypatch.setattr(link.mpc, "Listener", denied)
    with pytest.raises(AutomationError, match="another dj-digger is already connected"):
        link.Listener(link.PIPE_PREFIX + "dj-digger-test", KEY).start()


def test_oversized_command_is_refused_locally(listener, address):
    client = connect(address)
    session = listener.session(2)
    with pytest.raises(AutomationError, match="too large"):
        session.submit("x", {"p": "x" * link.MAX_TO_BROWSER})
    client.close()


# -- address and authkey --------------------------------------------------------


@pytest.mark.skipif(not POSIX, reason="unix sockets only")
def test_socket_address_and_authkey_locations(tmp_path, monkeypatch):
    # macOS tmp_path alone nearly fills sun_path, so the short cases use a short base.
    short = Path(tempfile.mkdtemp(prefix="djs", dir="/tmp"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short / "run"))
    assert link.socket_address() == str(short / "run" / "dj-digger" / "webext.sock")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    monkeypatch.setenv("XDG_DATA_HOME", str(short / "data"))
    assert link.socket_address() == str(short / "data" / "dj-digger" / "run" / "webext.sock")
    short.rmdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ("long" * 30)))  # beyond sun_path
    assert link.socket_address() == f"/tmp/dj-digger-{os.getuid()}/webext.sock"


def test_authkey_is_created_once_with_private_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert link.load_authkey() is None
    key = link.load_authkey(create=True)
    assert len(key) == 32 and link.load_authkey() == key and link.load_authkey(create=True) == key
    path = tmp_path / "dj-digger" / "webext.json"
    assert len(json.loads(path.read_text())["authkey"]) == 64
    if POSIX:
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(not POSIX, reason="unix socket directory permissions")
def test_listener_creates_a_private_socket_directory(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="djr", dir="/tmp") as runtime:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with link.Listener() as listener:
            assert os.stat(Path(listener.address).parent).st_mode & 0o777 == 0o700
            assert os.stat(listener.address).st_mode & 0o777 == 0o600


# -- no pickling ------------------------------------------------------------


def test_webext_sources_never_use_the_pickling_connection_api():
    for path in (ROOT / "dj_digger" / "webext").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert ".send(" not in text and ".recv(" not in text, path


# -- registration -----------------------------------------------------------


def _extension_dir(tmp_path, version="1.0.0"):
    source = tmp_path / "extension-src"
    source.mkdir(exist_ok=True)
    der = b"\x30\x82fake-der-public-key"
    manifest = {
        "manifest_version": 3, "version": version, "key": base64.b64encode(der).decode(),
        "browser_specific_settings": {"gecko": {"id": "dj-digger@fbialogrecki.github.io"}},
    }
    (source / "manifest.json").write_text(json.dumps(manifest))
    (source / "background.js").write_text("// x")
    digest = hashlib.sha256(der).hexdigest()[:32]
    expected = "".join("abcdefghijklmnop"[int(c, 16)] for c in digest)
    return source, expected


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("APPDATA", str(home / "AppData/Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData/Local"))
    source, expected = _extension_dir(tmp_path)
    monkeypatch.setattr(register, "EXTENSION_SOURCE", source)
    launcher = tmp_path / "bin" / ("dj-digger-host.exe" if sys.platform == "win32" else "dj-digger-host")
    launcher.parent.mkdir()
    launcher.write_text("")
    monkeypatch.setattr(register, "host_executable", lambda: launcher)
    return home, source, expected, launcher


def test_chrome_extension_id_is_derived_from_the_key():
    der = bytes(range(64))
    digest = hashlib.sha256(der).hexdigest()
    mapped = digest[:32].translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))
    assert register.chrome_extension_id(base64.b64encode(der).decode()) == mapped
    assert len(mapped) == 32 and set(mapped) <= set("abcdefghijklmnop")
    with pytest.raises(register.RegistrationError):
        register.chrome_extension_id("not base64 !!")


def test_register_requires_the_extension_manifest(tmp_path, monkeypatch, sandbox):
    monkeypatch.setattr(register, "EXTENSION_SOURCE", tmp_path / "missing")
    with pytest.raises(register.RegistrationError, match="manifest"):
        register.register()


@pytest.mark.skipif(sys.platform == "win32", reason="posix layout")
def test_register_and_unregister_on_linux(sandbox, monkeypatch):
    home, source, expected, launcher = sandbox
    monkeypatch.setattr(sys, "platform", "linux")
    (home / ".config/google-chrome").mkdir(parents=True)
    (home / ".config/BraveSoftware/Brave-Browser").mkdir(parents=True)
    (home / ".mozilla/firefox").mkdir(parents=True)
    (home / ".var/app/org.mozilla.firefox").mkdir(parents=True)  # Flatpak: reported only
    result = register.register()
    name = register.HOST_NAME + ".json"
    chrome = json.loads((home / ".config/google-chrome/NativeMessagingHosts" / name).read_text())
    assert chrome["allowed_origins"] == [f"chrome-extension://{i}/" for i in (expected, register.CHROME_STORE_ID)]
    assert chrome["path"] == str(launcher) and chrome["type"] == "stdio" and chrome["name"] == register.HOST_NAME
    assert (home / ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts" / name).is_file()
    assert not (home / ".config/chromium").exists() and not (home / ".config/microsoft-edge").exists()
    for firefox in (home / ".mozilla/native-messaging-hosts", home / ".config/mozilla/native-messaging-hosts"):
        manifest = json.loads((firefox / name).read_text())
        assert manifest["allowed_extensions"] == ["dj-digger@fbialogrecki.github.io"]
        assert "allowed_origins" not in manifest
    assert any("Flatpak" in note for note in result.notes)
    assert not list((home / ".var").rglob("*djdigger*"))
    assert (result.extension_dir / "background.js").is_file()
    assert result.extension_dir == home / ".local/share/dj-digger/browser-extension"
    assert len(result.written) == 4
    register.register()  # idempotent
    removed = register.unregister()
    assert len(removed) == 4
    assert not list(home.rglob(name))
    assert register.unregister() == []


@pytest.mark.skipif(sys.platform == "win32", reason="posix layout")
def test_register_also_allows_the_chrome_web_store_id(sandbox, monkeypatch):
    home, source, expected, launcher = sandbox
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(register, "CHROME_STORE_ID", "a" * 32)
    (home / ".config/chromium").mkdir(parents=True)
    (home / ".mozilla/firefox").mkdir(parents=True)
    register.register()
    name = register.HOST_NAME + ".json"
    chrome = json.loads((home / ".config/chromium/NativeMessagingHosts" / name).read_text())
    assert chrome["allowed_origins"] == [f"chrome-extension://{expected}/", f"chrome-extension://{'a' * 32}/"]
    firefox = json.loads((home / ".mozilla/native-messaging-hosts" / name).read_text())
    assert firefox["allowed_extensions"] == ["dj-digger@fbialogrecki.github.io"]


@pytest.mark.skipif(sys.platform == "win32", reason="posix layout")
def test_register_on_macos_and_without_browsers(sandbox, monkeypatch):
    home, *_ = sandbox
    monkeypatch.setattr(sys, "platform", "darwin")
    assert register.register().written == []
    support = home / "Library/Application Support"
    (support / "Google/Chrome").mkdir(parents=True)
    (support / "Firefox").mkdir(parents=True)
    result = register.register()
    assert len(result.written) == 2
    assert (support / "Google/Chrome/NativeMessagingHosts" / (register.HOST_NAME + ".json")).is_file()
    assert (support / "Mozilla/NativeMessagingHosts" / (register.HOST_NAME + ".json")).is_file()


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    def CreateKey(self, root, path):
        assert root == "HKCU"
        return self._Key(path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[(key.path, name)] = value

    def DeleteKey(self, root, path):
        assert root == "HKCU"
        for stored in [k for k in self.values if k[0] == path]:
            del self.values[stored]
            return
        raise FileNotFoundError(path)


def test_register_and_unregister_on_windows_with_a_fake_registry(sandbox, monkeypatch):
    home, source, expected, launcher = sandbox
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    (home / "AppData/Local/Google/Chrome/User Data").mkdir(parents=True)
    (home / "AppData/Roaming/Mozilla/Firefox").mkdir(parents=True)
    result = register.register()
    chrome_key = rf"Software\Google\Chrome\NativeMessagingHosts\{register.HOST_NAME}"
    firefox_key = rf"Software\Mozilla\NativeMessagingHosts\{register.HOST_NAME}"
    assert set(k[0] for k in fake.values) == {chrome_key, firefox_key}
    chrome_manifest = Path(fake.values[(chrome_key, "")])
    assert json.loads(chrome_manifest.read_text())["allowed_origins"] == [f"chrome-extension://{i}/" for i in (expected, register.CHROME_STORE_ID)]
    assert json.loads(Path(fake.values[(firefox_key, "")]).read_text())["allowed_extensions"]
    assert home in chrome_manifest.parents and len(result.written) == 2
    register.unregister()
    assert fake.values == {} and not chrome_manifest.exists()


def test_extension_copy_is_refreshed_when_its_content_differs(sandbox):
    home, source, *_ = sandbox
    destination = register.sync_extension()
    assert (destination / "background.js").read_text() == "// x"
    mark = (destination / "manifest.json").stat().st_mtime_ns
    register.sync_extension()  # identical content: untouched
    assert (destination / "manifest.json").stat().st_mtime_ns == mark
    # Same manifest version, changed script: the browser must still get the new file.
    (source / "background.js").write_text("// y")
    register.sync_extension()
    assert (destination / "background.js").read_text() == "// y"
    # A hand-edited or damaged copy is restored, and a new file arrives too.
    (destination / "background.js").write_text("// local edit")
    (source / "extra.js").write_text("// z")
    register.sync_extension()
    assert (destination / "background.js").read_text() == "// y"
    assert (destination / "extra.js").read_text() == "// z"
    assert not list(destination.parent.glob("browser-extension*.old"))
    assert [p.name for p in destination.parent.iterdir()] == ["browser-extension"]


def test_host_executable_prefers_the_sibling_of_the_interpreter(tmp_path, monkeypatch):
    name = "dj-digger-host.exe" if sys.platform == "win32" else "dj-digger-host"
    (tmp_path / name).write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    assert register.host_executable() == (tmp_path / name).absolute()
    (tmp_path / name).unlink()
    monkeypatch.setattr(register.shutil, "which", lambda _: None)
    with pytest.raises(register.RegistrationError, match="Cannot find"):
        register.host_executable()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(register.shutil, "which", lambda _: str(tmp_path / "elsewhere"))
    with pytest.raises(register.RegistrationError):
        register.host_executable()


def test_cli_browser_subcommand_parses():
    from dj_digger import cli

    assert cli.parse_cli_args(["browser", "register"]).browser_action == "register"
    assert cli.parse_cli_args(["browser", "unregister"]).browser_action == "unregister"

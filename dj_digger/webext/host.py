"""``dj-digger-host``: Native Messaging relay between the browser and the app.

The browser spawns this process and talks to it over stdin/stdout (4-byte
native-endian length + UTF-8 JSON). It forwards raw frames to the app's private
socket (``link.py``) and back, using only ``send_bytes``/``recv_bytes``.

* stdout carries frames and nothing else (``main`` points ``sys.stdout`` at
  stderr); stderr diagnostics never contain frame data.
* It exits when the browser closes stdin, whatever the app connection is doing.
* It retries the app connection every second and goes back to waiting when the
  app disconnects. The extension's ``hello`` is cached and replayed to every new
  app connection, because the extension says hello only once per port.
* App -> browser frames above 1 MB are refused with an error frame.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import threading
import time
from multiprocessing import connection as mpc
from typing import BinaryIO

from .link import MAX_FROM_BROWSER, MAX_TO_BROWSER, load_authkey, socket_address

RETRY_SECONDS = 1.0
HELLO_PEEK_LIMIT = 4096


def read_frame(stream: BinaryIO) -> bytes | None:
    """One framed message, or ``None`` at EOF (also for a truncated frame)."""

    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("=I", header)
    if length > MAX_FROM_BROWSER:
        return None
    data = stream.read(length)
    return data if len(data) == length else None


def write_frame(stream: BinaryIO, data: bytes) -> None:
    stream.write(struct.pack("=I", len(data)) + data)
    stream.flush()


def _is_hello(frame: bytes) -> bool:
    if len(frame) > HELLO_PEEK_LIMIT or b"hello" not in frame:
        return False
    try:
        decoded = json.loads(frame)
    except ValueError:
        return False
    return isinstance(decoded, dict) and decoded.get("event") == "hello"


def _error_frame(data: bytes) -> bytes:
    error = {"code": "frame-too-large", "message": "app frame exceeds the 1 MB Native Messaging limit"}
    try:
        request_id = json.loads(data).get("id")
    except (ValueError, AttributeError):
        request_id = None
    if isinstance(request_id, int):
        return json.dumps({"id": request_id, "ok": False, "error": error}).encode("utf-8")
    return json.dumps({"event": "host-error", "error": error}).encode("utf-8")


class Relay:
    def __init__(self, stdout: BinaryIO):
        self._stdout = stdout
        self._stdout_lock = threading.Lock()
        self._lock = threading.Lock()
        self._app: mpc.Connection | None = None
        self._hello: bytes | None = None
        self._offline_sent = False

    def to_browser(self, data: bytes) -> bool:
        with self._stdout_lock:
            try:
                write_frame(self._stdout, data)
            except (OSError, ValueError):
                return False
        return True

    def from_browser(self, frame: bytes) -> None:
        if _is_hello(frame):
            with self._lock:
                self._hello = frame
        with self._lock:
            app = self._app
        if app is None:
            return  # nobody to tell; the app learns from the replayed hello
        try:
            app.send_bytes(frame)
        except (OSError, ValueError):
            pass  # the app loop sees the dead connection on its recv

    def app_loop(self) -> None:
        """Connect, pump app -> browser until the app goes away, repeat."""

        while True:
            app = self._connect()
            if app is None:
                if not self._offline_sent:
                    self._offline_sent = True
                    self.to_browser(b'{"event":"app-offline"}')
                time.sleep(RETRY_SECONDS)
                continue
            self._offline_sent = False
            with self._lock:
                self._app = app
                hello = self._hello
            try:
                if hello is not None:
                    app.send_bytes(hello)
                self._pump(app)
            except (OSError, ValueError):
                pass
            finally:
                with self._lock:
                    self._app = None
                try:
                    app.close()
                except OSError:
                    pass
            time.sleep(RETRY_SECONDS)

    @staticmethod
    def _connect() -> mpc.Connection | None:
        key = load_authkey()
        if key is None:
            return None
        try:
            return mpc.Client(socket_address(), authkey=key)
        except (OSError, EOFError, mpc.AuthenticationError):
            return None

    def _pump(self, app: mpc.Connection) -> None:
        while True:
            try:
                data = app.recv_bytes()
            except (EOFError, OSError):
                return
            if len(data) > MAX_TO_BROWSER:
                print("dj-digger-host: refused an oversized app frame", file=sys.stderr)
                data = _error_frame(data)
            if not self.to_browser(data):
                return


def run(stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Relay until ``stdin`` reaches EOF (the browser closed the port)."""

    relay = Relay(stdout)
    # Daemon: the process must end on stdin EOF even while this thread waits.
    threading.Thread(target=relay.app_loop, name="app-link", daemon=True).start()
    while (frame := read_frame(stdin)) is not None:
        relay.from_browser(frame)
    return 0


def main() -> int:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # a stray print() must never corrupt the frame stream
    code = run(stdin, stdout)
    # Hard exit: a normal interpreter shutdown can abort ("could not acquire
    # lock ... daemon threads") if the relay thread is mid-write to stdout.
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())

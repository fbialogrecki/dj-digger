"""App side of the browser-extension channel (plan D2, section 4 point 8).

Topology: extension <-Native Messaging-> ``dj-digger-host`` <-this socket-> app.
The app owns one :class:`Listener`; each ``dj-digger-host`` relay is one
:class:`Connection`; one batch of automation is one :class:`Session` bound to
the connection it started on (a dropped connection ends the session, it never
moves to another browser).

Only ``send_bytes``/``recv_bytes`` plus JSON are used on the sockets. The
pickling ``send``/``recv`` would turn a leaked authkey into code execution, and
a test greps this package for them.

Callers hold ``InstanceLock`` (``dj_digger/instance.py``, taken in ``cli.main``
and the GUI), but that lock is per data directory while the socket is per
account. So ``Listener.start()`` unlinks an old socket file only when nothing
answers on it and refuses with an ``AutomationError`` when another app does.

Typical use by an adapter::

    session = listener.session()                 # newest connection with hello
    result = session.request("tab.new", {"url": url}, timeout=15)   # blocking
    rid, future = session.submit("dom.click", args)   # asyncio.wrap_future(future)
    event = session.next_event(timeout=0.1)           # sync event dispatch

Cancelling the ``Future`` (what ``asyncio.wait_for`` does to a wrapped future)
automatically sends ``{"cmd": "cancel", "args": {"id": n}}``; the extension
still answers the cancelled command, see ``on_reply``.
"""

from __future__ import annotations

import getpass
import itertools
import json
import logging
import os
import queue
import re
import secrets
import socket
import stat
import threading
import time
from concurrent.futures import Future, InvalidStateError
from multiprocessing import connection as mpc
from pathlib import Path
from typing import Any, Callable

from ..automation_errors import AutomationError
from ..paths import config_dir, data_dir
from ..private_json import write_private_json

LOGGER = logging.getLogger(__name__)

PROTOCOL = 1
HELLO_TIMEOUT = 3.0
DEFAULT_TIMEOUT = 15.0
MAX_TO_BROWSER = 1024 * 1024  # Native Messaging host -> browser limit
MAX_FROM_BROWSER = 64 * 1024 * 1024 + 4096  # browser -> host limit, plus slack
RESTARTED = "browser extension restarted"
SOCKET_PATH_LIMIT = 100  # sun_path is 104 bytes on macOS, 108 on Linux
PIPE_PREFIX = "\\\\.\\pipe\\"
SOCKET_NAME = "webext.sock"
ANOTHER_APP = "another dj-digger is already connected to the browser extension"


class ExtensionMissing(AutomationError):
    """No extension connected, or none said hello in time (or protocol mismatch)."""


class ExtensionError(AutomationError):
    """The extension answered ``ok: false``; ``code`` is its error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def socket_address() -> str:
    """Where the app listens and the host connects. Pure: creates nothing."""

    if os.name == "nt":
        user = re.sub(r"[^\w.-]", "_", getpass.getuser())
        return f"{PIPE_PREFIX}dj-digger-{user}"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    directory = Path(runtime) / "dj-digger" if runtime else data_dir() / "run"
    address = str(directory / SOCKET_NAME)
    if len(os.fsencode(address)) >= SOCKET_PATH_LIMIT:
        address = f"/tmp/dj-digger-{os.getuid()}/{SOCKET_NAME}"
    return address


def load_authkey(create: bool = False) -> bytes | None:
    """The shared secret in ``config_dir()/webext.json`` (0600).

    Only the app (``create=True``) generates it; the host merely reads it and
    keeps retrying while it is missing.
    """

    path = config_dir() / "webext.json"
    try:
        key = json.loads(path.read_text(encoding="utf-8")).get("authkey")
        if isinstance(key, str) and len(key) == 64:
            return bytes.fromhex(key)
    except (OSError, ValueError, AttributeError):
        pass
    if not create:
        return None
    key = secrets.token_hex(32)
    write_private_json(path, {"authkey": key})
    return bytes.fromhex(key)


def _prepare_directory(address: str) -> None:
    """Own, private (0700) socket directory; refuse one somebody else made."""

    directory = Path(address).parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AutomationError("Refusing a socket directory that is not owned by this user")
    os.chmod(directory, 0o700)
    try:
        if not stat.S_ISSOCK(os.lstat(address).st_mode):
            return
    except FileNotFoundError:
        return
    # Another app (a second data directory, a test fixture) may be listening: only a socket
    # that refuses connections is a crash leftover. Anything else, a busy listener included, stays.
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.settimeout(1.0)
        probe.connect(address)
    except (ConnectionRefusedError, FileNotFoundError):
        os.unlink(address)
        return
    except OSError:
        pass
    finally:
        probe.close()
    raise AutomationError(ANOTHER_APP)


def _settle(future: Future, *, result: Any = None, error: BaseException | None = None) -> None:
    try:
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)
    except InvalidStateError:  # cancelled while the answer was in flight
        pass


class Session:
    """One batch of automation on one connection, with its own event queue.

    ``next_event`` yields frames like ``{"event": "tab.created", ...}``.
    ``on_event``, if set, receives them instead of the queue (called on the
    connection's reader thread; use ``loop.call_soon_threadsafe``). When the
    session ends, the terminating ``AutomationError`` is delivered the same way
    and every later call raises it.
    """

    def __init__(self, connection: Connection):
        self.connection = connection
        self.error: AutomationError | None = None
        self.on_event: Callable[[Any], None] | None = None
        self._events: queue.Queue = queue.Queue()

    def _deliver(self, item: Any) -> None:
        if self.on_event is not None:
            self.on_event(item)
        else:
            self._events.put(item)

    def _end(self, error: AutomationError) -> None:
        if self.error is None:
            self.error = error
            self._deliver(error)

    def submit(self, cmd: str, args: dict | None = None,
               on_reply: Callable[[dict], None] | None = None) -> tuple[int, Future]:
        if self.error is not None:
            raise self.error
        return self.connection.submit(cmd, args, on_reply)

    def request(self, cmd: str, args: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
        if self.error is not None:
            raise self.error
        return self.connection.request(cmd, args, timeout)

    def cancel(self, request_id: int) -> None:
        self.connection.cancel(request_id)

    def next_event(self, timeout: float | None = None) -> dict | None:
        """Next event, ``None`` on timeout (``0`` polls); raises once the session ended."""

        try:
            item = self._events.get_nowait() if timeout == 0 else self._events.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, Exception):
            self._events.put(item)
            raise item
        return item

    def close(self) -> None:
        self.connection._drop_session(self)

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Connection:
    """One ``dj-digger-host`` relay, i.e. one browser with the extension."""

    def __init__(self, listener: Listener, raw: mpc.Connection, authkey: bytes):
        self.id = next(listener._ids)
        self.hello: dict | None = None
        self.instance: str | None = None
        self.last_active = time.monotonic()
        self.closed = False
        self._listener = listener
        self._raw = raw
        self._authkey = authkey
        self._send_lock = threading.Lock()
        self._lock = threading.Lock()
        self._request_ids = itertools.count(1)
        self._pending: dict[int, tuple[Future, Callable | None]] = {}
        self._sessions: list[Session] = []

    # -- public ---------------------------------------------------------

    @property
    def compatible(self) -> bool:
        return self.hello is not None and self.hello.get("protocol") == PROTOCOL

    def session(self) -> Session:
        with self._lock:
            if self.closed:
                raise AutomationError(RESTARTED)
            session = Session(self)
            self._sessions.append(session)
        return session

    def submit(self, cmd: str, args: dict | None = None,
               on_reply: Callable[[dict], None] | None = None) -> tuple[int, Future]:
        """Send a command; return ``(id, future)``.

        ``on_reply(frame)`` runs on the reader thread for the answer even when
        the future was cancelled meanwhile, so an adapter can keep a tab busy
        until the extension has really finished the cancelled command.
        """

        request_id = next(self._request_ids)
        future: Future = Future()
        with self._lock:
            if self.closed:
                raise AutomationError(RESTARTED)
            self._pending[request_id] = (future, on_reply)
        try:
            self._send({"id": request_id, "cmd": cmd, "args": args or {}})
        except BaseException:
            with self._lock:
                self._pending.pop(request_id, None)
            raise
        future.add_done_callback(lambda f: f.cancelled() and self.cancel(request_id))
        return request_id, future

    def request(self, cmd: str, args: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
        """Blocking call. On timeout the command is cancelled in the extension."""

        _, future = self.submit(cmd, args)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise AutomationError(f"browser extension did not answer {cmd} in time") from None

    def cancel(self, request_id: int) -> None:
        try:
            self._send({"cmd": "cancel", "args": {"id": request_id}})
        except AutomationError:
            pass  # connection already gone; nothing left to cancel

    def close(self) -> None:
        try:
            self._raw.close()
        except OSError:
            pass
        self._shutdown()  # do not wait for the reader: close() may not wake its recv

    # -- internals ------------------------------------------------------

    def _send(self, frame: dict) -> None:
        data = json.dumps(frame, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_TO_BROWSER:
            raise AutomationError("browser extension command is too large")
        try:
            with self._send_lock:
                self._raw.send_bytes(data)
        except (OSError, ValueError) as exc:
            raise AutomationError(RESTARTED) from exc

    def _drop_session(self, session: Session) -> None:
        with self._lock:
            if session in self._sessions:
                self._sessions.remove(session)

    def _end_sessions(self) -> None:
        with self._lock:
            sessions, self._sessions = self._sessions, []
            pending, self._pending = self._pending, {}
        for session in sessions:
            session._end(AutomationError(RESTARTED))
        for future, _ in pending.values():
            _settle(future, error=AutomationError(RESTARTED))

    def _shutdown(self) -> None:
        with self._lock:
            already, self.closed = self.closed, True
        if not already:
            self._listener._forget(self)
        self._end_sessions()

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"webext-conn-{self.id}", daemon=True).start()

    def _run(self) -> None:
        try:
            # The handshake runs here, not in Listener.accept(), so a client that
            # connects and goes silent only parks its own thread. These two are
            # what accept()/Client() call; they exchange HMAC challenges.
            mpc.deliver_challenge(self._raw, self._authkey)
            mpc.answer_challenge(self._raw, self._authkey)
        except (mpc.AuthenticationError, EOFError, OSError):
            LOGGER.debug("webext connection %s failed authentication", self.id)
            self.close()
            return
        self._listener._adopt(self)
        try:
            while True:
                self._handle(self._raw.recv_bytes(MAX_FROM_BROWSER))
        except (EOFError, OSError):
            pass
        finally:
            self.close()

    def _handle(self, data: bytes) -> None:
        try:
            frame = json.loads(data)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        self.last_active = time.monotonic()
        if "id" in frame:
            with self._lock:
                entry = self._pending.pop(frame["id"], None) if isinstance(frame["id"], int) else None
            if entry is not None:
                self._reply(frame, *entry)
        elif frame.get("event") == "hello":
            self._hello(frame)
        elif "event" in frame:
            with self._lock:
                sessions = list(self._sessions)
            # ponytail: every event goes to every session of this connection (several run at
            # once); adapters filter by tabId / windowId. Per-session routing if that gets costly.
            for session in sessions:
                session._deliver(frame)

    def _reply(self, frame: dict, future: Future, on_reply: Callable | None) -> None:
        if frame.get("ok"):
            _settle(future, result=frame.get("result"))
        else:
            error = frame.get("error")
            error = error if isinstance(error, dict) else {}
            _settle(future, error=ExtensionError(str(error.get("code", "error")),
                                                 str(error.get("message", "browser extension error"))))
        if on_reply is not None:
            try:
                on_reply(frame)
            except Exception:  # an adapter bug must not kill the reader thread
                LOGGER.exception("webext on_reply callback failed")

    def _hello(self, frame: dict) -> None:
        instance = frame.get("instance")
        with self._listener._cond:
            restarted = self.hello is not None and instance != self.instance
            self.hello, self.instance = frame, instance
            self._listener._cond.notify_all()
        if restarted:
            self._end_sessions()


class Listener:
    """Accepts relays on the private socket. ``start()`` is explicit and idempotent."""

    def __init__(self, address: str | None = None, authkey: bytes | None = None):
        self.address = address or socket_address()
        self._authkey = authkey
        self._listener: mpc.Listener | None = None
        self._thread: threading.Thread | None = None
        self._closing = False
        self._conns: list[Connection] = []
        self._cond = threading.Condition()
        self._ids = itertools.count(1)

    def start(self) -> None:
        if self._listener is not None:
            return
        if self._authkey is None:
            self._authkey = load_authkey(create=True)
        family = "AF_PIPE" if self.address.startswith(PIPE_PREFIX) else "AF_UNIX"
        if family == "AF_UNIX":
            _prepare_directory(self.address)
        self._closing = False
        # No authkey here: Connection._run does the (mutual) handshake itself.
        try:
            self._listener = mpc.Listener(self.address, family)
        except PermissionError as exc:
            if family != "AF_PIPE":
                raise
            # ponytail: multiprocessing creates the first pipe instance with FILE_FLAG_FIRST_PIPE_INSTANCE,
            # so a second server of that name is denied; not testable on Linux beyond this mapping
            raise AutomationError(ANOTHER_APP) from exc
        if family == "AF_UNIX":
            try:
                os.chmod(self.address, 0o600)
            except OSError:
                pass
        self._thread = threading.Thread(target=self._accept_loop, name="webext-accept", daemon=True)
        self._thread.start()

    def close(self) -> None:
        listener, self._listener = self._listener, None
        if listener is None:
            return
        self._closing = True
        try:  # closing a listening socket does not wake accept() on Linux
            mpc.Client(self.address).close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(2)
        listener.close()
        with self._cond:
            conns = list(self._conns)
        for conn in conns:
            conn.close()

    def __enter__(self) -> Listener:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connections(self) -> list[Connection]:
        """Connections that said hello (any protocol)."""

        with self._cond:
            return [c for c in self._conns if c.hello is not None]

    def connection(self, timeout: float = HELLO_TIMEOUT) -> Connection:
        """Most recently active compatible connection, for NEW sessions.

        Waits up to ``timeout`` for a hello; raises :class:`ExtensionMissing`.
        """

        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                ready = [c for c in self._conns if c.hello is not None]
                usable = [c for c in ready if c.compatible]
                if usable:
                    return max(usable, key=lambda c: c.last_active)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExtensionMissing(
                        "Reload or update the dj-digger browser extension (protocol mismatch)"
                        if ready else "The dj-digger browser extension is not connected"
                    )
                self._cond.wait(remaining)

    def session(self, timeout: float = HELLO_TIMEOUT) -> Session:
        return self.connection(timeout).session()

    # -- internals ------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._closing:
            listener = self._listener
            try:
                raw = listener.accept() if listener is not None else None
            except (OSError, EOFError):
                if self._closing:
                    return
                time.sleep(0.1)
                continue
            if raw is None or self._closing:
                if raw is not None:
                    raw.close()
                return
            Connection(self, raw, self._authkey).start()

    def _adopt(self, conn: Connection) -> None:
        with self._cond:
            self._conns.append(conn)
            self._cond.notify_all()

    def _forget(self, conn: Connection) -> None:
        with self._cond:
            if conn in self._conns:
                self._conns.remove(conn)
            self._cond.notify_all()

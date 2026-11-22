"""Private atomic JSON persistence, independent of authentication."""

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from stat import S_IMODE
from typing import Any
from uuid import uuid4

LOGGER = logging.getLogger(__name__)

@contextmanager
def atomic_csv(path: Path):
    temporary = path.with_name(f".dj-digger-{uuid4().hex}.tmp")
    try:
        mode = S_IMODE(path.stat().st_mode) & 0o777
    except FileNotFoundError:
        mode = None
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666 if mode is None else 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            yield stream
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_private_json(
    path: Path, payload: Any, *, ensure_ascii: bool = True, durable: bool = False
) -> None:
    """Atomically write credentials without making them broadly readable.

    Not ``write_text`` followed by a chmod: that creates the file with the umask
    default, usually 0644, writes a live token into it, and only then narrows
    the permissions - so there is a window where any other account on the
    machine can read it. ``mkstemp`` hands back a file that is 0600 before it
    holds a single byte, and ``os.replace`` moves it into place atomically.
    """

    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError as exc:
        LOGGER.debug("Could not tighten permissions on %s: %s", directory, exc)
    write_json_atomic(path, payload, ensure_ascii=ensure_ascii, durable=durable)


def write_json_atomic(
    path: Path, payload: Any, *, private: bool = True, ensure_ascii: bool = True, durable: bool = False
) -> None:
    """Write JSON through a same-directory temporary and ``os.replace``.

    ``private`` keeps mkstemp's 0600; otherwise the file is made 0644 like one
    written the ordinary way (a fixed mode: reading the umask means changing it,
    which is process-wide and races other threads). ``durable`` fsyncs the file
    and its directory, for records that must survive a power cut.
    """

    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=ensure_ascii)
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
        if not private:
            os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        # Never leave a temporary holding the payload behind on the way out.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    if durable:
        from .media import fsync_directory

        fsync_directory(path.parent)

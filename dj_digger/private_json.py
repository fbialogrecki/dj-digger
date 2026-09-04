"""Private atomic JSON persistence, independent of authentication."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

def write_private_json(
    path: Path, payload: dict[str, Any], *, ensure_ascii: bool = True
) -> None:
    """Atomically write credentials without making them broadly readable.

    Not ``write_text`` followed by a chmod: that creates the file with the umask
    default, usually 0644, writes a live token into it, and only then narrows
    the permissions - so there is a window where any other account on the
    machine can read it. ``mkstemp`` hands back a file that is 0600 before it
    holds a single byte, and ``os.replace`` moves it into place atomically,
    which is also how ``config``, ``state`` and ``library`` already write.
    """

    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError as exc:
        LOGGER.debug("Could not tighten permissions on %s: %s", directory, exc)

    descriptor, temporary = tempfile.mkstemp(dir=str(directory), prefix=".auth-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=ensure_ascii)
        os.replace(temporary, path)
    except BaseException:
        # Never leave a temporary holding the token behind on the way out.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise



"""Remember which tracks you already grabbed, across every playlist.

Status lives in SQLite, keyed by SoundCloud track id, so buying a track once
marks it in every crate that contains it.

Until 0.9 every change was also mirrored into state.json, which nothing ever
read back - ``get`` has always asked SQLite. The mirror existed to be migrated
from, and it cost a full rewrite of the file on every single mark, which is why
a library scan needed ``batched()`` to hold it back. The current database has no
JSON import path; a state.json written by an older version is left alone.
"""

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from .db import database, default_db_path
from .paths import data_dir

NEW = "new"
OPENED = "opened"
SKIP = "skip"
GOT = "got"
STATUSES = (NEW, OPENED, SKIP, GOT)

LOGGER = logging.getLogger(__name__)


def default_state_path() -> Path:
    return data_dir() / "state.json"


class TrackState:
    """Track status, stored in SQLite.

    ``path`` still names state.json rather than the database, because it is what
    callers pass to point the whole store somewhere else - the tests do, and the
    database that goes with it is the one beside it.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_state_path()
        db_path = (
            self.path.parent / "digger.db"
            if self.path.suffix == ".json"
            else default_db_path()
        )
        self.db = database(db_path)
        self._lock = threading.Lock()

    def get(self, key: str) -> str:
        with self._lock:
            status = self.db.get_track_status(key)
            return status if status in STATUSES else NEW

    def set(self, key: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status: {status}")
        updated = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            self.db.set_track_status(key, status, updated)
            # A direct user decision is no longer contingent on a particular
            # file. Automated scans/downloads use set_local_file instead.
            self.db.delete_track_local_file(key)

    def local_file(self, key: str) -> str | None:
        with self._lock:
            return self.db.get_track_local_file(key)

    def set_local_file(self, key: str, path: str | Path) -> None:
        updated = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            self.db.set_track_status(key, GOT, updated)
            self.db.set_track_local_file(key, str(path))

    def clear_local_file(self, key: str) -> bool:
        """Forget a missing file and undo only the GOT that depended on it."""

        updated = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            if self.db.get_track_local_file(key) is None:
                return False
            self.db.delete_track_local_file(key)
            if self.db.get_track_status(key) == GOT:
                self.db.set_track_status(key, NEW, updated)
            return True

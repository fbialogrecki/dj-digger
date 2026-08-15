"""Remember which tracks you already grabbed, across every playlist.

Status is stored in SQLite and synced to state.json for backward compatibility.
"""

import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .db import Database, default_db_path

NEW = "new"
OPENED = "opened"
SKIP = "skip"
GOT = "got"
STATUSES = (NEW, OPENED, SKIP, GOT)

STATE_VERSION = 1

LOGGER = logging.getLogger(__name__)


def default_state_path() -> Path:
    data_dir = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "dj-digger"
    return data_dir / "state.json"


class TrackState:
    """SQLite-backed status store with state.json synchronization."""

    def __init__(self, path: Path | None = None) -> None:
        self.json_path = Path(path) if path else default_state_path()
        self.path = self.json_path
        db_path = self.json_path.parent / "digger.db" if self.json_path.suffix == ".json" else default_db_path()
        self.db = Database(db_path)
        self._entries: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self._defer_saves = False
        self._save_pending = False
        self._load_json()

    @contextmanager
    def batched(self) -> Iterator[None]:
        """Write the JSON mirror once at the end instead of once per change.

        ``set`` rewrites the whole of state.json every time, so marking a few
        hundred tracks in a row - which is exactly what a library scan does -
        writes the same file a few hundred times over. SQLite is unaffected;
        this only holds back the mirror. Not reentrant, and not meant to be.
        """

        self._defer_saves = True
        try:
            yield
        finally:
            self._defer_saves = False
            if self._save_pending:
                self._save_pending = False
                self.save()

    def _load_json(self) -> None:
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("tracks"), dict):
                for k, v in raw["tracks"].items():
                    if isinstance(v, dict) and "status" in v:
                        self.db.set_track_status(k, v["status"], v.get("updated", ""))
                        self._entries[k] = v
        except Exception:
            pass

    def save(self) -> None:
        if self._defer_saves:
            self._save_pending = True
            return
        payload = {"version": STATE_VERSION, "tracks": self._entries}
        try:
            self.json_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.json_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.json_path)
        except OSError as exc:
            LOGGER.warning("Could not save state to %s: %s", self.json_path, exc)

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
            if status == NEW:
                self._entries.pop(key, None)
            else:
                self._entries[key] = {"status": status, "updated": updated}
            self.save()

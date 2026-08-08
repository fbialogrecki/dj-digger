"""Remember which tracks you already grabbed, across every playlist.

Status is stored in SQLite and synced to state.json for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

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

    def __init__(self, path: Optional[Path] = None) -> None:
        self.json_path = Path(path) if path else default_state_path()
        self.path = self.json_path
        db_path = self.json_path.parent / "digger.db" if self.json_path.suffix == ".json" else default_db_path()
        self.db = Database(db_path)
        self._entries: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()
        self._load_json()

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
        updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self.db.set_track_status(key, status, updated)
            if status == NEW:
                self._entries.pop(key, None)
            else:
                self._entries[key] = {"status": status, "updated": updated}
            self.save()

    def counts(self) -> Dict[str, int]:
        return self.db.get_status_counts()

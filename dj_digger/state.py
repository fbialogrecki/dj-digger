"""Remember which tracks you already grabbed, across every playlist.

Status is keyed by SoundCloud track id rather than by playlist, so a track you
bought once shows up as already handled the next time it turns up in someone
else's set. Tracks without an id (the saved-HTML path) fall back to their URL.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from platformdirs import user_data_dir

NEW = "new"
OPENED = "opened"
SKIP = "skip"
GOT = "got"
STATUSES = (NEW, OPENED, SKIP, GOT)

STATE_VERSION = 1

LOGGER = logging.getLogger(__name__)


def default_state_path() -> Path:
    return Path(user_data_dir("dj-digger")) / "state.json"


class TrackState:
    """A tiny JSON-backed status store."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_state_path()
        self._entries: Dict[str, Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            LOGGER.warning("Ignoring unreadable state file %s: %s", self.path, exc)
            return

        entries = raw.get("tracks") if isinstance(raw, dict) else None
        if isinstance(entries, dict):
            self._entries = {
                key: value for key, value in entries.items() if isinstance(value, dict)
            }

    def save(self) -> None:
        payload = {"version": STATE_VERSION, "tracks": self._entries}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            LOGGER.warning("Could not save state to %s: %s", self.path, exc)

    def get(self, key: str) -> str:
        entry = self._entries.get(key)
        if not entry:
            return NEW
        status = entry.get("status", NEW)
        return status if status in STATUSES else NEW

    def set(self, key: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status: {status}")
        if status == NEW:
            self._entries.pop(key, None)
        else:
            self._entries[key] = {
                "status": status,
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        self.save()

    def counts(self) -> Dict[str, int]:
        counts = {status: 0 for status in STATUSES}
        for entry in self._entries.values():
            status = entry.get("status", NEW)
            if status in counts:
                counts[status] += 1
        return counts

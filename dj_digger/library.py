"""A local library of imported crates.

Stores whole tracks rather than categorised links, so that improving the
categorisation improves crates you imported months ago. Stream URLs are
deliberately not stored - they expire, and are fetched fresh on playback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Crate, Track

VERSION = 1

LOGGER = logging.getLogger(__name__)


def crates_dir() -> Path:
    data_dir = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "dj-digger"
    return data_dir / "crates"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug_for(source: str) -> str:
    """A stable filename for a source, so re-importing updates instead of duplicating."""

    source = source.strip()
    readable = re.sub(r"^https?://(www\.)?soundcloud\.com/", "", source)
    readable = re.sub(r"[^A-Za-z0-9]+", "-", readable).strip("-").lower()[:60]
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:6]
    return f"{readable or 'crate'}-{digest}"


def _track_to_json(track: Track) -> Dict[str, Any]:
    return asdict(track)


def _track_from_json(data: Dict[str, Any]) -> Track:
    # Filtered by field name so a crate written by another version still loads.
    known = {f.name for f in fields(Track)}
    values = {key: value for key, value in data.items() if key in known}
    values["extra_links"] = [tuple(pair) for pair in values.get("extra_links") or []]
    return Track(**values)


@dataclass
class CrateRecord:
    source: str
    title: str
    tracks: List[Track] = field(default_factory=list)
    removed_track_keys: List[str] = field(default_factory=list)
    imported_at: str = ""
    refreshed_at: Optional[str] = None
    # True for crates read out of an export file, which carries fewer fields than
    # the API does (no genre, no description). Refreshing fills them in.
    partial: bool = False

    @property
    def slug(self) -> str:
        return slug_for(self.source)

    @property
    def path(self) -> Path:
        return crates_dir() / f"{self.slug}.json"

    @property
    def active_tracks(self) -> List[Track]:
        removed = set(self.removed_track_keys)
        return [track for track in self.tracks if track.key not in removed]

    def remove(self, track_key: str) -> None:
        if track_key not in self.removed_track_keys:
            self.removed_track_keys.append(track_key)

    def restore(self, track_key: str) -> None:
        if track_key in self.removed_track_keys:
            self.removed_track_keys.remove(track_key)

    @classmethod
    def from_crate(cls, crate: Crate, *, partial: bool = False) -> "CrateRecord":
        return cls(
            source=crate.source,
            title=crate.title or crate.source,
            tracks=list(crate.tracks),
            imported_at=_now(),
            partial=partial,
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": VERSION,
            "source": self.source,
            "title": self.title,
            "imported_at": self.imported_at,
            "refreshed_at": self.refreshed_at,
            "partial": self.partial,
            "removed_track_keys": self.removed_track_keys,
            "tracks": [_track_to_json(track) for track in self.tracks],
        }

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "CrateRecord":
        return cls(
            source=data.get("source") or "",
            title=data.get("title") or data.get("source") or "crate",
            tracks=[_track_from_json(item) for item in data.get("tracks") or []],
            removed_track_keys=list(data.get("removed_track_keys") or []),
            imported_at=data.get("imported_at") or "",
            refreshed_at=data.get("refreshed_at"),
            partial=bool(data.get("partial")),
        )


def save(record: CrateRecord) -> Path:
    path = record.path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(record.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def load(slug: str) -> CrateRecord:
    path = crates_dir() / f"{slug}.json"
    return CrateRecord.from_json(json.loads(path.read_text(encoding="utf-8")))


def list_crates() -> List[CrateRecord]:
    """Every saved crate, sorted by title. Unreadable files are skipped, not fatal."""

    records = []
    for path in sorted(crates_dir().glob("*.json")):
        try:
            records.append(CrateRecord.from_json(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as exc:
            LOGGER.warning("Skipping unreadable crate %s: %s", path, exc)
    return sorted(records, key=lambda record: record.title.lower())


def delete(slug: str) -> None:
    (crates_dir() / f"{slug}.json").unlink(missing_ok=True)


def refresh(record: CrateRecord, crate: Crate, *, partial: bool = False) -> CrateRecord:
    """Replace the tracks from a fresh dig, keeping what you deleted locally deleted."""

    record.tracks = list(crate.tracks)
    record.title = crate.title or record.title
    record.refreshed_at = _now()
    record.partial = partial
    return record


def remember(crate: Crate, *, partial: bool = False) -> CrateRecord:
    """Save a dug crate, updating the existing record for that source if there is one."""

    slug = slug_for(crate.source)
    try:
        record = refresh(load(slug), crate, partial=partial)
    except (OSError, ValueError):
        record = CrateRecord.from_crate(crate, partial=partial)
    save(record)
    return record

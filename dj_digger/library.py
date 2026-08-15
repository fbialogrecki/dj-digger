"""A local library of imported crates.

Stores whole tracks rather than categorised links, so that improving the
categorisation improves crates you imported months ago. Stream URLs are
deliberately not stored - they expire, and are fetched fresh on playback.

ponytail: every crate is written twice, to crates/<slug>.json and to the crates
table, and ``list_crates`` merges both by source. See the same note in
``state``: one of the two is enough, and dropping the JSON half would take
about forty lines with it.
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .db import Database
from .models import Crate, Track

VERSION = 1

LOGGER = logging.getLogger(__name__)


def crates_dir() -> Path:
    data_dir = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "dj-digger"
    return data_dir / "crates"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slug_for(source: str) -> str:
    """A stable filename for a source, so re-importing updates instead of duplicating."""

    source = source.strip()
    readable = re.sub(r"^https?://(www\.)?soundcloud\.com/", "", source)
    readable = re.sub(r"[^A-Za-z0-9]+", "-", readable).strip("-").lower()[:60]
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:6]
    return f"{readable or 'crate'}-{digest}"


def _track_to_json(track: Track) -> dict[str, Any]:
    return asdict(track)


def _track_from_json(data: dict[str, Any]) -> Track:
    # Filtered by field name so a crate written by another version still loads.
    known = {f.name for f in fields(Track)}
    values = {key: value for key, value in data.items() if key in known}
    values["extra_links"] = [tuple(pair) for pair in values.get("extra_links") or []]
    return Track(**values)


@dataclass
class CrateRecord:
    source: str
    title: str
    tracks: list[Track] = field(default_factory=list)
    removed_track_keys: list[str] = field(default_factory=list)
    imported_at: str = ""
    refreshed_at: str | None = None
    partial: bool = False

    @property
    def slug(self) -> str:
        return slug_for(self.source)

    @property
    def path(self) -> Path:
        return crates_dir() / f"{self.slug}.json"

    @property
    def active_tracks(self) -> list[Track]:
        removed = set(self.removed_track_keys)
        return [track for track in self.tracks if track.key not in removed]

    def remove(self, track_key: str) -> None:
        if track_key not in self.removed_track_keys:
            self.removed_track_keys.append(track_key)

    def restore(self, track_key: str) -> None:
        if track_key in self.removed_track_keys:
            self.removed_track_keys.remove(track_key)

    @classmethod
    def from_crate(cls, crate: Crate, *, partial: bool = False) -> Self:
        return cls(
            source=crate.source,
            title=crate.title or crate.source,
            tracks=list(crate.tracks),
            imported_at=_now(),
            partial=partial,
        )

    def to_json(self) -> dict[str, Any]:
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
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            source=data.get("source") or "",
            title=data.get("title") or data.get("source") or "crate",
            tracks=[_track_from_json(item) for item in data.get("tracks") or []],
            removed_track_keys=list(data.get("removed_track_keys") or []),
            imported_at=data.get("imported_at") or "",
            refreshed_at=data.get("refreshed_at"),
            partial=bool(data.get("partial")),
        )


def _db() -> Database:
    return Database(crates_dir().parent / "digger.db")


def save(record: CrateRecord) -> Path:
    if not record.imported_at:
        record.imported_at = _now()
    path = record.path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = record.to_json()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)
    _db().save_crate(
        source=record.source,
        title=record.title,
        declared_count=len(record.tracks),
        updated=record.refreshed_at or record.imported_at,
        tracks_data=data.get("tracks", [])
    )
    return path


def load(slug: str) -> CrateRecord:
    path = crates_dir() / f"{slug}.json"
    if path.exists():
        return CrateRecord.from_json(json.loads(path.read_text(encoding="utf-8")))
    for crate_info in _db().list_crates():
        rec = _db().load_crate(crate_info["source"])
        if rec and slug_for(rec["source"]) == slug:
            return CrateRecord.from_json(rec)
    raise FileNotFoundError(f"Crate slug not found: {slug}")


def list_crates() -> list[CrateRecord]:
    records_by_source: dict[str, CrateRecord] = {}

    for path in sorted(crates_dir().glob("*.json")):
        try:
            rec = CrateRecord.from_json(json.loads(path.read_text(encoding="utf-8")))
            if rec.source:
                records_by_source[rec.source] = rec
        except (OSError, ValueError) as exc:
            LOGGER.warning("Skipping unreadable crate %s: %s", path, exc)

    try:
        for crate_info in _db().list_crates():
            source = crate_info.get("source")
            if source and source not in records_by_source:
                raw_rec = _db().load_crate(source)
                if raw_rec:
                    records_by_source[source] = CrateRecord.from_json(raw_rec)
    except Exception as exc:
        LOGGER.warning("Could not read crates from SQLite: %s", exc)

    return sorted(records_by_source.values(), key=lambda record: record.title.lower())


def delete(slug: str) -> None:
    path = crates_dir() / f"{slug}.json"
    if path.exists():
        try:
            rec = CrateRecord.from_json(json.loads(path.read_text(encoding="utf-8")))
            if rec and rec.source:
                _db().delete_crate(rec.source)
        except Exception:
            pass
        path.unlink(missing_ok=True)


def refresh(record: CrateRecord, crate: Crate, *, partial: bool = False) -> CrateRecord:
    record.tracks = list(crate.tracks)
    record.title = crate.title or record.title
    record.refreshed_at = _now()
    record.partial = partial
    return record


def remember(crate: Crate, *, partial: bool = False) -> CrateRecord:
    slug = slug_for(crate.source)
    try:
        record = refresh(load(slug), crate, partial=partial)
    except (OSError, ValueError):
        record = CrateRecord.from_crate(crate, partial=partial)
    save(record)
    return record

"""A local library of imported crates.

Stores whole tracks rather than categorised links, so that improving the
categorisation improves crates you imported months ago. Stream URLs are
deliberately not stored - they expire, and are fetched fresh on playback.

Since 0.9 there is one copy, in SQLite. Crates used to be written to both
crates/<slug>.json and the crates table, with ``list_crates`` merging the two by
source - which meant two answers to "what is in this crate" and, because the
table only had room for five of the record's fields, a fallback that silently
lost the import date, the NEW marks and the partial flag. Files written by 0.8
and earlier are imported once and then left alone.
"""

import hashlib
import logging
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .db import Database, database
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
    # What the last refresh brought in that the crate did not already have.
    new_track_keys: list[str] = field(default_factory=list)
    imported_at: str = ""
    refreshed_at: str | None = None
    partial: bool = False

    @property
    def slug(self) -> str:
        return slug_for(self.source)

    @property
    def active_tracks(self) -> list[Track]:
        removed = set(self.removed_track_keys)
        kept = [track for track in self.tracks if track.key not in removed]
        # What the last refresh added goes to the top; sorted is stable, so the
        # playlist's own order survives inside each half.
        arrived = set(self.new_track_keys)
        return sorted(kept, key=lambda track: track.key not in arrived)

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
            "new_track_keys": self.new_track_keys,
            "tracks": [_track_to_json(track) for track in self.tracks],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            source=data.get("source") or "",
            title=data.get("title") or data.get("source") or "crate",
            tracks=[_track_from_json(item) for item in data.get("tracks") or []],
            removed_track_keys=list(data.get("removed_track_keys") or []),
            new_track_keys=list(data.get("new_track_keys") or []),
            imported_at=data.get("imported_at") or "",
            refreshed_at=data.get("refreshed_at"),
            partial=bool(data.get("partial")),
        )


def _db() -> Database:
    return database(crates_dir().parent / "digger.db")


def save(record: CrateRecord) -> None:
    if not record.imported_at:
        record.imported_at = _now()
    _db().save_crate(record.to_json())


def load(slug: str) -> CrateRecord:
    for raw in _db().all_crates():
        source = raw.get("source") or ""
        if source and slug_for(source) == slug:
            return CrateRecord.from_json(raw)
    raise FileNotFoundError(f"Crate slug not found: {slug}")


def list_crates() -> list[CrateRecord]:
    try:
        raw_records = _db().all_crates()
    except Exception as exc:
        LOGGER.warning("Could not read crates from SQLite: %s", exc)
        return []
    records = [CrateRecord.from_json(raw) for raw in raw_records if raw.get("source")]
    return sorted(records, key=lambda record: record.title.lower())


def delete(slug: str) -> None:
    """Remove a crate from the library.

    Until 0.9 all of this sat inside ``if the JSON file exists``, so a crate whose
    row outlived its file could not be deleted at all: the row survived,
    ``list_crates`` kept returning it, and the sidebar drew it again the moment it
    reloaded - pressing X did nothing, every time.
    """

    for raw in _db().all_crates():
        source = raw.get("source") or ""
        if source and slug_for(source) == slug:
            _db().delete_crate(source)
            return


def refresh(record: CrateRecord, crate: Crate, *, partial: bool = False) -> CrateRecord:
    known = {track.key for track in record.tracks}
    arrived = [track.key for track in crate.tracks if track.key not in known]
    if arrived:
        # A refresh that brought nothing keeps the previous batch marked, so
        # pressing r twice does not lose what the first press turned up.
        record.new_track_keys = arrived
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

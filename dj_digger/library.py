"""A local library of imported crates.

Stores whole tracks rather than categorised links, so that improving the
categorisation improves crates you imported months ago. Stream URLs are
deliberately not stored - they expire, and are fetched fresh on playback.

Since 0.9 there is one copy, in SQLite. Crates used to be written to both
crates/<slug>.json and the crates table, with ``list_crates`` merging the two by
source - which meant two answers to "what is in this crate" and, because the
table only had room for five of the record's fields, a fallback that silently
lost the import date, the NEW marks and the partial flag. 
"""

import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any, Self

from .db import database
from .models import Crate, Track

VERSION = 1

LOGGER = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
        # asdict recurses into the Track dataclasses too.
        return {"version": VERSION, **asdict(self)}

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


def save(record: CrateRecord) -> None:
    if not record.imported_at:
        record.imported_at = _now()
    database().save_crate(record.to_json())


def load(source: str) -> CrateRecord | None:
    """The stored record for a source, or None - source is the primary key."""

    raw = database().load_crate(source.strip())
    return CrateRecord.from_json(raw) if raw else None


def list_crates() -> list[CrateRecord]:
    try:
        raw_records = database().all_crates()
    except Exception as exc:
        LOGGER.warning("Could not read crates from SQLite: %s", exc)
        return []
    records = [CrateRecord.from_json(raw) for raw in raw_records if raw.get("source")]
    return sorted(records, key=lambda record: record.title.lower())


def delete(source: str) -> None:
    """Remove a crate from the library; deleting a missing one is a no-op."""

    database().delete_crate(source.strip())


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
    stored = load(crate.source)
    if stored is not None:
        record = refresh(stored, crate, partial=partial)
    else:
        record = CrateRecord.from_crate(crate, partial=partial)
    save(record)
    return record

"""A local library of imported crates.

Stores whole tracks rather than categorised links, so that improving the
categorisation improves crates you imported months ago. Stream URLs are
deliberately not stored - they expire, and are fetched fresh on playback.

Since 0.9 there is one copy, in SQLite. Crates used to be written to both
crates/<slug>.json and the crates table, with the listing merging the two by
source - which meant two answers to "what is in this crate" and, because the
table only had room for five of the record's fields, a fallback that silently
lost the import date, the NEW marks and the partial flag. The sidebar reads
``list_crate_headers`` and loads one crate at a time; ``list_crates`` is the
whole library with every track attached, which only the tests need.
"""


from .crate_models import CrateHeader, CrateRecord, _now
from .db import database
from .models import Crate


def save(record: CrateRecord) -> None:
    if not record.imported_at:
        record.imported_at = _now()
    database().save_crate(record.to_json())


def load(source: str) -> CrateRecord | None:
    """The stored record for a source, or None - source is the primary key."""

    raw = database().load_crate(source.strip())
    return CrateRecord.from_json(raw) if raw else None


def list_crate_headers() -> list[CrateHeader]:
    raw = database().list_crate_headers()
    headers = [CrateHeader(**row) for row in raw if row.get("source")]
    return sorted(headers, key=lambda header: header.title.lower())


def list_crates() -> list[CrateRecord]:
    records = (load(header.source) for header in list_crate_headers())
    return [record for record in records if record is not None]


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


def remember(crate: Crate, *, partial: bool = False, generation: str | None = None) -> CrateRecord | None:
    incoming = CrateRecord.from_crate(crate, partial=partial).to_json()
    raw = database().remember_collection(incoming, generation)
    return CrateRecord.from_json(raw) if raw is not None else None

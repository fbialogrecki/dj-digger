"""Test-side view of the crate table: what the app reads and writes through ``db``."""

from dj_digger.crate_models import CrateRecord, _now
from dj_digger.db import database
from dj_digger.services.library import LibraryService
from dj_digger.state import TrackState


def save(record: CrateRecord) -> None:
    if not record.imported_at:
        record.imported_at = _now()
    database().save_crate(record.to_json())


def load(source: str) -> CrateRecord | None:
    raw = database().load_crate(source.strip())
    return CrateRecord.from_json(raw) if raw else None


def list_crates() -> list[CrateRecord]:
    return [load(header.source) for header in LibraryService(TrackState()).headers()]

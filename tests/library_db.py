"""Test-side view of the crate table: what the app reads and writes through ``db``."""

from dj_digger.crate_models import CrateHeader, CrateRecord, _now
from dj_digger.db import database


def save(record: CrateRecord) -> None:
    if not record.imported_at:
        record.imported_at = _now()
    database().save_crate(record.to_json())


def load(source: str) -> CrateRecord | None:
    raw = database().load_crate(source.strip())
    return CrateRecord.from_json(raw) if raw else None


def list_crate_headers() -> list[CrateHeader]:
    rows = database().list_crate_headers()
    return sorted((CrateHeader(**row) for row in rows if row.get("source")), key=lambda h: h.title.lower())


def list_crates() -> list[CrateRecord]:
    return [load(header.source) for header in list_crate_headers()]


def delete(source: str) -> None:
    database().delete_crate(source.strip())

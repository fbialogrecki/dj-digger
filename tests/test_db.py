"""Tests for SQLite database engine."""

from pathlib import Path
from dj_digger.db import Database


def test_database_init_and_state(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    # Test track status
    assert db.get_track_status("12345") == "new"
    db.set_track_status("12345", "got", "2026-08-08T12:00:00")
    assert db.get_track_status("12345") == "got"

    counts = db.get_status_counts()
    assert counts["got"] == 1


def test_database_crates(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    db.save_crate("http://sc.com/set", "Test Crate", 10, "2026-08-08", [{"title": "Track 1"}])
    crate = db.load_crate("http://sc.com/set")
    assert crate is not None
    assert crate["title"] == "Test Crate"
    assert len(crate["tracks"]) == 1

    crates_list = db.list_crates()
    assert len(crates_list) == 1
    assert crates_list[0]["source"] == "http://sc.com/set"

    db.delete_crate("http://sc.com/set")
    assert db.load_crate("http://sc.com/set") is None

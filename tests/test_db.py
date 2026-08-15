"""Tests for SQLite database engine."""

import json
from pathlib import Path

from dj_digger.db import Database


def test_database_init_and_state(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    # Test track status
    assert db.get_track_status("12345") == "new"
    db.set_track_status("12345", "got", "2026-08-08T12:00:00")
    assert db.get_track_status("12345") == "got"

    db.set_track_status("12345", "new", "2026-08-08T12:01:00")
    assert db.get_track_status("12345") == "new"


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


def test_the_legacy_import_does_not_resurrect_a_cleared_status(tmp_path: Path) -> None:
    """It used to run on every Database(), and library._db() builds one per call.

    So clearing a mark and then touching the crate library put the old mark
    straight back, read out of the state.json mirror that had not caught up yet.
    """

    (tmp_path / "state.json").write_text(
        json.dumps({"version": 1, "tracks": {"42": {"status": "got", "updated": ""}}}),
        encoding="utf-8",
    )
    db_path = tmp_path / "digger.db"

    db = Database(db_path)
    assert db.get_track_status("42") == "got", "the legacy file is still imported once"

    db.set_track_status("42", "new", "2026-08-15T00:00:00")
    Database(db_path)

    assert db.get_track_status("42") == "new"

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

    db.set_track_status("12345", "new", "2026-08-08T12:01:00")
    assert db.get_track_status("12345") == "new"


def test_database_crates(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    db.save_crate(
        {
            "source": "http://sc.com/set",
            "title": "Test Crate",
            "imported_at": "2026-08-08",
            "refreshed_at": None,
            "partial": True,
            "new_track_keys": ["k1"],
            "removed_track_keys": [],
            "tracks": [{"title": "Track 1"}],
        }
    )
    crate = db.load_crate("http://sc.com/set")
    assert crate is not None
    assert crate["title"] == "Test Crate"
    assert len(crate["tracks"]) == 1
    # The fields the old five-column table had nowhere to put.
    assert crate["imported_at"] == "2026-08-08"
    assert crate["partial"] is True
    assert crate["new_track_keys"] == ["k1"]

    crates_list = db.all_crates()
    assert len(crates_list) == 1
    assert crates_list[0]["source"] == "http://sc.com/set"

    db.delete_crate("http://sc.com/set")
    assert db.load_crate("http://sc.com/set") is None


def test_an_old_shaped_crates_table_is_dropped_and_rebuilt(tmp_path: Path) -> None:
    """A crates table written by <=0.8 has five chosen columns, no record_json.

    CREATE TABLE IF NOT EXISTS would silently keep that shape and every crate
    read would then fail on the missing column - invisibly, because
    list_crates swallows the error and shows an empty library.
    """

    import sqlite3

    db_path = tmp_path / "digger.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE crates (
                source TEXT PRIMARY KEY,
                title TEXT,
                declared_count INTEGER,
                updated TEXT,
                tracks_json TEXT
            )"""
        )

    db = Database(db_path)
    db.save_crate(
        {"source": "s://one", "title": "One", "imported_at": "2026-01-01", "tracks": []}
    )
    crate = db.load_crate("s://one")
    assert crate is not None
    assert crate["title"] == "One"

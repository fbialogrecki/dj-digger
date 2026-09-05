"""Tests for SQLite database engine."""

from pathlib import Path

from dj_digger.db import Database


def test_database_init_and_state(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    # Test track status
    assert db.all_track_statuses() == {}
    db.set_track_status("12345", "got")
    assert db.all_track_statuses() == {"12345": "got"}

    db.set_track_status("12345", "new")
    assert db.all_track_statuses() == {}


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

    assert [header["source"] for header in db.list_crate_headers()] == ["http://sc.com/set"]

    db.delete_crate("http://sc.com/set")
    assert db.load_crate("http://sc.com/set") is None


def test_an_old_shaped_crates_table_is_dropped_and_rebuilt(tmp_path: Path) -> None:
    """A crates table written by <=0.8 has five chosen columns, no record_json.

    CREATE TABLE IF NOT EXISTS would silently keep that shape and every crate
    read would then fail on the missing column - invisibly, because
    list_crate_headers swallows the error and shows an empty library.
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


def test_an_old_shaped_local_files_table_is_dropped_and_rebuilt(tmp_path: Path) -> None:
    """Until 1.0 the cache also stored size, artist and title, all NOT NULL."""

    import sqlite3

    db_path = tmp_path / "digger.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE local_files (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                artist TEXT NOT NULL,
                title TEXT NOT NULL,
                normalized_stem TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO local_files VALUES ('/old.mp3', 1.0, 10, '', 'old', 'old')"
        )

    db = Database(db_path)
    db.upsert_local_files([("/new.mp3", 2.0, "new")])

    assert db.get_cached_files() == {"/new.mp3": (2.0, "new")}


def test_crate_headers_come_without_the_tracks(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    db.save_crate(
        {
            "source": "https://soundcloud.com/a/sets/one",
            "title": "One",
            "imported_at": "2026-01-01T00:00:00+00:00",
            "partial": True,
            "tracks": [{"title": "x"}, {"title": "y"}, {"title": "z"}],
        }
    )

    headers = db.list_crate_headers()

    assert headers == [
        {
            "source": "https://soundcloud.com/a/sets/one",
            "title": "One",
            "updated": "2026-01-01T00:00:00+00:00",
            "partial": True,
        }
    ]


def test_information_release_refuses_newer_library_without_changes(tmp_path):
    import sqlite3

    import pytest
    path = tmp_path / 'future.db'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA user_version=2')
        conn.execute('CREATE TABLE preserve (value TEXT)')
        conn.execute("INSERT INTO preserve VALUES ('user data')")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match='left unchanged'):
        Database(path)
    assert path.read_bytes() == before

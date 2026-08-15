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


def test_a_whole_0_8_library_survives_the_move_to_sqlite(tmp_path: Path, monkeypatch) -> None:
    """The upgrade path: state.json plus crates/*.json, read once, never again.

    The old crates table had room for five fields, so a record that fell back to
    it lost its import date, its partial flag and its NEW marks. Those are what
    this asserts, because they are what had nowhere to live once the files went.
    """

    from dj_digger import library, state

    (tmp_path / "state.json").write_text(
        json.dumps({"version": 1, "tracks": {"77": {"status": "skip", "updated": "2026-01-01"}}}),
        encoding="utf-8",
    )
    crates = tmp_path / "crates"
    crates.mkdir()
    (crates / "a-crate-abc123.json").write_text(
        json.dumps(
            {
                "version": 1,
                "source": "https://soundcloud.com/a/sets/b",
                "title": "Old Crate",
                "imported_at": "2026-02-03T10:00:00+00:00",
                "refreshed_at": "2026-03-04T11:00:00+00:00",
                "partial": True,
                "removed_track_keys": ["gone"],
                "new_track_keys": ["fresh"],
                "tracks": [
                    {"title": "T1", "permalink_url": "https://soundcloud.com/a/1", "id": 1},
                    {"title": "T2", "permalink_url": "https://soundcloud.com/a/2", "id": 2},
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(library, "crates_dir", lambda: crates)
    monkeypatch.setattr(state, "default_state_path", lambda: tmp_path / "state.json")

    assert state.TrackState(tmp_path / "state.json").get("77") == "skip"

    listed = library.list_crates()
    assert [record.title for record in listed] == ["Old Crate"]
    record = listed[0]
    assert record.imported_at == "2026-02-03T10:00:00+00:00"
    assert record.refreshed_at == "2026-03-04T11:00:00+00:00"
    assert record.partial is True
    assert record.new_track_keys == ["fresh"]
    assert record.removed_track_keys == ["gone"]
    assert [track.id for track in record.tracks] == [1, 2]

    # And the files are left exactly where they were - an upgrade does not get
    # to delete somebody's only copy.
    assert (crates / "a-crate-abc123.json").exists()
    assert (tmp_path / "state.json").exists()


def test_a_restart_does_not_bring_the_legacy_file_back(tmp_path: Path, monkeypatch) -> None:
    """The old JSON file stays on disk, so the guard has to outlive the process.

    It used to be a set held in memory: after the upgrade, editing a crate and
    restarting re-read the file and threw the edit away. The guard is
    ``user_version`` now, which is in the database itself.
    """

    from dj_digger import db as db_module
    from dj_digger import library

    crates = tmp_path / "crates"
    crates.mkdir()
    (crates / "c.json").write_text(
        json.dumps(
            {
                "source": "s://one",
                "title": "One",
                "tracks": [{"title": "T1", "permalink_url": "https://soundcloud.com/a/1", "id": 1}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "crates_dir", lambda: crates)

    record = library.list_crates()[0]
    assert record.title == "One", "the file is imported on the first open"
    record.remove(record.tracks[0].key)
    library.save(record)

    # What a restart does: nothing cached, a brand new Database over the same file.
    db_module._INSTANCES.clear()

    assert library.list_crates()[0].removed_track_keys == ["1"], "the edit has to survive"

"""Tests for SQLite database engine."""

from pathlib import Path

import pytest

from dj_digger.db import Database
from dj_digger.schema import UnsupportedSchema


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


def test_an_old_shaped_crates_table_is_rejected_without_changes(tmp_path: Path) -> None:
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

    before = db_path.read_bytes()
    with pytest.raises(UnsupportedSchema):
        Database(db_path)
    assert db_path.read_bytes() == before


def test_an_old_shaped_local_files_table_is_rejected_without_changes(tmp_path: Path) -> None:
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

    before = db_path.read_bytes()
    with pytest.raises(UnsupportedSchema):
        Database(db_path)
    assert db_path.read_bytes() == before


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


def test_register_legacy_schema_backs_up_wal_once(tmp_path):
    import sqlite3
    from contextlib import closing

    from dj_digger.schema import DDL

    path = tmp_path / 'library.db'
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        for sql in DDL:
            writer.execute(sql)
        writer.execute("INSERT INTO track_states VALUES ('wal-track', 'skip', 'now')")
        db = Database(path)
        assert db.all_track_statuses() == {'wal-track': 'skip'}
        copies = list((tmp_path / 'backups').glob('*.db'))
        assert len(copies) == 1
        with closing(sqlite3.connect(copies[0])) as saved:
            assert saved.execute('PRAGMA user_version').fetchone()[0] == 0
            assert saved.execute('SELECT key FROM track_states').fetchone()[0] == 'wal-track'
        import os
        if os.name == 'posix':
            assert copies[0].stat().st_mode & 0o777 == 0o600
        db.close()
        Database(path).close()
        assert list((tmp_path / 'backups').glob('*.db')) == copies


def test_backup_failure_does_not_register_version(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing

    from dj_digger import schema

    path = tmp_path / 'library.db'
    with closing(sqlite3.connect(path)) as conn:
        for sql in schema.DDL:
            conn.execute(sql)
    def fail(*args):
        raise OSError('full disk')
    monkeypatch.setattr(schema, 'backup', fail)
    with pytest.raises(OSError, match='full disk'):
        Database(path)
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 0


@pytest.fixture
def legacy_cache_database(tmp_path):
    import sqlite3
    from contextlib import closing

    from dj_digger.schema import DDL

    path = tmp_path / 'legacy.db'
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        # The cache shape shipped before 1.0, independent of the recognizer.
        conn.execute('''CREATE TABLE local_files (
            path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL,
            artist TEXT NOT NULL, title TEXT NOT NULL, normalized_stem TEXT NOT NULL
        )''')
        for sql in DDL:
            if not sql.startswith('CREATE TABLE local_files'):
                conn.execute(sql)
        conn.execute("INSERT INTO local_files VALUES ('C:/Music/one.mp3', 1.5, 42, 'Artist', 'One', 'one')")
        conn.execute("INSERT INTO track_states VALUES ('one', 'got', 'now')")
        conn.execute("INSERT INTO track_local_files VALUES ('one', 'C:/Music/one.mp3')")
        conn.execute('INSERT INTO crates VALUES (?, ?, ?, ?)',
                     ('playlist', 'Saved', 'now', '{"source":"playlist","title":"Saved","tracks":[]}'))
        yield path, conn


@pytest.mark.parametrize('version', [0, 1])
def test_legacy_cache_migration_preserves_library_and_backup(legacy_cache_database, version):
    import sqlite3
    from contextlib import closing

    from dj_digger.schema import expected_signature, signature

    path, writer = legacy_cache_database
    writer.execute(f'PRAGMA user_version={version}')
    before = list(writer.iterdump())
    db = Database(path)
    assert db.all_track_statuses() == {'one': 'got'}
    assert db.all_track_local_files() == {'one': 'C:/Music/one.mp3'}
    assert db.get_cached_files() == {'C:/Music/one.mp3': (1.5, 'one')}
    assert db.load_crate('playlist') == {'source': 'playlist', 'title': 'Saved', 'tracks': []}
    db.upsert_local_files([('C:/Music/two.mp3', 2.0, 'two')])
    db.close()
    assert writer.execute('PRAGMA user_version').fetchone()[0] == 2
    assert signature(writer) == expected_signature(2)
    copies = list((path.parent / 'backups').glob('*.db'))
    assert len(copies) == 1
    with closing(sqlite3.connect(copies[0])) as saved:
        assert saved.execute('PRAGMA user_version').fetchone()[0] == version
        assert list(saved.iterdump()) == before
    Database(path).close()
    assert list((path.parent / 'backups').glob('*.db')) == copies


@pytest.mark.parametrize('failure', ['backup', 'migration'])
def test_legacy_cache_failure_preserves_original(legacy_cache_database, monkeypatch, failure):
    import sqlite3

    from dj_digger import schema

    path, writer = legacy_cache_database
    writer.execute('PRAGMA user_version=1')
    before = list(writer.iterdump())
    if failure == 'backup':
        def fail(*args):
            raise OSError('backup denied')
        monkeypatch.setattr(schema, 'backup', fail)
    else:
        # Fail after the legacy columns have been dropped, exercising rollback.
        monkeypatch.setattr(schema, 'MEDIA_DDL', ('CREATE TABLE local_files (duplicate TEXT)',))
    with pytest.raises((OSError, sqlite3.OperationalError)):
        Database(path)
    assert writer.execute('PRAGMA user_version').fetchone()[0] == 1
    assert list(writer.iterdump()) == before


def test_legacy_cache_with_unknown_constraint_is_rejected(legacy_cache_database):
    path, writer = legacy_cache_database
    writer.execute('PRAGMA user_version=1')
    writer.execute('CREATE UNIQUE INDEX unexpected ON local_files(size)')
    before = list(writer.iterdump())
    with pytest.raises(UnsupportedSchema):
        Database(path)
    assert list(writer.iterdump()) == before
    assert not (path.parent / 'backups').exists()


@pytest.mark.parametrize('version', [-1, 2, 99])
def test_unknown_version_leaves_database_unchanged(tmp_path, version):
    import sqlite3
    from contextlib import closing

    from dj_digger.schema import DDL

    path = tmp_path / 'library.db'
    with closing(sqlite3.connect(path)) as conn:
        for sql in DDL:
            conn.execute(sql)
        conn.execute(f'PRAGMA user_version={version}')
    before = path.read_bytes()
    with pytest.raises(UnsupportedSchema):
        Database(path)
    assert path.read_bytes() == before
    assert not (tmp_path / 'backups').exists()


def test_connection_never_crosses_thread_boundary(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    db = Database(tmp_path / 'library.db')
    assert db._owner != threading.get_ident()
    with pytest.raises(RuntimeError, match='belongs'):
        with db.connection():
            pass
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda key: db.set_track_state(str(key), 'got', str(key)), range(40)))
    assert len(db.all_track_statuses()) == 40
    assert len(db.all_track_local_files()) == 40
    db.close()
    db.close()


@pytest.mark.parametrize('device,inode', [
    (1, 2), ((1 << 63) - 1, (1 << 63) - 1),
    (1 << 63, 1 << 63), ((1 << 64) - 1, (1 << 128) - 1),
])
def test_filesystem_identity_round_trips_without_overflow(tmp_path, device, inode):
    path = tmp_path / 'library.db'
    db = Database(path)
    assert db.observe_root('root', device, inode)
    db.close()
    db = Database(path)
    assert db.media_roots() == [{'path': 'root', 'device': device, 'inode': inode}]
    assert db.observe_root('root', device, inode)
    assert not db.observe_root('root', device + 1, inode)
    assert not db.observe_root('root', device, inode + 1)


@pytest.mark.parametrize('device,inode', [(1, 2), ((1 << 64) - 1, (1 << 127) + 100)])
def test_media_identity_filters_rounded_collisions_before_limit(tmp_path, device, inode):
    import json

    db = Database(tmp_path / 'library.db')
    # Oversized adjacent IDs become the same REAL in SQLite's JSON index.
    for offset in range(1, 4):
        db.register_media(f'other-inode-{offset}', json.dumps([device, inode + offset, 1, 2, 3]))
        db.register_media(f'other-device-{offset}', json.dumps([device + offset, inode, 1, 2, 3]))
    signature = json.dumps([device, inode, 1, 2, 3])
    exact = [db.register_media(f'exact-{n}', signature)['id'] for n in range(3)]
    matches = db.media_at_identity(signature)
    assert len(matches) == 2
    assert {row['id'] for row in matches} <= set(exact)
    assert db.media_at_identity(json.dumps([device + 4, inode + 4, 1, 2, 3])) == []


def test_wal_read_does_not_wait_for_external_writer(tmp_path):
    import sqlite3
    from contextlib import closing
    db = Database(tmp_path / 'library.db')
    db.set_track_status('one', 'skip')
    with closing(sqlite3.connect(db.path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE track_states SET status='got'")
        assert db.all_track_statuses() == {'one': 'skip'}
        writer.rollback()

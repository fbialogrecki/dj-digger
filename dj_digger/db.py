"""Unified SQLite storage engine for track states, crates, and scanned local files.

Replaces flat JSON files with a thread-safe SQLite database (~/.local/share/dj-digger/digger.db).
Supports WAL mode for concurrent background worker writes without UI thread locks.
"""

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# 1: crates hold a whole CrateRecord rather than five of its fields. See
# Database._ensure_crates_schema.
SCHEMA_VERSION = 1

# One Database per file for the whole process. Before this, library._db() built a
# fresh one on every call - three times inside list_crates alone - and each one
# opened its own connection, re-ran every CREATE TABLE, and closed nothing. The
# lock covers _INSTANCES, which is read-then-written from worker threads (the
# library scan, downloads).
_INSTANCES: dict[Path, "Database"] = {}
_LOCK = threading.Lock()


def default_db_path() -> Path:
    data_dir = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "dj-digger"
    return data_dir / "digger.db"


def database(db_path: Path | None = None) -> "Database":
    """The shared Database for this file, built on first use."""

    path = Path(db_path) if db_path else default_db_path()
    with _LOCK:
        instance = _INSTANCES.get(path)
        if instance is None:
            instance = Database(path)
            _INSTANCES[path] = instance
        return instance

class Database:
    """Thread-safe SQLite database manager with WAL mode and auto-migration."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Get or create a thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        with self.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS track_states (
                    key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_files (
                    path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    normalized_stem TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_local_normalized ON local_files(normalized_stem);")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                self._upgrade_schema(conn)
                self._import_legacy_files(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _upgrade_schema(self, conn: sqlite3.Connection) -> None:
        """Bring the crates table up to SCHEMA_VERSION.

        Version 1 stores the whole record rather than five chosen columns. Until
        0.9 the JSON file was the real copy and this table was a fallback, so
        ``imported_at``, ``refreshed_at``, ``partial``, ``new_track_keys`` and
        the removed tracks were simply not stored - which is why every row
        written by an older version reads back with no import date. With the
        files gone those fields have nowhere else to live.
        """

        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='crates'"
        ).fetchone()
        carried: list[tuple[str, str, str, str]] = []
        if existing:
            # Rebuilt rather than altered: the old columns would otherwise stay
            # behind as a second copy of the tracks, which is the whole thing
            # this release is removing.
            for row in conn.execute("SELECT * FROM crates").fetchall():
                keys = row.keys()
                record = {
                    "version": 1,
                    "source": row["source"],
                    "title": row["title"] if "title" in keys else "",
                    "imported_at": row["updated"] if "updated" in keys else "",
                    "refreshed_at": None,
                    "partial": False,
                    "removed_track_keys": [],
                    "new_track_keys": [],
                    "tracks": json.loads(row["tracks_json"]) if "tracks_json" in keys else [],
                }
                carried.append(
                    (
                        record["source"],
                        record["title"],
                        record["imported_at"],
                        json.dumps(record, ensure_ascii=False),
                    )
                )
            conn.execute("DROP TABLE crates")

        conn.execute("""
            CREATE TABLE crates (
                source TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                updated TEXT NOT NULL,
                record_json TEXT NOT NULL
            )
        """)
        if carried:
            conn.executemany(
                "INSERT OR REPLACE INTO crates (source, title, updated, record_json) VALUES (?, ?, ?, ?)",
                carried,
            )
            LOGGER.info("Moved %s crates to the one-record schema", len(carried))

    def _import_legacy_files(self, conn: sqlite3.Connection) -> None:
        """Read state.json and crates/*.json once, at the moment of the upgrade.

        Runs inside the same transaction as the schema change and behind the same
        ``user_version``, so it happens exactly once per database and never again
        - not on the next start, and not on the next Database in this process.
        That matters: this used to be guarded by a set that lived only as long as
        the process, so a legacy file left on disk could overwrite an edit made
        after the upgrade, every time the app was restarted.

        The files themselves are left where they are. From 0.9 they are neither
        written nor read, but deleting somebody's only copy of a crate during an
        upgrade is not a migration.
        """

        base_dir = self.path.parent
        state_file = base_dir / "state.json"
        if state_file.exists():
            try:
                raw = json.loads(state_file.read_text(encoding="utf-8"))
                tracks = raw.get("tracks", {}) if isinstance(raw, dict) else {}
                for key, val in tracks.items():
                    if isinstance(val, dict) and "status" in val:
                        conn.execute(
                            "INSERT OR IGNORE INTO track_states (key, status, updated) VALUES (?, ?, ?)",
                            (str(key), str(val["status"]), str(val.get("updated", "")))
                        )
                LOGGER.info("Migrated legacy state.json to SQLite")
            except (OSError, ValueError) as exc:
                LOGGER.warning("Could not migrate legacy state.json: %s", exc)

        crates_dir = base_dir / "crates"
        if not crates_dir.is_dir():
            return
        migrated = 0
        for crate_file in sorted(crates_dir.glob("*.json")):
            try:
                raw = json.loads(crate_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                LOGGER.warning("Could not read crate file %s: %s", crate_file, exc)
                continue
            if not isinstance(raw, dict) or not raw.get("source"):
                continue
            # Overwrites whatever the old table carried over for this source. The
            # file was the real copy before 0.9 - list_crates read files first and
            # fell back to the table - so where both exist the file is the one
            # with the import date, the partial flag and the NEW marks in it.
            conn.execute(
                """INSERT OR REPLACE INTO crates (source, title, updated, record_json)
                   VALUES (?, ?, ?, ?)""",
                (
                    raw["source"],
                    raw.get("title") or "",
                    raw.get("refreshed_at") or raw.get("imported_at") or "",
                    json.dumps(raw, ensure_ascii=False),
                ),
            )
            migrated += 1
        if migrated:
            LOGGER.info("Migrated %s crate files to SQLite", migrated)

    # --- Track State API ---
    def get_track_status(self, key: str) -> str:
        with self.connection() as conn:
            cur = conn.execute("SELECT status FROM track_states WHERE key = ?", (str(key),))
            row = cur.fetchone()
            return row["status"] if row else "new"

    def set_track_status(self, key: str, status: str, updated: str) -> None:
        with self.connection() as conn:
            if status == "new":
                conn.execute("DELETE FROM track_states WHERE key = ?", (str(key),))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO track_states (key, status, updated) VALUES (?, ?, ?)",
                    (str(key), status, updated)
                )

    # --- Crates API ---
    def save_crate(self, record: dict[str, Any]) -> None:
        """Store a whole ``CrateRecord.to_json()``.

        source, title and updated are kept as columns as well, so a listing can
        be ordered without parsing every record.
        """

        updated = record.get("refreshed_at") or record.get("imported_at") or ""
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO crates (source, title, updated, record_json)
                   VALUES (?, ?, ?, ?)""",
                (
                    record["source"],
                    record.get("title") or "",
                    updated,
                    json.dumps(record, ensure_ascii=False),
                ),
            )

    def load_crate(self, source: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT record_json FROM crates WHERE source = ?", (source,)
            ).fetchone()
            return json.loads(row["record_json"]) if row else None

    def all_crates(self) -> list[dict[str, Any]]:
        """Every stored record, newest first. One query, not one per crate."""

        with self.connection() as conn:
            rows = conn.execute(
                "SELECT record_json FROM crates ORDER BY updated DESC"
            ).fetchall()
        records = []
        for row in rows:
            try:
                records.append(json.loads(row["record_json"]))
            except ValueError as exc:
                LOGGER.warning("Skipping an unreadable crate row: %s", exc)
        return records

    def delete_crate(self, source: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM crates WHERE source = ?", (source,))

    # --- Local File Cache API ---
    def get_cached_files(self) -> dict[str, tuple[float, str]]:
        """Return dict of path -> (mtime, normalized_stem)."""
        with self.connection() as conn:
            cur = conn.execute("SELECT path, mtime, normalized_stem FROM local_files")
            return {row["path"]: (row["mtime"], row["normalized_stem"]) for row in cur.fetchall()}

    def upsert_local_file(self, path: str, mtime: float, size: int, artist: str, title: str, normalized_stem: str) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO local_files (path, mtime, size, artist, title, normalized_stem)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (path, mtime, size, artist, title, normalized_stem)
            )

    def find_local_match(self, normalized_stem: str) -> str | None:
        with self.connection() as conn:
            cur = conn.execute("SELECT path FROM local_files WHERE normalized_stem = ? LIMIT 1", (normalized_stem,))
            row = cur.fetchone()
            return row["path"] if row else None

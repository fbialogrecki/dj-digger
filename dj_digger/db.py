"""Unified SQLite storage engine for track states, crates, and scanned local files.

Replaces flat JSON files with a thread-safe SQLite database (~/.local/share/dj-digger/digger.db).
Supports WAL mode for concurrent background worker writes without UI thread locks.
"""

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .paths import data_dir

LOGGER = logging.getLogger(__name__)

# One Database per file for the whole process. Before this, library._db() built a
# fresh one on every call - three times inside list_crates alone - and each one
# opened its own connection, re-ran every CREATE TABLE, and closed nothing. The
# lock covers _INSTANCES, which is read-then-written from worker threads (the
# library scan, downloads).
_INSTANCES: dict[Path, "Database"] = {}
_LOCK = threading.Lock()


def default_db_path() -> Path:
    return data_dir() / "digger.db"


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
    """Thread-safe SQLite database manager with WAL mode."""

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
            # SQLite busy-timeout: the background library scan and the UI
            # thread write concurrently, so a briefly locked database waits
            # instead of raising.
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS track_local_files (
                    key TEXT PRIMARY KEY,
                    path TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_local_normalized ON local_files(normalized_stem);")
            # A crates table written by <=0.8 has five chosen columns instead of
            # record_json, and CREATE TABLE IF NOT EXISTS would silently keep it,
            # breaking every crate read. The pre-0.9 one-time JSON import is gone,
            # so an old-shaped table is dropped, not migrated (see CHANGELOG).
            crate_columns = {row["name"] for row in conn.execute("PRAGMA table_info(crates)")}
            if crate_columns and "record_json" not in crate_columns:
                conn.execute("DROP TABLE crates")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS crates (
                    source TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    updated TEXT NOT NULL,
                    record_json TEXT NOT NULL
                )
            """)

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

    def all_track_statuses(self) -> dict[str, str]:
        """Every non-new status at once; the table only holds the marked rows."""
        with self.connection() as conn:
            rows = conn.execute("SELECT key, status FROM track_states").fetchall()
            return {row["key"]: row["status"] for row in rows}

    def all_track_local_files(self) -> dict[str, str]:
        with self.connection() as conn:
            rows = conn.execute("SELECT key, path FROM track_local_files").fetchall()
            return {row["key"]: row["path"] for row in rows}

    def get_track_local_file(self, key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT path FROM track_local_files WHERE key = ?", (str(key),)
            ).fetchone()
            return row["path"] if row else None

    def set_track_local_file(self, key: str, path: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO track_local_files (key, path) VALUES (?, ?)",
                (str(key), path),
            )

    def delete_track_local_file(self, key: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM track_local_files WHERE key = ?", (str(key),))

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

    def delete_local_files(self, paths: list[str]) -> None:
        if not paths:
            return
        with self.connection() as conn:
            conn.executemany(
                "DELETE FROM local_files WHERE path = ?",
                ((path,) for path in paths),
            )

    def find_local_match(self, normalized_stem: str) -> str | None:
        with self.connection() as conn:
            cur = conn.execute("SELECT path FROM local_files WHERE normalized_stem = ? LIMIT 1", (normalized_stem,))
            row = cur.fetchone()
            return row["path"] if row else None

    def find_unique_local_match(
        self, containing: str, also_containing: str = ""
    ) -> str | None:
        """Return a decorated filename match only when it is unambiguous."""
        condition = "instr(normalized_stem, ?) > 0"
        values = [containing]
        if also_containing:
            condition += " AND instr(normalized_stem, ?) > 0"
            values.append(also_containing)
        with self.connection() as conn:
            row = conn.execute(
                f"""SELECT MIN(path) AS path,
                           COUNT(DISTINCT normalized_stem) AS variants
                    FROM local_files WHERE {condition}""",
                values,
            ).fetchone()
            return row["path"] if row and row["variants"] == 1 else None

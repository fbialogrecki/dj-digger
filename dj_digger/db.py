"""SQLite repositories with a single connection owner and explicit transactions."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from weakref import WeakSet

from .paths import data_dir
from .schema import open_database

_DATABASES = WeakSet()

# One Database per file for the whole process. Before this, library._db() built a
# fresh one on every call, and each one opened its own connection, re-ran every
# CREATE TABLE, and closed nothing. The lock covers _INSTANCES, which is
# read-then-written from worker threads (the library scan, downloads).
_INSTANCES: dict[Path, "Database"] = {}
_LOCK = threading.Lock()


def default_db_path() -> Path:
    return data_dir() / "digger.db"


def database(db_path: Path | None = None) -> "Database":
    """The shared Database for this file, built on first use."""

    path = Path(db_path) if db_path else default_db_path()
    with _LOCK:
        instance = _INSTANCES.get(path)
        if instance is None or instance._closed:
            instance = Database(path)
            _INSTANCES[path] = instance
        return instance

def owned(method):
    """Run one repository call on the connection's owning thread."""
    @wraps(method)
    def invoke(self, *args, **kwargs):
        if threading.get_ident() == self._owner:
            return method(self, *args, **kwargs)
        return self._executor.submit(method, self, *args, **kwargs).result()
    return invoke


class Database:
    """One connection, created, used and closed on a dedicated thread.

    Public calls are synchronous for CLI/worker use. UI callers must await
    asyncio.to_thread; no cursor or connection crosses this boundary.
    """

    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="digger-db")
        self._owner = None
        self._closed = False
        try:
            self._executor.submit(self._open).result()
        except BaseException:
            self._executor.shutdown()
            raise
        _DATABASES.add(self)

    def _open(self) -> None:
        self._owner = threading.get_ident()
        self._conn = open_database(self.path)

    def close(self) -> None:
        if not self._closed:
            self._executor.submit(self._conn.close).result()
            self._closed = True
            self._executor.shutdown()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if threading.get_ident() != self._owner:
            raise RuntimeError("SQLite connection belongs to its database thread")
        conn = self._conn
        # Nested repository calls participate in the outer transaction.
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    @owned
    def set_track_state(self, key: str, status: str, path: str | None) -> None:
        """Commit status and provenance together, including manual removal."""
        with self.connection():
            self.set_track_status(key, status)
            if path is None:
                self.delete_track_local_file(key)
            else:
                self.set_track_local_file(key, path)

    # --- Track State API ---
    @owned
    def set_track_status(self, key: str, status: str) -> None:
        updated = datetime.now(UTC).isoformat(timespec="seconds")
        with self.connection() as conn:
            if status == "new":
                conn.execute("DELETE FROM track_states WHERE key = ?", (str(key),))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO track_states (key, status, updated) VALUES (?, ?, ?)",
                    (str(key), status, updated)
                )

    @owned
    def all_track_statuses(self) -> dict[str, str]:
        """Every non-new status at once; the table only holds the marked rows."""
        with self.connection() as conn:
            rows = conn.execute("SELECT key, status FROM track_states").fetchall()
            return {row["key"]: row["status"] for row in rows}

    @owned
    def all_track_local_files(self) -> dict[str, str]:
        with self.connection() as conn:
            rows = conn.execute("SELECT key, path FROM track_local_files").fetchall()
            return {row["key"]: row["path"] for row in rows}

    @owned
    def set_track_local_file(self, key: str, path: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO track_local_files (key, path) VALUES (?, ?)",
                (str(key), path),
            )

    @owned
    def delete_track_local_file(self, key: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM track_local_files WHERE key = ?", (str(key),))

    # --- Crates API ---
    @owned
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

    @owned
    def load_crate(self, source: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT record_json FROM crates WHERE source = ?", (source,)
            ).fetchone()
            return json.loads(row["record_json"]) if row else None

    @owned
    def list_crate_headers(self) -> list[dict[str, Any]]:
        """source, title, updated and the partial flag, without parsing a record.

        The sidebar only shows titles, but the listing used to deserialise
        every track of every crate to draw it - at startup and again after each
        dig. ``partial`` lives inside the JSON, so it comes out through SQLite's
        own parser (json_extract is built in since SQLite 3.38).
        """

        with self.connection() as conn:
            rows = conn.execute(
                """SELECT source, title, updated,
                          json_extract(record_json, '$.partial') AS partial
                   FROM crates ORDER BY updated DESC"""
            ).fetchall()
        return [
            {
                "source": row["source"],
                "title": row["title"],
                "updated": row["updated"],
                "partial": bool(row["partial"]),
            }
            for row in rows
        ]

    @owned
    def delete_crate(self, source: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM crates WHERE source = ?", (source,))

    # --- Local File Cache API ---
    @owned
    def get_cached_files(self) -> dict[str, tuple[float, str]]:
        """Return dict of path -> (mtime, normalized_stem)."""
        with self.connection() as conn:
            cur = conn.execute("SELECT path, mtime, normalized_stem FROM local_files")
            return {row["path"]: (row["mtime"], row["normalized_stem"]) for row in cur.fetchall()}

    @owned
    def upsert_local_files(self, rows: list[tuple[str, float, str]]) -> None:
        """Write a batch of ``(path, mtime, normalized_stem)``.

        One transaction for the lot: the scan used to commit per file, which on
        a Windows drive mounted into WSL meant an fsync per track.
        """
        if not rows:
            return
        with self.connection() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO local_files (path, mtime, normalized_stem)
                   VALUES (?, ?, ?)""",
                rows,
            )

    @owned
    def delete_local_files(self, paths: list[str]) -> None:
        if not paths:
            return
        with self.connection() as conn:
            conn.executemany(
                "DELETE FROM local_files WHERE path = ?",
                ((path,) for path in paths),
            )

    @owned
    def find_local_match(self, normalized_stem: str) -> str | None:
        with self.connection() as conn:
            cur = conn.execute("SELECT path FROM local_files WHERE normalized_stem = ? LIMIT 1", (normalized_stem,))
            row = cur.fetchone()
            return row["path"] if row else None

    @owned
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

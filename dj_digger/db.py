"""Unified SQLite storage engine for track states, crates, and scanned local files.

Replaces flat JSON files with a thread-safe SQLite database (~/.local/share/dj-digger/digger.db).
Supports WAL mode for concurrent background worker writes without UI thread locks.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)

def default_db_path() -> Path:
    data_dir = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "dj-digger"
    return data_dir / "digger.db"

class Database:
    """Thread-safe SQLite database manager with WAL mode and auto-migration."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
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
                CREATE TABLE IF NOT EXISTS crates (
                    source TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    declared_count INTEGER,
                    updated TEXT NOT NULL,
                    tracks_json TEXT NOT NULL
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
        self._migrate_legacy_json()

    def _migrate_legacy_json(self) -> None:
        base_dir = self.path.parent
        state_file = base_dir / "state.json"
        if state_file.exists():
            try:
                raw = json.loads(state_file.read_text(encoding="utf-8"))
                tracks = raw.get("tracks", {}) if isinstance(raw, dict) else {}
                with self.connection() as conn:
                    for key, val in tracks.items():
                        if isinstance(val, dict) and "status" in val:
                            conn.execute(
                                "INSERT OR IGNORE INTO track_states (key, status, updated) VALUES (?, ?, ?)",
                                (str(key), str(val["status"]), str(val.get("updated", "")))
                            )
                state_file.rename(state_file.with_suffix(".json.bak"))
                LOGGER.info("Migrated legacy state.json to SQLite")
            except Exception as exc:
                LOGGER.warning("Could not migrate legacy state.json: %s", exc)

        crates_dir = base_dir / "crates"
        if crates_dir.is_dir():
            for crate_file in crates_dir.glob("*.json"):
                try:
                    raw = json.loads(crate_file.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and "source" in raw:
                        with self.connection() as conn:
                            conn.execute(
                                """INSERT OR REPLACE INTO crates
                                   (source, title, declared_count, updated, tracks_json)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (
                                    raw["source"],
                                    raw.get("title", ""),
                                    raw.get("declared_count"),
                                    raw.get("saved_at", ""),
                                    json.dumps(raw.get("tracks", []), ensure_ascii=False)
                                )
                            )
                except Exception as exc:
                    LOGGER.warning("Could not migrate crate file %s: %s", crate_file, exc)

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

    def get_status_counts(self) -> Dict[str, int]:
        counts = {"new": 0, "opened": 0, "skip": 0, "got": 0}
        with self.connection() as conn:
            cur = conn.execute("SELECT status, COUNT(*) as cnt FROM track_states GROUP BY status")
            for row in cur.fetchall():
                if row["status"] in counts:
                    counts[row["status"]] = row["cnt"]
        return counts

    # --- Crates API ---
    def save_crate(self, source: str, title: str, declared_count: Optional[int], updated: str, tracks_data: List[Dict[str, Any]]) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO crates
                   (source, title, declared_count, updated, tracks_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (source, title, declared_count, updated, json.dumps(tracks_data, ensure_ascii=False))
            )

    def load_crate(self, source: str) -> Optional[Dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.execute("SELECT source, title, declared_count, updated, tracks_json FROM crates WHERE source = ?", (source,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "source": row["source"],
                "title": row["title"],
                "declared_count": row["declared_count"],
                "saved_at": row["updated"],
                "tracks": json.loads(row["tracks_json"])
            }

    def list_crates(self) -> List[Dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.execute("SELECT source, title, declared_count, updated, tracks_json FROM crates ORDER BY updated DESC")
            crates = []
            for row in cur.fetchall():
                tracks = json.loads(row["tracks_json"])
                crates.append({
                    "source": row["source"],
                    "title": row["title"],
                    "declared_count": row["declared_count"],
                    "saved_at": row["updated"],
                    "track_count": len(tracks)
                })
            return crates

    def delete_crate(self, source: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM crates WHERE source = ?", (source,))

    # --- Local File Cache API ---
    def get_cached_files(self) -> Dict[str, Tuple[float, str]]:
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

    def find_local_match(self, normalized_stem: str) -> Optional[str]:
        with self.connection() as conn:
            cur = conn.execute("SELECT path FROM local_files WHERE normalized_stem = ? LIMIT 1", (normalized_stem,))
            row = cur.fetchone()
            return row["path"] if row else None

"""Local Music & Downloads directory scanner.

Recursively scans ~/Music, ~/Downloads (or custom configured folders) for audio files,
normalizes filenames/tags to match SoundCloud tracks, marks matched tracks as 'got',
and copies local file paths to the system clipboard using OSC 52 or native utilities.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .db import Database
from .models import Track

LOGGER = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".m4a", ".aac", ".ogg", ".alac"}


def normalize_string(text: str) -> str:
    """Normalize string for fuzzy-safe track matching."""
    text = (text or "").lower()
    text = re.sub(r"[^\w]+", "", text, flags=re.UNICODE)
    return text


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard using OSC 52 ANSI escape sequence with OS fallback commands."""
    if not text:
        return False

    # 1. OSC 52 ANSI escape sequence (works in modern terminals & SSH sessions)
    try:
        import base64
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        osc52 = f"\x1b]52;c;{b64}\x07"
        sys.stdout.write(osc52)
        sys.stdout.flush()
    except Exception:
        pass

    # 2. Native OS clipboard commands
    tools = [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["pbcopy"],
    ]
    for tool in tools:
        try:
            res = subprocess.run(tool, input=text.encode("utf-8"), capture_output=True, timeout=2)
            if res.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return True


def default_scan_directories() -> List[Path]:
    home = Path.home()
    dirs = [home / "Music", home / "Downloads"]
    return [d for d in dirs if d.is_dir()]


class LocalScanner:
    """Background scanner for local audio files with mtime SQLite caching."""

    def __init__(self, directories: Optional[List[Path]] = None, db: Optional[Database] = None) -> None:
        self.directories = directories or default_scan_directories()
        self.db = db or Database()

    def scan(self) -> int:
        """Scan configured directories, updating the local_files cache in SQLite."""
        cached = self.db.get_cached_files()
        scanned = 0

        for root_dir in self.directories:
            if not root_dir.exists():
                continue
            for entry in root_dir.rglob("*"):
                if entry.is_file() and entry.suffix.lower() in AUDIO_EXTENSIONS:
                    try:
                        stat = entry.stat()
                        path_str = str(entry.resolve())
                        mtime = stat.st_mtime
                        size = stat.st_size

                        # Check mtime cache
                        if path_str in cached and cached[path_str][0] == mtime:
                            continue

                        stem = entry.stem
                        norm_stem = normalize_string(stem)
                        self.db.upsert_local_file(
                            path=path_str,
                            mtime=mtime,
                            size=size,
                            artist="",
                            title=stem,
                            normalized_stem=norm_stem
                        )
                        scanned += 1
                    except (OSError, PermissionError) as exc:
                        LOGGER.debug("Skipping file %s during scan: %s", entry, exc)
        return scanned

    def match_track(self, track: Track) -> Optional[str]:
        """Find a local audio file matching the track's artist and title."""
        if not track.title:
            return None

        # Try matching full label "Artist Title" or "Title"
        label_stem = normalize_string(f"{track.artist}{track.title}")
        match = self.db.find_local_match(label_stem)
        if match:
            return match

        title_stem = normalize_string(track.title)
        if len(title_stem) >= 6:
            match = self.db.find_local_match(title_stem)
            if match:
                return match

        return None

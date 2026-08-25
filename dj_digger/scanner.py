"""Finding the tracks you already own.

Walks the configured folders for audio files, normalises their names, and offers
the crate browser a way to ask "do I have this one already?". The answer comes
with a confidence, because a filename is weak evidence: two different tracks can
easily share a title, and being wrong here would overwrite a decision the user
made by hand.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from .db import Database, database
from .models import Track

LOGGER = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aiff", ".m4a", ".aac", ".ogg", ".alac"}


def normalize_string(text: str) -> str:
    """Normalize string for fuzzy-safe track matching."""
    text = (text or "").lower()
    text = re.sub(r"[^\w]+", "", text, flags=re.UNICODE)
    return text


class LocalMatch(NamedTuple):
    """A file on disk that looks like a track, and how much it looks like it."""

    path: str
    # True when artist and title both matched. A title on its own is enough to
    # point at a file and nowhere near enough to mark a track as owned.
    confident: bool


# Tried in order. OSC 52 is deliberately absent even though it is the one that
# works over SSH: it copies by writing an escape sequence to stdout, and while
# the crate browser is running stdout belongs to Textual - the sequence would
# land in the middle of a frame and corrupt the screen.
CLIPBOARD_COMMANDS = (
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
    ["pbcopy"],
    # On WSL the clipboard you paste from is Windows', not the Linux one.
    ["clip.exe"],
)


def copy_to_clipboard(text: str) -> bool:
    """Put text on the system clipboard. False when nothing here could.

    It used to return True unconditionally, which meant the caller could not
    tell a copy from a shrug.
    """

    if not text:
        return False
    for command in CLIPBOARD_COMMANDS:
        # clip.exe reads UTF-16LE; everything else wants UTF-8. Sending the
        # wrong one mangles any path that is not pure ASCII.
        encoding = "utf-16-le" if command[0] == "clip.exe" else "utf-8"
        try:
            finished = subprocess.run(
                command, input=text.encode(encoding), capture_output=True, timeout=2
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if finished.returncode == 0:
            return True
    LOGGER.debug("No clipboard tool answered; tried %s", [c[0] for c in CLIPBOARD_COMMANDS])
    return False


def default_scan_directories() -> list[Path]:
    home = Path.home()
    dirs = [home / "Music", home / "Downloads"]
    return [d for d in dirs if d.is_dir()]


class LocalScanner:
    """Background scanner for local audio files with mtime SQLite caching."""

    def __init__(self, directories: list[Path] | None = None, db: Database | None = None) -> None:
        self.directories = directories or default_scan_directories()
        # database(), not Database(): the scan runs on a worker thread and must
        # share the one process-wide instance the UI thread is already using.
        self.db = db or database()
        self._stale_stems: set[str] = set()

    def scan(self) -> int:
        """Scan configured directories, updating the local_files cache in SQLite."""
        cached = self.db.get_cached_files()
        self._stale_stems.clear()
        missing = [path for path in cached if not Path(path).is_file()]
        for path in missing:
            self._stale_stems.add(cached[path][1])
            cached.pop(path)
        self.db.delete_local_files(missing)
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

    def match_track(self, track: Track) -> LocalMatch | None:
        """The local file that looks like this track, if there is one."""

        if not track.title:
            return None

        both = self._existing_match(
            normalize_string(f"{track.artist}{track.title}")
        )
        if both:
            return LocalMatch(both, confident=True)

        # A title alone. Short ones match far too much - "intro" is a filename
        # in every second folder - so there is a floor under how little evidence
        # is enough to even point at a file.
        title_stem = normalize_string(track.title)
        if len(title_stem) >= 6:
            loose = self._existing_match(title_stem)
            if loose:
                return LocalMatch(loose, confident=False)

        return None

    def _existing_match(self, normalized_stem: str) -> str | None:
        while path := self.db.find_local_match(normalized_stem):
            if Path(path).is_file():
                return path
            self._stale_stems.add(normalized_stem)
            self.db.delete_local_files([path])
        return None

    def had_stale_match(self, track: Track) -> bool:
        exact = normalize_string(f"{track.artist}{track.title}")
        title = normalize_string(track.title)
        return exact in self._stale_stems or (
            len(title) >= 6 and title in self._stale_stems
        )

"""Local-file reconciliation and field-specific playlist edits, independent of UI."""

from pathlib import Path

from ..library import CrateRecord
from ..scanner import SCAN_BATCH, LocalScanner
from ..state import GOT, NEW


class LibraryService:
    def __init__(self, state):
        self.state = state

    def remember_beatport(self, source, generation, outcome):
        raw = self.state.db.remember_beatport(source, generation, outcome)
        return CrateRecord.from_json(raw) if raw is not None else None

    def remove_tracks(self, source, generation, keys, *, removed):
        raw = self.state.db.set_removed_tracks(source, generation, keys, removed)
        return CrateRecord.from_json(raw) if raw is not None else None

    def forget_missing(self, track):
        if not track.local_path or Path(track.local_path).is_file():
            return False
        was_file_backed = self.state.clear_local_file(track.key)
        if not was_file_backed and self.state.get(track.key) == GOT:
            self.state.set(track.key, NEW)
        track.local_path = None
        return True

    def mark_existing(self, track):
        remembered = self.state.local_file(track.key)
        if remembered:
            track.local_path = remembered
        if self.forget_missing(track) or not track.local_path:
            return False
        if self.state.get(track.key) != GOT or remembered != track.local_path:
            self.state.set_local_file(track.key, track.local_path)
        return True

    def needs_copy(self, path, directory):
        if not path:
            return False
        source = Path(path)
        return source.is_file() and not source.resolve().is_relative_to(directory.resolve())

    def scanner(self, directories):
        return LocalScanner([Path(d).expanduser() for d in directories], db=self.state.db)

    def match_tracks(self, tracks, scanner):
        """Resolve paths outside transactions; persist certain matches in short batches."""
        paths = {}
        pending = []
        for track in tracks:
            remembered = self.state.local_file(track.key) or track.local_path
            stale = bool(remembered) and not Path(remembered).is_file()
            if remembered and not stale:
                path, confident = remembered, True
            else:
                match = scanner.match_track(track)
                path, confident = (match.path, match.confident) if match else (None, False)
                stale = stale or scanner.had_stale_match(track)
            if path is not None or stale or track.local_path is not None:
                paths[track.key] = path
            pending.append((track.key, path, confident, stale))
            if len(pending) == SCAN_BATCH:
                self.state.apply_file_matches(pending)
                pending.clear()
        self.state.apply_file_matches(pending)
        return paths

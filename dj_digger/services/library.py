"""Local-file reconciliation and field-specific playlist edits, independent of UI."""

from pathlib import Path

from ..library import CrateRecord
from ..scanner import SCAN_BATCH, LocalScanner
from ..state import GOT, FileMatch


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
        observed = self.state.observe_file(track.key)
        path = observed.path or track.local_path
        if not path or Path(path).is_file():
            return False
        self.state.apply_file_matches([FileMatch(track.key, None, False, True, observed.revision)])
        track.local_path = self.state.local_file(track.key)
        return track.local_path is None

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
            observed = self.state.observe_file(track.key)
            remembered = observed.path or track.local_path
            stale = bool(remembered) and not Path(remembered).is_file()
            if remembered and not stale:
                path, confident = remembered, True
            else:
                match = scanner.match_track(track)
                path, confident = (match.path, match.confident) if match else (None, False)
                stale = stale or scanner.had_stale_match(track)
            if path is not None or stale or track.local_path is not None:
                paths[track.key] = path
            pending.append(FileMatch(track.key, path, confident, stale, observed.revision))
            if len(pending) == SCAN_BATCH:
                self.state.apply_file_matches(pending)
                pending.clear()
        self.state.apply_file_matches(pending)
        # A newer completed transfer can supersede a missing-file observation.
        return {key: self.state.local_file(key) or path for key, path in paths.items()}

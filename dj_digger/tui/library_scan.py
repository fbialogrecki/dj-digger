"""Matching the crate against audio files you already have on disk.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from textual import work

from .. import library as library_module
from ..scanner import LocalScanner, copy_to_clipboard
from ..state import GOT

LOGGER = logging.getLogger(__name__)


class LibraryScanMixin:
    """Matching the crate against audio files you already have on disk."""

    def _forget_missing_local_file(self, track) -> bool:
        if not track.local_path or Path(track.local_path).is_file():
            return False
        was_file_backed = self.state.clear_local_file(track.key)
        if not was_file_backed and self.state.get(track.key) == GOT:
            # Compatibility with auto-GOT rows created before file provenance
            # was stored separately.
            self.state.set(track.key, "new")
        track.local_path = None
        return True

    def _mark_existing_local_file(self, track) -> bool:
        remembered = self.state.local_file(track.key)
        if remembered:
            track.local_path = remembered
        if self._forget_missing_local_file(track) or not track.local_path:
            return False
        if self.state.get(track.key) != GOT or remembered != track.local_path:
            self.state.set_local_file(track.key, track.local_path)
        return True

    def _local_file_needs_copy(self, track) -> bool:
        if not track.local_path:
            return False
        source = Path(track.local_path)
        if not source.is_file():
            return False
        return not source.resolve().is_relative_to(self._download_directory().resolve())

    @work(thread=True, group="scan")
    def scan_local_files(self) -> None:
        """Walk the configured folders in the background, then mark what turns up.

        The group is spelled out because the default one is shared, and
        ``dig_in_background`` sits in it with ``exclusive=True`` - so digging a
        link would cancel a scan that happened to still be running.
        """

        scanner = LocalScanner(
            directories=[Path(d).expanduser() for d in self.config.scan_directories],
            # The status store already holds a connection to this database; a
            # second Database means a second pool and a second legacy import.
            db=self.state.db,
        )
        try:
            scanned = scanner.scan()
        except OSError as exc:
            LOGGER.debug("Local scan stopped early: %s", exc)
            return
        LOGGER.info("Scanned %s new local files", scanned)
        try:
            self.call_from_thread(self.apply_local_file_matches, scanner)
        except RuntimeError:
            # A thread worker cannot be interrupted, so a scan that outlives the
            # app arrives here with nothing left to talk to.
            LOGGER.debug("Scan finished after the app had gone")

    def apply_local_file_matches(self, scanner: LocalScanner) -> None:
        """Badge every track we have a file for, but only promote the certain ones.

        A loose match is a title that happens to agree, which is enough to point
        at a file and nowhere near enough to overwrite a decision. So the badge
        goes on either way, and the status only moves when artist and title both
        matched. A confident file match is the strongest available evidence
        that the track is already owned, so it becomes ``got`` regardless of a
        stale new, opened or skipped status.
        """

        touched = False
        # No batching any more: this used to run inside state.batched(), which
        # existed only to stop each mark rewriting the whole of state.json. With
        # the mirror gone a mark is one SQLite write.
        for row in self.rows:
            track = row.track
            remembered = self.state.local_file(track.key)
            if remembered:
                track.local_path = remembered
            if self._forget_missing_local_file(track):
                touched = True
            if self._mark_existing_local_file(track):
                touched = True
                continue
            match = scanner.match_track(track)
            if match is None:
                stale = getattr(scanner, "had_stale_match", lambda _track: False)
                if stale(track) and self.state.get(track.key) == GOT:
                    self.state.set(track.key, "new")
                    touched = True
                continue
            track.local_path = match.path
            touched = True
            if match.confident:
                self.state.set_local_file(track.key, match.path)
        if touched:
            self.refresh_rows()

    def action_copy_path(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if self._forget_missing_local_file(row.track) or not row.track.local_path:
            self.refresh_rows()
            self.notify("No local file matched for this track", timeout=3)
            return
        if copy_to_clipboard(row.track.local_path):
            self.notify(f"Copied {row.track.local_path}", timeout=4)
        else:
            self.notify(
                "Could not reach a clipboard tool", severity="warning", timeout=5
            )

    def action_copy_local_file(self) -> None:
        row = self.current_row()
        if row is None or not self._local_file_needs_copy(row.track):
            self.notify("The local file is unavailable or already in this playlist folder", timeout=4)
            return
        self.notify(f"Copying {row.track.label} to the playlist folder…", timeout=3)
        self.copy_local_file_in_background(row.track)

    @work(thread=True, exclusive=True, group="copy_local_file")
    def copy_local_file_in_background(self, track) -> None:
        source = Path(track.local_path or "")
        try:
            target = _copy_local_file(source, self._download_directory())
        except (OSError, ValueError) as exc:
            self.call_from_thread(
                self.notify,
                f"Could not copy local file: {exc}",
                severity="error",
                timeout=6,
            )
            return
        self.call_from_thread(self._local_file_copied, track, target)

    def _local_file_copied(self, track, target: Path) -> None:
        track.local_path = str(target)
        self.state.set_local_file(track.key, target)
        if self.crate is not None:
            try:
                library_module.save(self.crate)
            except Exception as exc:
                LOGGER.warning("Could not persist copied local path: %s", exc)
        self.refresh_rows()
        self.notify(f"Copied to {target}", timeout=5)


def _copy_local_file(source: Path, directory: Path) -> Path:
    source = source.resolve(strict=True)
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    if source.is_relative_to(directory):
        return source

    target = directory / source.name
    counter = 1
    while target.exists():
        target = directory / f"{source.stem} ({counter}){source.suffix}"
        counter += 1

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".dj-digger-copy-", suffix=".part", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
        return target
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass

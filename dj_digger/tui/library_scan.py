"""Matching the crate against audio files you already have on disk.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
from pathlib import Path

from textual import work

from ..config import AppConfig
from ..scanner import LocalScanner, copy_to_clipboard
from ..state import GOT, NEW

LOGGER = logging.getLogger(__name__)


class LibraryScanMixin:
    """Matching the crate against audio files you already have on disk."""

    @work(thread=True, group="scan")
    def scan_local_files(self) -> None:
        """Walk the configured folders in the background, then mark what turns up.

        The group is spelled out because the default one is shared, and
        ``dig_in_background`` sits in it with ``exclusive=True`` - so digging a
        link would cancel a scan that happened to still be running.
        """

        scanner = LocalScanner(
            directories=[Path(d).expanduser() for d in AppConfig().scan_directories],
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
        matched - and only from ``new``, because a deliberate skip is not
        something a filename in your Downloads folder gets to undo.
        """

        touched = False
        with self.state.batched():
            for row in self.rows:
                track = row.track
                if track.local_path:
                    continue
                match = scanner.match_track(track)
                if match is None:
                    continue
                track.local_path = match.path
                touched = True
                if match.confident and self.state.get(track.key) == NEW:
                    self.state.set(track.key, GOT)
        if touched:
            self.refresh_rows()

    def action_copy_path(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if not row.track.local_path:
            self.notify("No local file matched for this track", timeout=3)
            return
        if copy_to_clipboard(row.track.local_path):
            self.notify(f"Copied {row.track.local_path}", timeout=4)
        else:
            self.notify(
                "Could not reach a clipboard tool", severity="warning", timeout=5
            )

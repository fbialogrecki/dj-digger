"""Matching the crate against audio files you already have on disk.

Composed by ``DiggerApp`` with explicit state and presentation callbacks.
"""

import logging
from copy import deepcopy
from functools import partial
from pathlib import Path

from ..clipboard import copy_to_clipboard

LOGGER = logging.getLogger(__name__)


class LibraryScanController:
    """Matching the crate against audio files you already have on disk."""

    def __init__(self, *, _download_directory, _main_available, _paint_key, call_from_thread, get_config, current_row, download_service, finish_job, notify, operations, playlist_state, refresh_rows, run_worker, scan_state, show_error, start_job, state, update_status, worker_scope, library_service, io):
        self.io = io
        self._download_directory = _download_directory
        self._main_available = _main_available
        self._paint_key = _paint_key
        self.call_from_thread = call_from_thread
        self.get_config = get_config
        self.current_row = current_row
        self.download_service = download_service
        self.finish_job = finish_job
        self.notify = notify
        self.operations = operations
        self.playlist_state = playlist_state
        self.refresh_rows = refresh_rows
        self.run_worker = run_worker
        self.scan_state = scan_state
        self.show_error = show_error
        self.start_job = start_job
        self.state = state
        self.library_service = library_service
        self.update_status = update_status
        self.worker_scope = worker_scope

    @property
    def config(self):
        return self.get_config()

    async def _forget_missing_local_file(self, track) -> bool:
        snapshot = deepcopy(track)
        missing = await self.io(self.library_service.forget_missing, snapshot)
        track.local_path = snapshot.local_path
        return missing

    async def _mark_existing_local_file(self, track) -> bool:
        snapshot = deepcopy(track)
        found = await self.io(self.library_service.mark_existing, snapshot)
        track.local_path = snapshot.local_path
        return found

    async def _local_file_needs_copy(self, track) -> bool:
        return await self.io(self.library_service.needs_copy, track.local_path, self._download_directory())

    def scan_local_files_work(self, tracks, directories, view, handle) -> None:
        with self.worker_scope():
            """Walk the configured folders in the background, then mark what turns up.

            The group is spelled out because the default one is shared, and
            ``dig_in_background`` sits in it with ``exclusive=True`` - so digging a
            link would cancel a scan that happened to still be running.
            """

            scanner = self.library_service.scanner(directories)
            try:
                try:
                    scanned = scanner.scan(cancel=self.scan_state._scan_cancel)
                except OSError as exc:
                    LOGGER.warning("Local scan stopped early: %s", exc)
                    self.call_from_thread(self.show_error, f"Scan stopped: {exc}")
                    self.call_from_thread(self.finish_job, handle)
                    return
                LOGGER.info("Scanned %s new local files", scanned)
                if scanner.errors:
                    shown = "; ".join(scanner.errors[:3])
                    more = len(scanner.errors) - 3
                    self.call_from_thread(
                        self.show_error,
                        f"Scan skipped {len(scanner.errors)} unreadable folder"
                        f"{'s' if len(scanner.errors) != 1 else ''}: {shown}"
                        + (f" (+{more} more)" if more > 0 else ""),
                    )
                paths = self.library_service.match_tracks(tracks, scanner)
                self.call_from_thread(self._apply_paths, paths, view)
                self.call_from_thread(self.finish_job, handle)
            except RuntimeError:
                # A thread worker cannot be interrupted, so a scan that outlives the
                # app arrives here with nothing left to talk to.
                LOGGER.debug("Scan finished after the app had gone")
            finally:
                if "handle" in locals():
                    self.operations.finish(handle)

    async def apply_local_file_matches(self, scanner) -> None:
        view = self.playlist_state._view_generation
        tracks = deepcopy([row.track for row in self.playlist_state.rows])
        paths = await self.io(self.library_service.match_tracks, tracks, scanner)
        self._apply_paths(paths, view)

    def _apply_paths(self, paths, view):
        if view != self.playlist_state._view_generation:
            return
        touched = False
        for row in self.playlist_state.rows:
            if row.track.key in paths:
                row.track.local_path = paths[row.track.key]
                touched = True
        if touched:
            self.refresh_rows()

    async def action_copy_path(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if await self._forget_missing_local_file(row.track) or not row.track.local_path:
            self._paint_key(row.track.key)
            self.notify("No local file matched for this track", timeout=3)
            return
        if await self.io(copy_to_clipboard, row.track.local_path):
            self.notify(f"Copied {row.track.local_path}", timeout=4)
        else:
            self.notify(
                "Could not reach a clipboard tool", severity="warning", timeout=5
            )

    async def action_copy_local_file(self) -> None:
        row = self.current_row()
        if row is None or not await self._local_file_needs_copy(row.track):
            self.notify("The local file is unavailable or already in this playlist folder", timeout=4)
            return
        self.notify(f"Copying {row.track.label} to the playlist folder…", timeout=3)
        self.copy_local_file_in_background(row.track)

    def copy_local_file_in_background(self, track):
        if not self._main_available():
            return None
        handle = self.start_job("Copying")
        return self._copy_local_worker(deepcopy(track), self._download_directory(), handle)

    def _copy_local_worker_work(self, track, directory, handle):
        with self.worker_scope():
            try:
                target = self.download_service.copy(
                    track.key, Path(track.local_path or ""), directory, handle.cancel,
                )
                self.call_from_thread(self._local_file_copied, track.key, target)
            except Exception as exc:
                self.call_from_thread(self.notify, f"Could not copy local file: {exc}", severity="error", timeout=6)
            finally:
                self.operations.finish(handle)
                try:
                    self.call_from_thread(self.update_status)
                except RuntimeError:
                    pass

    def _local_file_copied(self, key, target: Path) -> None:
        for row in self.playlist_state.rows:
            if row.track.key == key:
                row.track.local_path = str(target)
        self._paint_key(key)
        self.update_status()
        self.notify(f"Copied to {target}", timeout=5)

    def scan_local_files(self):
        if self.operations.active("scan") is not None:
            return None
        self.scan_state._scan_cancel.clear()
        handle = self.start_job("Scanning", cancel=self.scan_state._scan_cancel, animate=False)
        tracks = deepcopy([row.track for row in self.playlist_state.rows])
        return self.run_worker(
            partial(self.scan_local_files_work, tracks, tuple(self.config.scan_directories),
                    self.playlist_state._view_generation, handle),
            thread=True, group="scan", description="scan_local_files",
        )

    def _copy_local_worker(self, *args, **kwargs):
        return self.run_worker(
            partial(self._copy_local_worker_work, *args, **kwargs), thread=True, group='copy_local_file',
            description="_copy_local_worker",
        )

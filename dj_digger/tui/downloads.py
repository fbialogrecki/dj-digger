"""Fetching artist-provided files, one at a time or the whole visible list.

Composed by ``DiggerApp`` with explicit state and presentation callbacks.
"""

import logging
import re
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from threading import Event

from .. import links as links_module
from ..models import GOT, SKIP, Cancelled, Track, check_cancelled
from ..services.downloads import (
    BROWSER_BATCH_MAX,
    FAILURE_GROUPS,
    DownloadEvent,
    DownloadRequest,
    DownloadWorkflow,
    downloadable,
)
from .rows import Row
from .screens import GateProfileScreen, SoundCloudAuthScreen

LOGGER = logging.getLogger(__name__)

_INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _playlist_folder_name(title: str) -> str:
    cleaned = _INVALID_FOLDER_CHARS.sub(" ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    # 120 keeps stem + suffix inside common 255-byte filename limits with room
    # for the " (n)" uniqueness counter.
    return cleaned[:120].rstrip(" .") or "playlist"


@dataclass(frozen=True)
class DownloadContext:
    source: str
    generation: str
    directory: Path
    view_generation: int
    timeout: float


class DownloadController:
    """Fetching artist-provided files, one at a time or the whole visible list."""

    def __init__(
        self,
        *,
        _find_gate_url,
        _main_available,
        _mark_existing_local_file,
        _paint_key,
        _set_records,
        call_from_thread,
        call_later,
        get_client,
        get_config,
        current_row,
        get_dig_options,
        download_service,
        download_state,
        job_progress,
        notify,
        operations,
        playlist_state,
        push_screen,
        refresh_rows,
        adopt_login,
        accounts,
        run_worker,
        show_error,
        start_job,
        state,
        status_of,
        targets,
        update_status,
        worker_scope,
        io,
    ):
        self.io = io
        self._prerequisites_settled = Event()
        self._prerequisites_settled.set()
        self._find_gate_url = _find_gate_url
        self._main_available = _main_available
        self._mark_existing_local_file = _mark_existing_local_file
        self._paint_key = _paint_key
        self._set_records = _set_records
        self.call_from_thread = call_from_thread
        self.call_later = call_later
        self.get_client = get_client
        self.get_config = get_config
        self.current_row = current_row
        self.get_dig_options = get_dig_options
        self.download_service = download_service
        self.download_state = download_state
        self.job_progress = job_progress
        self.notify = notify
        self.operations = operations
        self.playlist_state = playlist_state
        self.push_screen = push_screen
        self.refresh_rows = refresh_rows
        self.adopt_login = adopt_login
        self.accounts = accounts
        self.run_worker = run_worker
        self.show_error = show_error
        self.start_job = start_job
        self.state = state
        self.status_of = status_of
        self.targets = targets
        self.update_status = update_status
        self.worker_scope = worker_scope

    @property
    def client(self):
        return self.get_client()

    @property
    def config(self):
        return self.get_config()

    @property
    def dig_options(self):
        return self.get_dig_options()

    def _capture_download_context(self):
        source = self.playlist_state.crate.source if self.playlist_state.crate is not None else ""
        self.download_state._download_context = DownloadContext(
            source, self.state.db.crate_generation(source), self._download_directory(),
            self.playlist_state._view_generation, self.dig_options.timeout,
        )
        return self.download_state._download_context

    def _download_directory(self) -> Path:
        base = Path(self.config.download_directory).expanduser()
        title = self.playlist_state.crate.title if self.playlist_state.crate is not None else self.playlist_state.crate_title
        if not title.strip():
            return base
        folder = _playlist_folder_name(title)
        return base if base.name.casefold() == folder.casefold() else base / folder

    async def action_download_track(self) -> None:
        if not self._main_available():
            return
        row = self.current_row()
        if row is None:
            return
        row = deepcopy(row)
        self.download_state._gate_cancel = Event()
        context = self._capture_download_context()
        handle = self.start_job("Downloading", 1, cancel=self.download_state._gate_cancel)
        started = False
        try:
            if await self._mark_existing_local_file(row.track):
                self._local_match_ready(row.track, context.view_generation)
                self.notify(f"Already on disk: {row.track.local_path}", timeout=4)
                return
            if handle.cancel.is_set():
                return
            gate_url = self._find_gate_url(row)
            if not downloadable(row.track, gate_url):
                self.notify("This track has no active SoundCloud free download or supported gate link", timeout=4)
                return
            self.notify(f"Downloading {row.track.label}...", timeout=3)
            self.download_track_in_background(row.track, gate_url, handle=handle)
            started = True
        finally:
            if not started:
                self.operations.finish(handle)
                self.update_status()

    def _local_match_ready(self, track, view, *, refresh=True):
        if view == self.playlist_state._view_generation:
            for row in self.playlist_state.rows:
                if row.track.key == track.key:
                    row.track.local_path = track.local_path
            if refresh:
                self.refresh_rows()

    def download_track_in_background_work(
        self,
        track: Track,
        gate_url: str | None = None,
        allow_prerequisite_retry: bool = True,
        handle=None,
    ) -> None:
        with self.worker_scope():
            with self._download_worker(handle):
                try:
                    self._workflow(batch=False).run_one(track, gate_url, allow_prerequisite_retry)
                except Cancelled:
                    pass

    @contextmanager
    def _download_worker(self, handle=None):
        """Hold admission until this worker and any authentication have settled."""

        if handle is None:
            self._capture_download_context()
        handle = handle or self.operations.start("Downloading", cancel=self.download_state._gate_cancel)
        self.download_state._download_handle = handle
        try:
            yield
        finally:
            self._prerequisites_settled.wait()
            self.accounts.wait_authentication()
            self.operations.finish(handle)
            try:
                self.call_from_thread(self.update_status)
            except RuntimeError:
                pass

    def _workflow(self, *, batch):
        context = self.download_state._download_context
        return DownloadWorkflow(
            self.download_service,
            DownloadRequest(context.source, context.generation, context.directory, context.timeout),
            self.download_state._download_handle,
            client=self.get_client, config=self.config,
            emit=lambda event: self._receive_from_thread(event, batch=batch),
            prerequisites=self._wait_download_prerequisites,
        )

    def _receive_from_thread(self, event: DownloadEvent, *, batch):
        if event.kind == 'progress' and event.progress:
            with self.download_state._progress_lock:
                self.download_state._pending_progress[event.key] = (event.operation_id, event.progress)
            return
        try:
            self.call_from_thread(self._receive, event, batch=batch)
        except RuntimeError:
            # The service already settled its effect; a closed view needs no update.
            pass

    def _receive(self, event: DownloadEvent, *, batch):
        handle = self.download_state._download_handle
        if handle is None or event.operation_id != handle.id:
            return
        context = self.download_state._download_context
        current_view = context.view_generation == self.playlist_state._view_generation
        if event.kind == 'progress':
            self._update_track_progress(event.key, event.progress, event.operation_id)
        elif event.kind == 'downloaded':
            self._download_finished(event.key, event.path, toast=not batch)
        elif event.kind == 'unrecorded':
            for row in self.playlist_state.rows:
                if row.track.key == event.key:
                    row.track.local_path = str(event.path)
            self._download_failed(event.key, event.message, banner_label=event.label if batch else None)
        elif event.kind == 'failed':
            self._download_failed(event.key, event.message, banner_label=event.label if batch else None)
        elif event.kind in ('waiting', 'cancelled'):
            self._settle_download_row(event.key)
        elif event.kind == 'metadata' and current_view:
            self._hub_metadata_ready(event.key, event.fields, context.view_generation)
        elif event.kind == 'hubs' and current_view:
            self._hub_preflight_finished()
        elif event.kind == 'status':
            self.notify(event.message, timeout=6, markup=False)
        elif event.kind == 'browser_started':
            self._browser_batch_started(event.count)
        elif event.kind == 'browser_finished':
            self._browser_batch_finished()
        elif event.kind == 'deferred':
            self.notify(
                f"{event.count} more gate{'s' if event.count != 1 else ''} left new: "
                f"the browser takes {BROWSER_BATCH_MAX} at a time, run the batch again for the rest",
                timeout=8, markup=False,
            )
        elif event.kind == 'summary':
            summary = event.summary
            self._on_batch_download_complete(summary.completed, summary.failed, summary.total,
                                             summary.pending, summary.failure_groups, summary.cancelled)

    def _adopt_login(self, oauth_token: str) -> bool:
        return self.adopt_login(oauth_token)

    def _wait_download_prerequisites(self, profiles, auth_items):
        completed = Event()
        cancel = self.download_state._gate_cancel
        ready = []
        cancel_dialogs = self.call_from_thread(
            self._resolve_download_prerequisites, profiles, auth_items,
            ready.extend, completed.set,
        )
        while not completed.wait(0.05):
            if cancel.is_set():
                try:
                    self.call_from_thread(cancel_dialogs)
                except RuntimeError:
                    pass
                raise Cancelled()
        check_cancelled(cancel)
        return ready

    def _resolve_download_prerequisites(
        self, profile_items: list, auth_items: list, retry, on_done=None
    ):
        """Run each required wizard once, then retry only approved items."""

        ready, screens = [], []
        cancel = self.download_state._gate_cancel
        settled = self._prerequisites_settled

        def finish() -> None:
            if ready and not cancel.is_set():
                if on_done is None:
                    self.call_later(retry, ready)
                else:
                    retry(ready)
            if on_done is not None:
                on_done()

        def ask_for_soundcloud() -> None:
            if not auth_items or cancel.is_set():
                finish()
                return

            def after_auth(oauth_token: str | None) -> None:
                if not oauth_token:
                    self.notify("SoundCloud login cancelled; download was not retried.", timeout=4)
                elif not cancel.is_set() and self._adopt_login(oauth_token):
                    ready.extend(auth_items)
                finish()

            screen = SoundCloudAuthScreen(self.accounts)
            screens.append(screen)
            self.push_screen(screen, after_auth)

        async def save_profile(saved):
            if saved and not cancel.is_set():
                settled.clear()
                try:
                    await self.io(self.accounts.save_profile, saved)
                    if not cancel.is_set():
                        ready.extend(profile_items)
                    ask_for_soundcloud()
                except Exception:
                    self.notify("Could not save the gate profile", severity="error")
                    finish()
                finally:
                    settled.set()
            else:
                self.notify("Gate profile cancelled; download was not retried.", timeout=4)
                ask_for_soundcloud()

        if profile_items:
            screen = GateProfileScreen(self.config)
            screens.append(screen)
            self.push_screen(screen, lambda saved: self.run_worker(save_profile(saved)))
        else:
            ask_for_soundcloud()

        def cancel_dialogs():
            for screen in screens:
                if screen.is_current:
                    screen.dismiss(None)

        return cancel_dialogs

    def _update_track_progress(self, key: str, pct: float, operation_id: str | None = None) -> None:
        handle = self.download_state._download_handle
        if operation_id is not None and (handle is None or handle.id != operation_id or not self.operations.current(handle)):
            return
        if self.download_state._download_context is not None and self.download_state._download_context.view_generation != self.playlist_state._view_generation:
            return
        self.download_state.download_progress[key] = pct
        self.download_state._dirty_download_rows.add(key)
        now = time.time()
        # ~12 repaints/s, below the 1/30 s UI ticker (keymap.TICK), so a fast
        # download can never outrun the frame budget with row repaints.
        if now - self.download_state._last_progress_redraw >= 0.08:
            self.download_state._last_progress_redraw = now
            dirty_keys = tuple(self.download_state._dirty_download_rows)
            self.download_state._dirty_download_rows.clear()
            for dirty_key in dirty_keys:
                self._paint_key(dirty_key)

    def _settle_download_row(self, key: str) -> None:
        """Drop the progress bookkeeping for a row and repaint it."""

        with self.download_state._progress_lock:
            self.download_state._pending_progress.pop(key, None)
        self.download_state.download_progress.pop(key, None)
        self.download_state._dirty_download_rows.discard(key)
        self._paint_key(key)
        self.update_status()

    def _download_failed(
        self, key: str, message: str, *, banner_label: str | None = None
    ) -> None:
        if banner_label is not None:
            # Batch failures go to the error banner so one bad gate does not
            # bury the toast stream.
            self.show_error(f"Batch download failed [{banner_label}]: {message}")
            self._settle_download_row(key)
            self.job_progress(failed=1, handle=self.download_state._download_handle)
            return
        self._settle_download_row(key)
        self.notify(f"Download failed: {message}", severity="error", timeout=6)

    def _download_finished(
        self, key: str, path: Path | str, *, toast: bool = True
    ) -> None:
        with self.download_state._progress_lock:
            self.download_state._pending_progress.pop(key, None)
        self.download_state.download_progress.pop(key, None)
        self.download_state._dirty_download_rows.discard(key)
        was_visible = any(row.track.key == key for row in self.playlist_state.visible_rows)
        for row in self.playlist_state.rows:
            if row.track.key == key:
                row.track.local_path = str(path)
                break
        if self.playlist_state.hide_handled and was_visible:
            self.refresh_rows()
        else:
            self._paint_key(key)
        self.job_progress(1, handle=self.download_state._download_handle)
        self.update_status()
        if toast:
            self.notify(f"Downloaded to {path}", timeout=5)

    async def action_batch_download(self) -> None:
        if not self._main_available():
            return
        rows = deepcopy(self.targets())
        self.download_state._gate_cancel = Event()
        context = self._capture_download_context()
        handle = self.start_job("Downloading", len(rows), cancel=self.download_state._gate_cancel)
        started = False
        local_matches = False
        try:
            eligible = []
            for row in rows:
                if handle.cancel.is_set():
                    return
                if await self._mark_existing_local_file(row.track):
                    self._local_match_ready(row.track, context.view_generation, refresh=False)
                    local_matches = True
                    continue
                if self.status_of(row) in (GOT, SKIP):
                    continue
                gate_url = self._find_gate_url(row)
                if downloadable(row.track, gate_url):
                    eligible.append((row, gate_url))
            if not eligible:
                self.notify("No downloadable free or gate tracks in current view", timeout=3)
                return
            if handle.cancel.is_set():
                return
            handle.total = len(eligible)
            if context.view_generation == self.playlist_state._view_generation:
                for row, _gate_url in eligible:
                    self.download_state.download_progress[row.track.key] = 0.0
                    self._paint_key(row.track.key)
            self.notify(f"Checking and downloading {len(eligible)} tracks in parallel...", timeout=4)
            self.batch_download_in_background(eligible, handle=handle)
            started = True
        finally:
            if local_matches and context.view_generation == self.playlist_state._view_generation:
                self.refresh_rows()
            if not started:
                self.operations.finish(handle)
                self.update_status()

    def batch_download_in_background_work(
        self,
        items: list[tuple[Row, str | None]],
        allow_prerequisite_retry: bool = True,
        handle=None,
    ) -> None:
        with self.worker_scope():
            with self._download_worker(handle):
                try:
                    self._workflow(batch=True).run_batch([(row.track, url) for row, url in items], allow_prerequisite_retry)
                except Cancelled:
                    pass

    def _hub_metadata_ready(self, key, fields, view_generation):
        if view_generation != self.playlist_state._view_generation:
            return
        for row in self.playlist_state.rows:
            if row.track.key == key:
                for name, value in fields.items():
                    setattr(row.track, name, value)

    def _hub_preflight_finished(self) -> None:
        tracks = [row.track for row in self.playlist_state.rows]
        self._set_records(links_module.categorise_all(tracks))
        self.refresh_rows()

    def _browser_batch_started(self, count: int) -> None:
        self.download_state._browser_batch_active = True
        self.notify(
            f"Finishing {count} Hypeddit gate{'s' if count != 1 else ''} in the hidden "
            "browser; a window opens only for a step that needs you. ctrl+x stops.",
            timeout=8,
            markup=False,
        )

    def _browser_batch_finished(self) -> None:
        self.download_state._browser_batch_active = False

    def _on_batch_download_complete(
        self,
        completed: int,
        failed: int,
        total: int,
        pending: int = 0,
        failure_groups: dict[str, int] | None = None,
        cancelled: int = 0,
    ) -> None:
        stale_keys = tuple(self.download_state.download_progress)
        self.download_state.download_progress.clear()
        self.download_state._dirty_download_rows.clear()
        for key in stale_keys:
            self._paint_key(key)
        msg = (
            "Batch check finished: no downloadable tracks remained"
            if total == 0
            else f"Batch download finished: {completed}/{total} downloaded"
        )
        if failed > 0:
            msg += f" ({failed} failed)"
        grouped = [
            f"{name}={count}"
            for name in [name for name, _types in FAILURE_GROUPS] + ["other"]
            if (count := (failure_groups or {}).get(name, 0))
        ]
        if grouped:
            msg += f" [{', '.join(grouped)}]"
        if cancelled:
            msg += f" ({cancelled} cancelled)"
        if pending:
            msg += f" ({pending} waiting for configuration)"
        self.notify(msg, timeout=6, markup=False)

    def _admit_background(self, handle, total):
        if handle is not None:
            return handle
        if not self._main_available():
            return None
        self.download_state._gate_cancel = Event()
        self._capture_download_context()
        return self.start_job("Downloading", total, cancel=self.download_state._gate_cancel)

    def download_track_in_background(self, track, gate_url=None, allow_prerequisite_retry=True, *, handle=None):
        handle = self._admit_background(handle, 1)
        if handle is None:
            return None
        return self.run_worker(
            partial(self.download_track_in_background_work, deepcopy(track), gate_url,
                    allow_prerequisite_retry, handle),
            thread=True, group='download', description="download_track_in_background",
        )

    def batch_download_in_background(self, items, allow_prerequisite_retry=True, *, handle=None):
        handle = self._admit_background(handle, len(items))
        if handle is None:
            return None
        return self.run_worker(
            partial(self.batch_download_in_background_work, deepcopy(items),
                    allow_prerequisite_retry, handle),
            thread=True, group='batch_download', description="batch_download_in_background",
        )

"""Fetching artist-provided files, one at a time or the whole visible list.

Composed by ``DiggerApp`` with explicit state and presentation callbacks.
"""

import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from threading import Event

from dj_digger import gate_models

from .. import links as links_module
from .. import soundcloud_errors as soundcloud
from ..models import Cancelled, Track, check_cancelled
from ..services import collection as dig_module
from ..services.downloads import FileBatchResult, PublishedFileUnrecorded
from ..state import GOT, SKIP
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


# Classification and summary order in one place. GateProfileRequired is
# deliberately absent - it pauses for configuration instead of failing.
# How many gates one batch hands to the private browser. Each open tab waits
# up to five minutes for its download; a playlist of refused gates must not
# become fifty tabs. The rest are left new for another run.
BROWSER_BATCH_MAX = 8

FAILURE_GROUPS = (
    ("auth", (gate_models.GateAuthenticationRequired,)),
    ("captcha", (gate_models.GateCaptchaRequired,)),
    ("consent", (gate_models.GateSocialActionsDisabled,)),
    ("manual", (gate_models.GateManualActionRequired,)),
    ("protocol", (gate_models.GateProtocolChanged, gate_models.GateUnavailable)),
    ("rejected", (gate_models.GateRejected,)),
    ("download", (gate_models.GateDownloadError, soundcloud.SoundCloudError)),
)


def _is_hypeddit(url: str | None) -> bool:
    return bool(url) and links_module.is_hypeddit_url(url)


def _downloadable(track: Track, gate_url: str | None) -> bool:
    """Whether there is anything to fetch: a free download, a direct file or a gate."""

    return bool(track.free_download or gate_url or track.has_direct_download)


def _gate_failure_group(error: Exception) -> str:
    return next(
        (name for name, types in FAILURE_GROUPS if isinstance(error, types)), "other"
    )


@dataclass(frozen=True)
class DownloadContext:
    source: str
    generation: str
    directory: Path
    view_generation: int
    timeout: float


@dataclass
class _BatchProgress:
    """What one batch pass has produced so far, shared between its two stages."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    hubs_changed: bool = False
    profile_items: list[tuple[Row, str | None]] = field(default_factory=list)
    auth_items: list[tuple[Row, str | None]] = field(default_factory=list)
    browser_items: list[tuple[Row, str]] = field(default_factory=list)
    # Why the HTTP flow gave up on each browser item, keyed by track: the
    # browser's own failure is only half the story without it.
    browser_reasons: dict[str, str] = field(default_factory=dict)
    deferred: int = 0
    failure_groups: Counter = field(default_factory=Counter)

    def record(
        self,
        row: Row,
        gate_url: str | None,
        outcome: str,
        result,
        changed: bool,
        *,
        retry_prerequisites: bool,
    ) -> str:
        """Sort one finished pool item into the bag.

        Returns what its row should show now: "downloaded", "failed", or
        "waiting" for everything that is going on to a wizard, the browser
        or another run.
        """

        self.hubs_changed = self.hubs_changed or changed
        if outcome == "hub":
            self.total -= 1
        elif outcome == "cancelled":
            pass  # Stopped by the user: not a failure, nothing to report.
        elif outcome == "downloaded":
            self.completed += 1
            return "downloaded"
        elif retry_prerequisites and isinstance(result, gate_models.GateProfileRequired):
            self.profile_items.append((row, gate_url))
        elif retry_prerequisites and isinstance(result, soundcloud.SoundCloudLoginRequired):
            self.auth_items.append((row, gate_url))
        elif isinstance(result, gate_models.BROWSER_REQUIRED_ERRORS) and _is_hypeddit(gate_url):
            if len(self.browser_items) < BROWSER_BATCH_MAX:
                self.browser_items.append((row, gate_url))
                self.browser_reasons[row.track.key] = str(result)
            else:
                self.deferred += 1
        else:
            self.failed += 1
            if isinstance(result, Exception):
                self.failure_groups[_gate_failure_group(result)] += 1
            return "failed"
        return "waiting"


class DownloadController:
    """Fetching artist-provided files, one at a time or the whole visible list."""

    def __init__(self, *, _find_gate_url, _main_available, _mark_existing_local_file, _paint_key, _set_records, call_from_thread, call_later, get_client, get_config, current_row, get_dig_options, download_service, download_state, job_progress, notify, operations, playlist_state, push_screen, refresh_rows, adopt_login, accounts, run_worker, show_error, start_job, state, status_of, targets, update_status, worker_scope, io):
        self.io = io
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
            if not _downloadable(row.track, gate_url):
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
                self._download_track_once(track, gate_url, allow_prerequisite_retry)

    @contextmanager
    def _download_worker(self, handle=None):
        """Count this thread among the downloads holding the SoundCloud client."""

        if handle is None:
            self._capture_download_context()
        handle = handle or self.operations.start("Downloading", cancel=self.download_state._gate_cancel)
        self.download_state._download_handle = handle
        with self.download_state._download_worker_lock:
            self.download_state._active_download_workers += 1
        try:
            yield
        finally:
            self.accounts.wait_authentication()
            with self.download_state._download_worker_lock:
                self.download_state._active_download_workers -= 1
            self.operations.finish(handle)
            try:
                self.call_from_thread(self.update_status)
            except RuntimeError:
                pass

    def _download_track_once(
        self,
        track: Track,
        gate_url: str | None,
        allow_prerequisite_retry: bool,
    ) -> None:
        key = track.key
        gate_url, changed = self._normalise_hypeddit_item(
            Row(0, track, links_module.categorise(track)), gate_url
        )
        if changed:
            self._persist_normalised_hubs()
        if not _downloadable(track, gate_url):
            self.call_from_thread(
                self._download_failed,
                key,
                "Hypeddit link is a store hub rather than a download gate",
            )
            return

        try:
            try:
                path = self._fetch_one(track, gate_url, self.download_state._download_context.directory)
            except gate_models.BROWSER_REQUIRED_ERRORS as exc:
                path = self._browser_fallback(track, gate_url, exc)
        except Cancelled:
            self.call_from_thread(self._settle_download_row, key)
            return
        except (gate_models.GateProfileRequired, soundcloud.SoundCloudLoginRequired) as exc:
            self.call_from_thread(self._settle_download_row, key)
            if allow_prerequisite_retry:
                item = (track, gate_url)
                ready = self._wait_download_prerequisites(
                    [item] if isinstance(exc, gate_models.GateProfileRequired) else [],
                    [item] if isinstance(exc, soundcloud.SoundCloudLoginRequired) else [],
                )
                if ready:
                    self._download_track_once(track, gate_url, False)
            else:
                self.call_from_thread(self._download_failed, key, str(exc))
            return
        except Exception as exc:
            self.call_from_thread(self._download_failed, key, str(exc))
            return
        self.call_from_thread(self._download_finished, key, path)

    def _fetch_one(
        self, track: Track, gate_url: str | None, directory: Path, session=None
    ) -> Path:
        """Fetch one track through the client, its byte progress painted on its row."""

        key = track.key

        def on_progress(downloaded: int, total_bytes: int | None) -> None:
            # 0.5 when the server sent no Content-Length: visibly moving
            # without pretending to know how far along it is.
            pct = min(1.0, downloaded / total_bytes) if total_bytes and total_bytes > 0 else 0.5
            with self.download_state._progress_lock:
                self.download_state._pending_progress[key] = (self.download_state._download_handle.id, pct)

        self.call_from_thread(self._update_track_progress, key, 0.0)
        return self.download_service.fetch(
            self.client,
            track,
            directory,
            gate_url=gate_url,
            on_progress=on_progress,
            session=session,
            cancel=self.download_state._gate_cancel,
        )

    def _browser_fallback(self, track: Track, gate_url: str | None, exc: Exception) -> Path:
        """Finish a Hypeddit gate the HTTP flow gave up on in the private browser.

        Anything else is not the browser's to fix, so its error passes straight
        through; a browser failure keeps the HTTP reason after it.
        """

        if not _is_hypeddit(gate_url):
            raise exc
        self.call_from_thread(
            self.notify, f"Finishing in the hidden browser: {exc}", timeout=5, markup=False
        )
        try:
            path = self.download_service.finish_gate(
                track,
                gate_url,
                self.download_state._download_context.directory,
                self.download_state._gate_cancel,
                status=self._gate_status,
                config=self.config,
            )
            return path
        except PublishedFileUnrecorded:
            raise
        except Exception as browser_exc:
            raise RuntimeError(f"{browser_exc} (after: {exc})") from browser_exc

    def _adopt_login(self, oauth_token: str) -> bool:
        return self.adopt_login(oauth_token)

    def _wait_download_prerequisites(self, profiles, auth_items):
        completed = Event()
        ready = []
        self.call_from_thread(
            self._resolve_download_prerequisites, profiles, auth_items,
            ready.extend, completed.set,
        )
        while not completed.wait(0.05):
            check_cancelled(self.download_state._gate_cancel)
        check_cancelled(self.download_state._gate_cancel)
        return ready

    def _resolve_download_prerequisites(
        self, profile_items: list, auth_items: list, retry, on_done=None
    ) -> None:
        """Run each required wizard once, then retry only approved items."""

        ready = []

        def finish() -> None:
            if ready and not self.download_state._gate_cancel.is_set():
                if on_done is None:
                    self.call_later(retry, ready)
                else:
                    retry(ready)
            if on_done is not None:
                on_done()

        def ask_for_soundcloud() -> None:
            if not auth_items or self.download_state._gate_cancel.is_set():
                finish()
                return

            def after_auth(oauth_token: str | None) -> None:
                if not oauth_token:
                    self.notify("SoundCloud login cancelled; download was not retried.", timeout=4)
                elif self._adopt_login(oauth_token):
                    ready.extend(auth_items)
                finish()

            self.push_screen(
                SoundCloudAuthScreen(self.accounts), after_auth
            )

        if profile_items:
            async def after_profile(saved) -> None:
                if saved and not self.download_state._gate_cancel.is_set():
                    try:
                        await self.io(self.accounts.save_profile, saved)
                    except Exception:
                        self.notify("Could not save the gate profile", severity="error")
                        finish()
                        return
                    ready.extend(profile_items)
                else:
                    self.notify("Gate profile cancelled; download was not retried.", timeout=4)
                ask_for_soundcloud()

            self.push_screen(GateProfileScreen(self.config), after_profile)
        else:
            ask_for_soundcloud()

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
                if _downloadable(row.track, gate_url):
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
                self._run_batch_download(items, allow_prerequisite_retry)

    def _run_batch_download(
        self,
        items: list[tuple[Row, str | None]],
        allow_prerequisite_retry: bool,
    ) -> None:
        items = [(row, gate_url) for row, gate_url in items if _downloadable(row.track, gate_url)]
        download_directory = self.download_state._download_context.directory
        progress = self._batch_pool_pass(
            items, download_directory, allow_prerequisite_retry
        )

        if progress.hubs_changed:
            self._persist_normalised_hubs()
        if progress.browser_items:
            self._batch_browser_pass(progress, download_directory)
        if progress.deferred:
            self.call_from_thread(
                self.notify,
                f"{progress.deferred} more gate{'s' if progress.deferred != 1 else ''} left new: "
                f"the browser takes {BROWSER_BATCH_MAX} at a time, run the batch again for the rest",
                timeout=8,
                markup=False,
            )

        pending = len(progress.profile_items) + len(progress.auth_items)
        self.call_from_thread(
            self._on_batch_download_complete,
            progress.completed,
            progress.failed,
            progress.total,
            pending,
            dict(progress.failure_groups),
        )
        if pending:
            ready = self._wait_download_prerequisites(progress.profile_items, progress.auth_items)
            if ready:
                self._run_batch_download(ready, False)

    def _batch_download_one(
        self, item: tuple[Row, str | None], download_directory: Path
    ):
        row, gate_url = item
        if self.download_state._gate_cancel.is_set():
            return (row, gate_url, "cancelled", None, False)
        gate_url, changed = self._normalise_hypeddit_item(row, gate_url)
        if not _downloadable(row.track, gate_url):
            return (row, gate_url, "hub", None, changed)

        # Its own session, not the client's: a gate is a multi-step flow held
        # together by its own cookies, and four of them sharing one jar
        # overwrite each other's state. Same reason dig._expand_one builds one
        # per track - this path simply never got the fix.
        try:
            path = self._fetch_one(row.track, gate_url, download_directory)
            return (row, gate_url, "downloaded", str(path), changed)
        except Cancelled:
            return (row, gate_url, "cancelled", None, changed)
        except Exception as exc:
            return (row, gate_url, "failed", exc, changed)

    def _batch_pool_pass(
        self,
        items: list[tuple[Row, str | None]],
        download_directory: Path,
        allow_prerequisite_retry: bool,
    ) -> "_BatchProgress":
        """Run the pool downloads and sort every outcome into the progress bag."""

        progress = _BatchProgress(total=len(items))
        # Gate providers enforce their own per-host limit; extra workers let
        # direct files continue while those slots are waiting on gate pages.
        self.download_state._download_executor = ThreadPoolExecutor(max_workers=8)
        try:
            futures = [
                self.download_state._download_executor.submit(
                    self._batch_download_one, item, download_directory
                )
                for item in items
            ]
            for future in as_completed(futures):
                row, gate_url, outcome, result, changed = future.result()
                verdict = progress.record(
                    row, gate_url, outcome, result, changed,
                    retry_prerequisites=allow_prerequisite_retry,
                )
                key = row.track.key
                if verdict == "downloaded":
                    self.call_from_thread(self._download_finished, key, result, toast=False)
                elif verdict == "failed":
                    self.call_from_thread(
                        self._download_failed, key, str(result), banner_label=row.track.label
                    )
                else:
                    self.call_from_thread(self._settle_download_row, key)
        finally:
            if self.download_state._download_executor is not None:
                self.download_state._download_executor.shutdown(wait=True, cancel_futures=True)
                self.download_state._download_executor = None
        return progress

    def _batch_browser_pass(
        self, progress: "_BatchProgress", download_directory: Path
    ) -> None:
        # One persistent profile cannot be driven by several Playwright threads.
        # All manual gates therefore share this worker's one context - hidden
        # first, a window only for what needs a person - and open as separate
        # tabs, with each tab's download bound back to its own row.
        rows_by_key = {row.track.key: row for row, _url in progress.browser_items}
        self.call_from_thread(self._browser_batch_started, len(progress.browser_items))
        try:
            browser_result = self.download_service.finish_gates(
                [(row.track, gate_url) for row, gate_url in progress.browser_items],
                download_directory,
                self.download_state._gate_cancel,
                status=self._gate_status,
                config=self.config,
            )
        except Exception as exc:
            browser_result = FileBatchResult(
                failures=tuple((key, gate_models.GateUnavailable(str(exc))) for key in rows_by_key)
            )
        finally:
            self.call_from_thread(self._browser_batch_finished)

        for result in browser_result.completed:
            key, path = result.key, result.path
            row = rows_by_key[key]
            progress.completed += 1
            self.call_from_thread(self._download_finished, row.track.key, str(path), toast=False)
        for key, exc in browser_result.failures:
            row = rows_by_key[key]
            progress.failed += 1
            progress.failure_groups[_gate_failure_group(exc)] += 1
            reason = progress.browser_reasons.get(key)
            message = f"{exc} (after: {reason})" if reason else str(exc)
            self.call_from_thread(self._download_failed, row.track.key, message, banner_label=row.track.label)

    def _normalise_hypeddit_item(self, row: Row, gate_url: str | None) -> tuple[str | None, bool]:
        context = self.download_state._download_context or self._capture_download_context()
        if not context.source or not _is_hypeddit(gate_url):
            return gate_url, False
        changed = bool(dig_module.expand_link_hubs([row.track], timeout=context.timeout))
        if changed:
            fields = {name: deepcopy(getattr(row.track, name)) for name in (
                "purchase_url", "purchase_title", "extra_links", "description",
            )}
            self.state.db.merge_track_metadata(context.source, context.generation, {row.track.key: fields})
            self.call_from_thread(self._hub_metadata_ready, row.track.key, fields, context.view_generation)
        refreshed = Row(row.position, row.track, links_module.categorise(row.track))
        return self._find_gate_url(refreshed), changed

    def _hub_metadata_ready(self, key, fields, view_generation):
        if view_generation != self.playlist_state._view_generation:
            return
        for row in self.playlist_state.rows:
            if row.track.key == key:
                for name, value in fields.items():
                    setattr(row.track, name, value)

    def _persist_normalised_hubs(self):
        if self.download_state._download_context.view_generation == self.playlist_state._view_generation:
            self.call_from_thread(self._hub_preflight_finished)

    def _hub_preflight_finished(self) -> None:
        tracks = [row.track for row in self.playlist_state.rows]
        self._set_records(links_module.categorise_all(tracks))
        self.refresh_rows()

    def _gate_status(self, message: str) -> None:
        """A word from the browser worker about what it is waiting on."""

        try:
            self.call_from_thread(self.notify, message, timeout=6, markup=False)
        except RuntimeError:
            pass

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
        if pending:
            msg += f" ({pending} waiting for configuration)"
        self.notify(msg, timeout=6, markup=False)

    def download_track_in_background(self, *args, **kwargs):
        return self.run_worker(
            partial(self.download_track_in_background_work, *args, **kwargs), thread=True, exclusive=True, group='download',
            description="download_track_in_background",
        )

    def batch_download_in_background(self, *args, **kwargs):
        return self.run_worker(
            partial(self.batch_download_in_background_work, *args, **kwargs), thread=True, exclusive=True, group='batch_download',
            description="batch_download_in_background",
        )

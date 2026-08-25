"""Fetching artist-provided files, one at a time or the whole visible list.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from textual import work

from .. import gates, soundcloud
from ..models import Track
from ..state import GOT, SKIP
from .rows import Row
from .screens import GateProfileScreen, SoundCloudAuthScreen

LOGGER = logging.getLogger(__name__)


def _gate_failure_group(error: Exception) -> str:
    if isinstance(error, gates.GateAuthenticationRequired):
        return "auth"
    if isinstance(error, gates.GateCaptchaRequired):
        return "captcha"
    if isinstance(
        error,
        (gates.GateManualActionRequired, gates.GateSocialActionsDisabled),
    ):
        return "manual"
    if isinstance(error, (gates.GateProtocolChanged, gates.GateUnavailable)):
        return "protocol"
    if isinstance(error, gates.GateRejected):
        return "rejected"
    return "other"


class DownloadMixin:
    """Fetching artist-provided files, one at a time or the whole visible list."""

    def action_download_track(self) -> None:
        if self._client_refresh_pending:
            self.notify("Finishing active downloads before refreshing SoundCloud login…")
            return
        row = self.current_row()
        if row is None:
            return

        gate_url = self._find_gate_url(row)

        if not row.track.free_download and not gate_url and not row.track.has_direct_download:
            self.notify("This track has no active SoundCloud free download or supported gate link", timeout=4)
            return

        self.notify(f"Downloading {row.track.label}...", timeout=3)
        self.download_track_in_background(row.track, gate_url)

    @work(thread=True, exclusive=True, group="download")
    def download_track_in_background(
        self,
        track: Track,
        gate_url: str | None = None,
        allow_prerequisite_retry: bool = True,
    ) -> None:
        self._begin_download_worker()
        try:
            self._download_track_once(
                track, gate_url, allow_prerequisite_retry
            )
        finally:
            self._end_download_worker()

    def _download_track_once(
        self,
        track: Track,
        gate_url: str | None,
        allow_prerequisite_retry: bool,
    ) -> None:
        key = track.key

        def on_progress(downloaded: int, total_bytes: int | None) -> None:
            pct = min(1.0, downloaded / total_bytes) if total_bytes and total_bytes > 0 else 0.5
            self.call_from_thread(self._update_track_progress, key, pct)

        try:
            self.call_from_thread(self._update_track_progress, key, 0.0)
            path = self.client.download_track(
                track,
                Path(self.config.download_directory),
                gate_url=gate_url,
                on_progress=on_progress,
            )
        except gates.BROWSER_REQUIRED_ERRORS as exc:
            if not gate_url or "hypeddit" not in gate_url.lower():
                self.call_from_thread(self._download_failed, key, str(exc))
                return
            try:
                path = gates.download_hypeddit_in_browser(
                    track,
                    gate_url,
                    Path(self.config.download_directory),
                    True,
                    self._gate_cancel,
                )
            except Exception as browser_exc:
                self.call_from_thread(self._download_failed, key, str(browser_exc))
                return
        except (gates.GateProfileRequired, soundcloud.SoundCloudLoginRequired) as exc:
            self.call_from_thread(self._download_waiting, key)
            if allow_prerequisite_retry:
                self.call_from_thread(
                    self._offer_single_retry, track, gate_url, exc
                )
            else:
                self.call_from_thread(self._download_failed, key, str(exc))
            return
        except Exception as exc:
            self.call_from_thread(self._download_failed, key, str(exc))
            return
        self.call_from_thread(self._download_finished, key, path)

    def _begin_download_worker(self) -> None:
        with self._download_worker_lock:
            self._active_download_workers += 1

    def _end_download_worker(self) -> None:
        old_client = None
        callbacks = []
        oauth_token = None
        with self._download_worker_lock:
            self._active_download_workers -= 1
            if self._active_download_workers == 0 and self._client_refresh_pending:
                old_client = self._client
                self._client = None
                self._client_refresh_pending = False
                oauth_token = self._client_refresh_token
                self._client_refresh_token = None
                callbacks = self._client_refresh_callbacks
                self._client_refresh_callbacks = []
        if old_client is not None:
            try:
                old_client.close()
            except Exception as exc:
                LOGGER.debug("Could not close retired SoundCloud client: %s", exc)
        if callbacks:
            self.call_from_thread(
                self._complete_client_refresh, callbacks, oauth_token
            )

    def _request_client_refresh(self, oauth_token: str, callback) -> None:
        old_client = None
        with self._download_worker_lock:
            if self._active_download_workers:
                self._client_refresh_pending = True
                self._client_refresh_token = oauth_token
                self._client_refresh_callbacks.append(callback)
                return
            old_client = self._client
            self._client = None
        if old_client is not None:
            try:
                old_client.close()
            except Exception as exc:
                LOGGER.debug("Could not close retired SoundCloud client: %s", exc)
        self._client = soundcloud.SoundCloudClient(
            config=self.config, oauth_token=oauth_token
        )
        self.call_later(callback)

    def _complete_client_refresh(
        self, callbacks: list, oauth_token: str | None
    ) -> None:
        if oauth_token:
            self._client = soundcloud.SoundCloudClient(
                config=self.config, oauth_token=oauth_token
            )
        for callback in callbacks:
            callback()

    def _download_waiting(self, key: str) -> None:
        self.download_progress.pop(key, None)
        self._dirty_download_rows.discard(key)
        self._paint_download_row(key)
        self.update_status()

    def _offer_single_retry(
        self, track: Track, gate_url: str | None, error: Exception
    ) -> None:
        item = (track, gate_url)
        profiles = [item] if isinstance(error, gates.GateProfileRequired) else []
        auth_items = [item] if isinstance(error, soundcloud.SoundCloudLoginRequired) else []
        self._resolve_download_prerequisites(
            profiles,
            auth_items,
            lambda ready: self.download_track_in_background(
                ready[0][0], ready[0][1], allow_prerequisite_retry=False
            ),
        )

    def _resolve_download_prerequisites(
        self, profile_items: list, auth_items: list, retry
    ) -> None:
        """Run each required wizard once, then retry only approved items."""

        ready = []

        def finish() -> None:
            if ready:
                self.call_later(retry, ready)

        def ask_for_soundcloud() -> None:
            if not auth_items:
                finish()
                return

            def after_auth(oauth_token: str | None) -> None:
                if oauth_token:
                    ready.extend(auth_items)
                    self._request_client_refresh(oauth_token, finish)
                    return
                else:
                    self.notify("SoundCloud login cancelled; download was not retried.", timeout=4)
                finish()

            self.push_screen(
                SoundCloudAuthScreen(lambda: self.client.client_id), after_auth
            )

        if profile_items:
            def after_profile(saved: bool) -> None:
                if saved:
                    ready.extend(profile_items)
                else:
                    self.notify("Gate profile cancelled; download was not retried.", timeout=4)
                ask_for_soundcloud()

            self.push_screen(GateProfileScreen(self.config), after_profile)
        else:
            ask_for_soundcloud()

    def _update_track_progress(self, key: str, pct: float) -> None:
        self.download_progress[key] = pct
        self._dirty_download_rows.add(key)
        now = time.time()
        if now - self._last_progress_redraw >= 0.08:
            self._last_progress_redraw = now
            dirty_keys = tuple(self._dirty_download_rows)
            self._dirty_download_rows.clear()
            for dirty_key in dirty_keys:
                self._paint_download_row(dirty_key)

    def _paint_download_row(self, key: str) -> None:
        for index, row in enumerate(self.visible_rows):
            if row.track.key == key:
                self._paint_row(index)
                return

    def _download_failed(self, key: str, message: str) -> None:
        self.download_progress.pop(key, None)
        self._dirty_download_rows.discard(key)
        self._paint_download_row(key)
        self.update_status()
        self.notify(f"Download failed: {message}", severity="error", timeout=6)

    def _download_finished(self, key: str, path: Path) -> None:
        self.download_progress.pop(key, None)
        self._dirty_download_rows.discard(key)
        was_visible = any(row.track.key == key for row in self.visible_rows)
        self.state.set(key, GOT)
        if self.hide_handled and was_visible:
            self.refresh_rows()
        else:
            self._paint_download_row(key)
            self.update_status()
        self.notify(f"Downloaded to {path}", timeout=5)

    def action_batch_download(self) -> None:
        """Download all eligible tracks in current view (SoundCloud direct + Hypeddit/ToneDen gates) in parallel."""
        if self._client_refresh_pending:
            self.notify("Finishing active downloads before refreshing SoundCloud login…")
            return
        eligible: list[tuple[Row, str | None]] = []
        for row in self.visible_rows:
            status = self.status_of(row)
            if status in (GOT, SKIP):
                continue
            gate_url = self._find_gate_url(row)
            if row.track.free_download or gate_url or row.track.has_direct_download:
                eligible.append((row, gate_url))

        if not eligible:
            self.notify("No downloadable free or gate tracks in current view", timeout=3)
            return

        self.notify(f"Starting parallel batch download for {len(eligible)} tracks...", timeout=4)
        self.batch_download_in_background(eligible)

    @work(thread=True, exclusive=True, group="batch_download")
    def batch_download_in_background(
        self,
        items: list[tuple[Row, str | None]],
        allow_prerequisite_retry: bool = True,
    ) -> None:
        self._begin_download_worker()
        try:
            self._run_batch_download(items, allow_prerequisite_retry)
        finally:
            self._end_download_worker()

    def _run_batch_download(
        self,
        items: list[tuple[Row, str | None]],
        allow_prerequisite_retry: bool,
    ) -> None:
        completed_count = 0
        failed_count = 0
        total = len(items)
        profile_items: list[tuple[Row, str | None]] = []
        auth_items: list[tuple[Row, str | None]] = []
        browser_items: list[tuple[Row, str]] = []
        failure_groups: Counter[str] = Counter()

        def download_one(item: tuple[Row, str | None]):
            row, gate_url = item
            key = row.track.key

            def on_progress(downloaded: int, total_bytes: int | None) -> None:
                pct = min(1.0, downloaded / total_bytes) if total_bytes and total_bytes > 0 else 0.5
                self.call_from_thread(self._update_track_progress, key, pct)

            # Its own session, not the client's: a gate is a multi-step flow held
            # together by its own cookies, and four of them sharing one jar
            # overwrite each other's state. Same reason dig._expand_one builds one
            # per track - this path simply never got the fix.
            session = soundcloud.create_requests_session()
            try:
                self.call_from_thread(self._update_track_progress, key, 0.0)
                path = self.client.download_track(
                    row.track,
                    Path(self.config.download_directory),
                    gate_url=gate_url,
                    on_progress=on_progress,
                    session=session,
                )
                return (row, gate_url, True, str(path))
            except Exception as exc:
                return (row, gate_url, False, exc)
            finally:
                session.close()

        self._download_executor = ThreadPoolExecutor(max_workers=4)
        try:
            futures = [self._download_executor.submit(download_one, item) for item in items]
            for future in as_completed(futures):
                row, gate_url, success, result = future.result()
                if success:
                    completed_count += 1
                    self.call_from_thread(self._on_batch_track_finished, row, result)
                elif allow_prerequisite_retry and isinstance(
                    result, gates.GateProfileRequired
                ):
                    profile_items.append((row, gate_url))
                    self.call_from_thread(self._download_waiting, row.track.key)
                elif allow_prerequisite_retry and isinstance(
                    result, soundcloud.SoundCloudLoginRequired
                ):
                    auth_items.append((row, gate_url))
                    self.call_from_thread(self._download_waiting, row.track.key)
                elif (
                    isinstance(result, gates.BROWSER_REQUIRED_ERRORS)
                    and gate_url
                    and "hypeddit" in gate_url.lower()
                ):
                    browser_items.append((row, gate_url))
                    self.call_from_thread(self._download_waiting, row.track.key)
                else:
                    failed_count += 1
                    if isinstance(result, Exception):
                        failure_groups[_gate_failure_group(result)] += 1
                    self.call_from_thread(self._on_batch_track_failed, row, str(result))
        finally:
            if self._download_executor is not None:
                self._download_executor.shutdown(wait=False)
                self._download_executor = None

        # One persistent profile cannot be driven by several Playwright threads.
        # Browser fallbacks therefore run after the parallel HTTP/file phase.
        for row, gate_url in browser_items:
            try:
                path = gates.download_hypeddit_in_browser(
                    row.track,
                    gate_url,
                    Path(self.config.download_directory),
                    False,
                    self._gate_cancel,
                )
            except Exception as exc:
                failed_count += 1
                failure_groups[_gate_failure_group(exc)] += 1
                self.call_from_thread(self._on_batch_track_failed, row, str(exc))
            else:
                completed_count += 1
                self.call_from_thread(self._on_batch_track_finished, row, str(path))

        pending = len(profile_items) + len(auth_items)
        self.call_from_thread(
            self._on_batch_download_complete,
            completed_count,
            failed_count,
            total,
            pending,
            dict(failure_groups),
        )
        if pending:
            self.call_from_thread(
                self._resolve_download_prerequisites,
                profile_items,
                auth_items,
                lambda ready: self.batch_download_in_background(
                    ready, allow_prerequisite_retry=False
                ),
            )

    def _on_batch_track_finished(self, row: Row, path_str: str) -> None:
        key = row.track.key
        self.download_progress.pop(key, None)
        self._dirty_download_rows.discard(key)
        was_visible = any(visible.track.key == key for visible in self.visible_rows)
        self.state.set(key, GOT)
        if self.hide_handled and was_visible:
            self.refresh_rows()
        else:
            self._paint_download_row(key)
            self.update_status()

    def _on_batch_track_failed(self, row: Row, message: str) -> None:
        key = row.track.key
        self.download_progress.pop(key, None)
        self._dirty_download_rows.discard(key)
        if "requires browser completion" not in message:
            self.show_error(f"Batch download failed [{row.track.label}]: {message}")
        self._paint_download_row(key)
        self.update_status()

    def _on_batch_download_complete(
        self,
        completed: int,
        failed: int,
        total: int,
        pending: int = 0,
        failure_groups: dict[str, int] | None = None,
    ) -> None:
        stale_keys = tuple(self.download_progress)
        self.download_progress.clear()
        self._dirty_download_rows.clear()
        for key in stale_keys:
            self._paint_download_row(key)
        self.update_status()
        msg = f"Batch download finished: {completed}/{total} downloaded"
        if failed > 0:
            msg += f" ({failed} failed)"
        grouped = [
            f"{name}={count}"
            for name in ("auth", "captcha", "manual", "protocol", "rejected", "other")
            if (count := (failure_groups or {}).get(name, 0))
        ]
        if grouped:
            msg += f" [{', '.join(grouped)}]"
        if pending:
            msg += f" ({pending} waiting for configuration)"
        self.notify(msg, timeout=6)

"""Fetching artist-provided files, one at a time or the whole visible list.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from textual import work

from .. import dig as dig_module
from .. import gates, soundcloud
from .. import library as library_module
from .. import links as links_module
from ..models import Cancelled, Track
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
    ("auth", (gates.GateAuthenticationRequired,)),
    ("captcha", (gates.GateCaptchaRequired,)),
    ("consent", (gates.GateSocialActionsDisabled,)),
    ("manual", (gates.GateManualActionRequired,)),
    ("protocol", (gates.GateProtocolChanged, gates.GateUnavailable)),
    ("rejected", (gates.GateRejected,)),
    ("download", (gates.GateDownloadError, soundcloud.SoundCloudError)),
)


LOGIN_WAITS_FOR_DOWNLOADS = (
    "Signed in to SoundCloud, but a download is still running on the old login: "
    "let it finish or stop it with ctrl+x, then press w again"
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
        elif retry_prerequisites and isinstance(result, gates.GateProfileRequired):
            self.profile_items.append((row, gate_url))
        elif retry_prerequisites and isinstance(result, soundcloud.SoundCloudLoginRequired):
            self.auth_items.append((row, gate_url))
        elif isinstance(result, gates.BROWSER_REQUIRED_ERRORS) and _is_hypeddit(gate_url):
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


class DownloadMixin:
    """Fetching artist-provided files, one at a time or the whole visible list."""

    def _download_directory(self) -> Path:
        base = Path(self.config.download_directory).expanduser()
        title = self.crate.title if self.crate is not None else self.crate_title
        if not title.strip():
            return base
        folder = _playlist_folder_name(title)
        return base if base.name.casefold() == folder.casefold() else base / folder

    def action_download_track(self) -> None:
        row = self.current_row()
        if row is None:
            return
        if self._mark_existing_local_file(row.track):
            self.refresh_rows()
            self.notify(f"Already on disk: {row.track.local_path}", timeout=4)
            return
        self._gate_cancel.clear()

        gate_url = self._find_gate_url(row)

        if not _downloadable(row.track, gate_url):
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
        with self._download_worker():
            self._download_track_once(track, gate_url, allow_prerequisite_retry)

    @contextmanager
    def _download_worker(self):
        """Count this thread among the downloads holding the SoundCloud client."""

        with self._download_worker_lock:
            self._active_download_workers += 1
        try:
            yield
        finally:
            with self._download_worker_lock:
                self._active_download_workers -= 1

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
                path = self._fetch_one(track, gate_url, self._download_directory())
            except gates.BROWSER_REQUIRED_ERRORS as exc:
                path = self._browser_fallback(track, gate_url, exc)
        except Cancelled:
            self.call_from_thread(self._settle_download_row, key)
            return
        except (gates.GateProfileRequired, soundcloud.SoundCloudLoginRequired) as exc:
            self.call_from_thread(self._settle_download_row, key)
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

    def _fetch_one(
        self, track: Track, gate_url: str | None, directory: Path, session=None
    ) -> Path:
        """Fetch one track through the client, its byte progress painted on its row."""

        key = track.key

        def on_progress(downloaded: int, total_bytes: int | None) -> None:
            # 0.5 when the server sent no Content-Length: visibly moving
            # without pretending to know how far along it is.
            pct = min(1.0, downloaded / total_bytes) if total_bytes and total_bytes > 0 else 0.5
            self.call_from_thread(self._update_track_progress, key, pct)

        self.call_from_thread(self._update_track_progress, key, 0.0)
        return self.client.download_track(
            track,
            directory,
            gate_url=gate_url,
            on_progress=on_progress,
            session=session,
            cancel=self._gate_cancel,
        )

    def _browser_fallback(self, track: Track, gate_url: str | None, exc: Exception) -> Path:
        """Finish a Hypeddit gate the HTTP flow gave up on in the private browser.

        Anything else is not the browser's to fix, so its error passes straight
        through; a browser failure keeps the HTTP reason after it.
        """

        if not _is_hypeddit(gate_url):
            raise exc
        self.call_from_thread(
            self.notify, f"Finishing in the browser: {exc}", timeout=5, markup=False
        )
        try:
            return gates.download_hypeddit_in_browser(
                track,
                gate_url,
                self._download_directory(),
                self._gate_cancel,
                social=self.config.gate_social_actions,
                status=self._gate_status,
            )
        except Exception as browser_exc:
            raise RuntimeError(f"{browser_exc} (after: {exc})") from browser_exc

    def _adopt_login(self, oauth_token: str) -> bool:
        """Put a fresh SoundCloud login to use. False while a download still holds the old one.

        Every download thread shares the client, and closing it under one
        mid-request is a crash. Rather than queue the swap behind a callback,
        the person is told to let the download finish and ask again.
        """

        if self._active_download_workers:
            self.notify(LOGIN_WAITS_FOR_DOWNLOADS, severity="warning", timeout=8)
            return False
        old, self._client = (
            self._client,
            soundcloud.SoundCloudClient(config=self.config, oauth_token=oauth_token),
        )
        if old is not None:
            try:
                old.close()
            except Exception as exc:
                LOGGER.debug("Could not close retired SoundCloud client: %s", exc)
        return True

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
                if not oauth_token:
                    self.notify("SoundCloud login cancelled; download was not retried.", timeout=4)
                elif self._adopt_login(oauth_token):
                    ready.extend(auth_items)
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
        # ~12 repaints/s, below the 1/30 s UI ticker (keymap.TICK), so a fast
        # download can never outrun the frame budget with row repaints.
        if now - self._last_progress_redraw >= 0.08:
            self._last_progress_redraw = now
            dirty_keys = tuple(self._dirty_download_rows)
            self._dirty_download_rows.clear()
            for dirty_key in dirty_keys:
                self._paint_key(dirty_key)

    def _settle_download_row(self, key: str) -> None:
        """Drop the progress bookkeeping for a row and repaint it."""

        self.download_progress.pop(key, None)
        self._dirty_download_rows.discard(key)
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
            self.job_progress(failed=1)
            return
        self._settle_download_row(key)
        self.notify(f"Download failed: {message}", severity="error", timeout=6)

    def _download_finished(
        self, key: str, path: Path | str, *, toast: bool = True
    ) -> None:
        self.download_progress.pop(key, None)
        self._dirty_download_rows.discard(key)
        was_visible = any(row.track.key == key for row in self.visible_rows)
        for row in self.rows:
            if row.track.key == key:
                row.track.local_path = str(path)
                break
        self.state.set_local_file(key, path)
        if self.hide_handled and was_visible:
            self.refresh_rows()
        else:
            self._paint_key(key)
        self.job_progress(1)
        self.update_status()
        if toast:
            self.notify(f"Downloaded to {path}", timeout=5)

    def action_batch_download(self) -> None:
        """Download all eligible tracks in current view (SoundCloud direct + Hypeddit/ToneDen gates) in parallel."""
        eligible: list[tuple[Row, str | None]] = []
        local_matches = False
        for row in self.targets():
            if self._mark_existing_local_file(row.track):
                local_matches = True
                continue
            status = self.status_of(row)
            if status in (GOT, SKIP):
                continue
            gate_url = self._find_gate_url(row)
            if _downloadable(row.track, gate_url):
                eligible.append((row, gate_url))

        if local_matches:
            self.refresh_rows()

        if not eligible:
            self.notify("No downloadable free or gate tracks in current view", timeout=3)
            return

        self._gate_cancel.clear()
        for row, _gate_url in eligible:
            self.download_progress[row.track.key] = 0.0
            self._paint_key(row.track.key)
        self.start_job("Downloading", len(eligible), cancel=self._gate_cancel)
        self.notify(
            f"Checking and downloading {len(eligible)} tracks in parallel...",
            timeout=4,
        )
        self.batch_download_in_background(eligible)

    @work(thread=True, exclusive=True, group="batch_download")
    def batch_download_in_background(
        self,
        items: list[tuple[Row, str | None]],
        allow_prerequisite_retry: bool = True,
    ) -> None:
        with self._download_worker():
            self._run_batch_download(items, allow_prerequisite_retry)

    def _run_batch_download(
        self,
        items: list[tuple[Row, str | None]],
        allow_prerequisite_retry: bool,
    ) -> None:
        items = [(row, gate_url) for row, gate_url in items if _downloadable(row.track, gate_url)]
        download_directory = self._download_directory()
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
            self.call_from_thread(
                self._resolve_download_prerequisites,
                progress.profile_items,
                progress.auth_items,
                lambda ready: self.batch_download_in_background(
                    ready, allow_prerequisite_retry=False
                ),
            )

    def _batch_download_one(
        self, item: tuple[Row, str | None], download_directory: Path
    ):
        row, gate_url = item
        if self._gate_cancel.is_set():
            return (row, gate_url, "cancelled", None, False)
        gate_url, changed = self._normalise_hypeddit_item(row, gate_url)
        if not _downloadable(row.track, gate_url):
            return (row, gate_url, "hub", None, changed)

        # Its own session, not the client's: a gate is a multi-step flow held
        # together by its own cookies, and four of them sharing one jar
        # overwrite each other's state. Same reason dig._expand_one builds one
        # per track - this path simply never got the fix.
        session = soundcloud.create_requests_session()
        try:
            path = self._fetch_one(row.track, gate_url, download_directory, session=session)
            return (row, gate_url, "downloaded", str(path), changed)
        except Cancelled:
            return (row, gate_url, "cancelled", None, changed)
        except Exception as exc:
            return (row, gate_url, "failed", exc, changed)
        finally:
            session.close()

    def _batch_pool_pass(
        self,
        items: list[tuple[Row, str | None]],
        download_directory: Path,
        allow_prerequisite_retry: bool,
    ) -> "_BatchProgress":
        """Run the pool downloads and sort every outcome into the progress bag."""

        progress = _BatchProgress(total=len(items))
        # Four workers: enough to overlap gate waits, few enough to stay polite
        # to SoundCloud and the gate providers (each worker owns one session).
        self._download_executor = ThreadPoolExecutor(max_workers=4)
        try:
            futures = [
                self._download_executor.submit(
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
            if self._download_executor is not None:
                self._download_executor.shutdown(wait=False)
                self._download_executor = None
        return progress

    def _batch_browser_pass(
        self, progress: "_BatchProgress", download_directory: Path
    ) -> None:
        # One persistent profile cannot be driven by several Playwright threads.
        # All manual gates therefore share this worker's one context and open as
        # separate tabs, with each tab's download bound back to its own row.
        rows_by_key = {row.track.key: row for row, _url in progress.browser_items}
        self.call_from_thread(self._browser_batch_started, len(progress.browser_items))
        try:
            browser_result = gates.download_hypeddit_batch_in_browser(
                [(row.track, gate_url) for row, gate_url in progress.browser_items],
                download_directory,
                self._gate_cancel,
                social=self.config.gate_social_actions,
                status=self._gate_status,
            )
        except Exception as exc:
            browser_result = gates.HypedditBrowserBatchResult(
                failures=tuple((key, gates.GateUnavailable(str(exc))) for key in rows_by_key)
            )
        finally:
            self.call_from_thread(self._browser_batch_finished)

        for key, path in browser_result.completed:
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

    def _normalise_hypeddit_item(
        self, row: Row, gate_url: str | None
    ) -> tuple[str | None, bool]:
        if self.crate is None or not _is_hypeddit(gate_url):
            return gate_url, False
        changed = bool(
            dig_module.expand_link_hubs(
                [row.track], timeout=self.dig_options.timeout
            )
        )
        refreshed = Row(
            row.position,
            row.track,
            links_module.categorise(row.track),
        )
        return self._find_gate_url(refreshed), changed

    def _persist_normalised_hubs(self) -> None:
        if self.crate is None:
            return
        try:
            library_module.save(self.crate)
        except Exception as exc:
            LOGGER.warning("Could not persist normalised Hypeddit links: %s", exc)
        self.call_from_thread(self._hub_preflight_finished)

    def _hub_preflight_finished(self) -> None:
        tracks = [row.track for row in self.rows]
        self._set_records(links_module.categorise_all(tracks))
        self.refresh_rows()

    def _gate_status(self, message: str) -> None:
        """A word from the browser worker about what it is waiting on."""

        try:
            self.call_from_thread(self.notify, message, timeout=6, markup=False)
        except RuntimeError:
            pass

    def _browser_batch_started(self, count: int) -> None:
        self._browser_batch_active = True
        self.notify(
            f"Opened {count} Hypeddit tab{'s' if count != 1 else ''}. "
            "Complete them, close Chromium when finished, or press ctrl+x to stop.",
            timeout=8,
            markup=False,
        )

    def _browser_batch_finished(self) -> None:
        self._browser_batch_active = False

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
            self._paint_key(key)
        self.finish_job()
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

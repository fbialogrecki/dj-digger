"""Fetching artist-provided files, one at a time or the whole visible list.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from textual import work

from ..models import Track
from ..state import GOT, SKIP
from .rows import Row

LOGGER = logging.getLogger(__name__)


class DownloadMixin:
    """Fetching artist-provided files, one at a time or the whole visible list."""

    def action_download_track(self) -> None:
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
    def download_track_in_background(self, track: Track, gate_url: str | None = None) -> None:
        key = track.key

        def on_progress(downloaded: int, total_bytes: int | None) -> None:
            pct = min(1.0, downloaded / total_bytes) if total_bytes and total_bytes > 0 else 0.5
            self.call_from_thread(self._update_track_progress, key, pct)

        try:
            self.call_from_thread(self._update_track_progress, key, 0.05)
            path = self.client.download_track(
                track,
                Path.home() / "Downloads",
                gate_url=gate_url,
                on_progress=on_progress,
            )
        except Exception as exc:
            self.call_from_thread(self._download_failed, key, str(exc))
            return
        self.call_from_thread(self._download_finished, key, path)

    def _update_track_progress(self, key: str, pct: float) -> None:
        self.download_progress[key] = pct
        now = time.time()
        if now - self._last_progress_redraw >= 0.08:
            self._last_progress_redraw = now
            self.refresh_rows()

    def _download_failed(self, key: str, message: str) -> None:
        self.download_progress.pop(key, None)
        self.refresh_rows()
        self.notify(f"Download failed: {message}", severity="error", timeout=6)

    def _download_finished(self, key: str, path: Path) -> None:
        self.download_progress.pop(key, None)
        self.state.set(key, GOT)
        self.refresh_rows()
        self.notify(f"Downloaded to {path}", timeout=5)

    def action_batch_download(self) -> None:
        """Download all eligible tracks in current view (SoundCloud direct + Hypeddit/ToneDen gates) in parallel."""
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
    def batch_download_in_background(self, items: list[tuple[Row, str | None]]) -> None:
        completed_count = 0
        failed_count = 0
        total = len(items)

        def download_one(item: tuple[Row, str | None]) -> tuple[Row, bool, str]:
            row, gate_url = item
            key = row.track.key

            def on_progress(downloaded: int, total_bytes: int | None) -> None:
                pct = min(1.0, downloaded / total_bytes) if total_bytes and total_bytes > 0 else 0.5
                self.call_from_thread(self._update_track_progress, key, pct)

            try:
                self.call_from_thread(self._update_track_progress, key, 0.05)
                path = self.client.download_track(
                    row.track,
                    Path.home() / "Downloads",
                    gate_url=gate_url,
                    on_progress=on_progress,
                )
                return (row, True, str(path))
            except Exception as exc:
                return (row, False, str(exc))

        self._download_executor = ThreadPoolExecutor(max_workers=4)
        try:
            futures = [self._download_executor.submit(download_one, item) for item in items]
            for future in as_completed(futures):
                row, success, result = future.result()
                if success:
                    completed_count += 1
                    self.call_from_thread(self._on_batch_track_finished, row, result)
                else:
                    failed_count += 1
                    self.call_from_thread(self._on_batch_track_failed, row, result)
        finally:
            if self._download_executor is not None:
                self._download_executor.shutdown(wait=False)
                self._download_executor = None

        self.call_from_thread(self._on_batch_download_complete, completed_count, failed_count, total)

    def _on_batch_track_finished(self, row: Row, path_str: str) -> None:
        key = row.track.key
        self.download_progress.pop(key, None)
        self.state.set(key, GOT)
        self.refresh_rows()

    def _on_batch_track_failed(self, row: Row, message: str) -> None:
        key = row.track.key
        self.download_progress.pop(key, None)
        if "requires browser completion" not in message:
            self.show_error(f"Batch download failed [{row.track.label}]: {message}")
        self.refresh_rows()

    def _on_batch_download_complete(self, completed: int, failed: int, total: int) -> None:
        self.download_progress.clear()
        self.refresh_rows()
        msg = f"Batch download finished: {completed}/{total} downloaded"
        if failed > 0:
            msg += f" ({failed} require manual browser completion - press 'o' to open)"
        self.notify(msg, timeout=6)

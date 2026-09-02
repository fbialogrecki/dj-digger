"""Turning a pasted link into a crate, without blocking the interface.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

from textual import work

from .. import dig as dig_module
from .. import library as library_module
from .. import links as links_module
from ..models import Cancelled, Crate
from .keymap import (
    SPINNER_EVERY,
)
from .screens import AskLinkScreen


class DiggingMixin:
    """Turning a pasted link into a crate, without blocking the interface."""

    def _spin(self) -> None:
        if self._frame % SPINNER_EVERY == 0:
            self._draw_digging()

    def _draw_digging(self) -> None:
        """Something turning is the difference between working and hung.

        Drawn through the status bar's own update, so a resize or a mark
        landing mid-dig redraws the spinner rather than wiping it.
        """

        self.update_status()

    def action_dig_link(self) -> None:
        if self._digging:
            self.notify("Already digging - hold on", timeout=2)
            return
        message = "Paste a SoundCloud link" if self.rows else "What are we digging?"
        self.push_screen(AskLinkScreen(message=message), self._link_entered)

    def _link_entered(self, target: str | None) -> None:
        if not target:
            if not self.rows:
                # Nothing was asked for and there is nothing to show.
                self.exit()
            return
        self._start_dig(target)

    def _start_dig(self, target: str) -> None:
        self._digging = True
        self._dig_cancel.clear()
        self._dig_message = f"Digging {target}"
        # The rows stay on screen: a refresh of a big crate used to blank the
        # table for as long as the dig took.
        self.start_job("Digging", cancel=self._dig_cancel, detail=self._dig_message)
        self._draw_digging()
        self.dig_in_background(target)

    @work(thread=True, exclusive=True)
    def dig_in_background(self, target: str) -> None:
        def on_progress(stage: str, done: int, total: int | None) -> None:
            suffix = f" {done}/{total}" if total else ""
            # The ticker draws it, so the spinner keeps turning between stages.
            self._dig_message = f"{stage}{suffix}"
            try:
                self.call_from_thread(self.job_progress, detail=self._dig_message)
            except RuntimeError:
                pass  # the app is gone; the worker finishes on its own

        try:
            crate = dig_module.dig(
                target,
                limit=self.dig_options.limit,
                timeout=self.dig_options.timeout,
                delay=self.dig_options.delay,
                on_progress=on_progress,
                cancel=self._dig_cancel,
            )
        except Cancelled:
            self.call_from_thread(self._dig_failed, "Dig stopped")
            return
        except Exception as exc:  # a worker must never take the app down with it
            self.call_from_thread(self._dig_failed, str(exc))
            return
        self.call_from_thread(self._dig_finished, crate)

    def _finish_digging(self) -> None:
        self._digging = False
        self.finish_job()

    def _dig_failed(self, message: str) -> None:
        self._finish_digging()
        self.refresh_rows(keep_cursor=False)
        self.notify(message, severity="error", timeout=8)
        # Only re-ask when there is nothing to fall back to; a failed refresh
        # should not turn into a prompt for a different link.
        if not self.rows:
            self.action_dig_link()

    def _dig_finished(self, crate: Crate) -> None:
        self._finish_digging()
        if not crate.tracks:
            self._dig_failed(f"Found no tracks behind {crate.source}")
            return

        # Adding and refreshing both land here, so both persist the same way.
        record = library_module.remember(crate)
        self.load_crate(record)
        self.call_next(self.reload_sidebar)
        records = self.all_records()

        written = None
        if self.export_format != "none":
            written = links_module.export_records(
                records, self.export_format, self.export_path
            )
        message = f"{len(crate.tracks)} tracks, {len(records)} links"
        if written:
            message += f" - saved to {written}"
        self.notify(message, timeout=5)

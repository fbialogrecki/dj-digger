"""One line in the status bar for whatever long job is running, and one key to stop it.

A dig, a download batch, a bulk open, a scan and a cart batch each used to
report in their own way - a spinner, a per-row percentage, a toast at the end -
and none of them could be stopped from the keyboard except the Chromium batch.
The job is the one place they all report to, and ``ctrl+x`` reads it.

Mixed into ``DiggerApp``; ``self.job`` is set up in its ``__init__``.
"""

import asyncio
import threading
from dataclasses import dataclass


@dataclass
class Job:
    name: str
    total: int | None = None
    done: int = 0
    failed: int = 0
    cancel: threading.Event | asyncio.Event | None = None
    detail: str = ""
    # Whether the frame timer runs to turn the spinner. A scan at startup is
    # not worth thirty frames a second; its line updates when it finishes.
    animate: bool = True

    def describe(self) -> str:
        parts = [self.name]
        if self.total:
            parts.append(f"{self.done}/{self.total}")
        elif self.done:
            parts.append(str(self.done))
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.detail:
            parts.append(self.detail)
        if self.cancel is not None:
            parts.append("^X stop")
        return " \u00b7 ".join(parts)


class JobMixin:
    """Start, advance, finish and cancel the one job the status bar reports on."""

    def start_job(
        self,
        name: str,
        total: int | None = None,
        *,
        cancel: threading.Event | asyncio.Event | None = None,
        detail: str = "",
        animate: bool = True,
    ) -> Job:
        self.job = Job(name=name, total=total, cancel=cancel, detail=detail, animate=animate)
        if animate:
            self._wake()
        self.update_status()
        return self.job

    def job_progress(
        self,
        done: int = 0,
        *,
        failed: int = 0,
        detail: str | None = None,
    ) -> None:
        """Advance the job by ``done`` and ``failed`` items. UI thread only.

        Per-item events only: a download's byte progress stays on its own row.
        """

        job = self.job
        if job is None:
            return
        job.done += done
        job.failed += failed
        if detail is not None:
            job.detail = detail
        self.update_status()

    def finish_job(self) -> None:
        if self.job is None:
            return
        self.job = None
        self.update_status()
        if not self.player.playing:
            self._sleep()

    def action_cancel_job(self) -> None:
        job = self.job
        if job is not None and job.cancel is not None:
            job.cancel.set()
            self.notify(f"Stopping {job.name.lower()}; what finished is kept", timeout=4)
            return
        # Work started without a job line - a batch driven from a test, or a
        # download already in flight - still answers to the same key.
        if self._active_download_workers:
            self._gate_cancel.set()
            self.notify("Stopping downloads; finished files are kept", timeout=4)
            return
        self.notify("Nothing to stop", timeout=2)

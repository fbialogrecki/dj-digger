"""Persist completed file effects independently of the lifetime of the view."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .. import gate_models
from .. import links as links_module
from .. import soundcloud_errors as soundcloud
from ..diagnostics import log_safe_text
from ..files import copy_local_file
from ..gates.providers import can_resolve
from ..models import Cancelled, LinkRecord, Track, check_cancelled
from . import collection

DIRECT_STORE_CATEGORIES = frozenset(
    {"beatport", "bandcamp", "traxsource", "junodownload", "apple", "shop", "streaming"}
)


def find_gate_url(records: list[LinkRecord]) -> str | None:
    candidates = [
        record
        for record in records
        if record.link_url
        and "soundcloud.com" not in record.link_url
        and record.link_text != links_module.NO_STORE_LINK
    ]
    for record in candidates:
        if record.category == "gate":
            return record.link_url
    for record in candidates:
        if can_resolve(record.link_url):
            return record.link_url
    for record in candidates:
        if record.category not in DIRECT_STORE_CATEGORIES:
            return record.link_url
    return None


class MetadataStore(Protocol):
    def merge_track_metadata(self, source: str, generation: str, updates: dict) -> bool: ...


class FileState(Protocol):
    def set_local_file(self, key: str, path: str | Path) -> None: ...


@dataclass(frozen=True)
class FileResult:
    key: str
    path: Path
    recorded: bool


class PublishedFileUnrecorded(RuntimeError):
    """The transfer is complete; retry only recording, never the transfer."""

    def __init__(self, result: FileResult):
        self.result = result
        super().__init__(f"File saved to {result.path}; library update failed")


class DownloadService:
    def __init__(self, state: FileState, metadata: MetadataStore | None = None):
        self.state = state
        self.metadata = metadata

    def record(self, key: str, path: str | Path) -> FileResult:
        # Once publication has succeeded, cancellation cannot undo its effect.
        path = Path(path)
        try:
            self.state.set_local_file(key, path)
        except Exception:
            raise PublishedFileUnrecorded(FileResult(key, path, False)) from None
        return FileResult(key, path, True)

    def fetch(self, client, track, directory, **options) -> Path:
        path = client.download_track(track, directory, **options)
        self.record(track.key, path)
        return path

    def copy(self, key, source, directory, cancel=None) -> Path:
        path = copy_local_file(source, directory, cancel)
        self.record(key, path)
        return path

    def finish_gate(self, track, url, directory, cancel, *, config, status=None):
        from ..gates.browser import download_hypeddit_in_browser

        path = download_hypeddit_in_browser(
            track,
            url,
            directory,
            cancel,
            social=config.gate_social_actions,
            config=config,
            status=status,
        )
        self.record(track.key, path)
        return path

    def finish_gates(self, items, directory, cancel, *, config, status=None):
        from ..gates.browser import download_hypeddit_batch_in_browser

        result = download_hypeddit_batch_in_browser(
            items,
            directory,
            cancel,
            social=config.gate_social_actions,
            config=config,
            status=status,
        )
        completed, failures = [], list(result.failures)
        # Settle every published file even if one library write fails.
        for key, path in result.completed:
            try:
                completed.append(self.record(key, path))
            except PublishedFileUnrecorded as exc:
                failures.append((key, exc))
        return FileBatchResult(tuple(completed), tuple(failures), result.cancelled)


@dataclass(frozen=True)
class FileBatchResult:
    completed: tuple[FileResult, ...] = ()
    failures: tuple[tuple[str, Exception], ...] = ()
    cancelled: bool = False


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


def downloadable(track: Track, gate_url: str | None) -> bool:
    """Whether there is anything to fetch: a free download, a direct file or a gate."""

    return bool(track.free_download or gate_url or track.has_direct_download)


def _gate_failure_group(error: Exception) -> str:
    return next((name for name, types in FAILURE_GROUPS if isinstance(error, types)), "other")


@dataclass
class _BatchProgress:
    """What one batch pass has produced so far, shared between its two stages."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    hubs_changed: bool = False
    profile_items: list[tuple[Track, str | None]] = field(default_factory=list)
    auth_items: list[tuple[Track, str | None]] = field(default_factory=list)
    browser_items: list[tuple[Track, str]] = field(default_factory=list)
    # Why the HTTP flow gave up on each browser item, keyed by track: the
    # browser's own failure is only half the story without it.
    browser_reasons: dict[str, str] = field(default_factory=dict)
    deferred: int = 0
    failure_groups: Counter = field(default_factory=Counter)

    def record(
        self,
        track: Track,
        gate_url: str | None,
        outcome: str,
        result,
        changed: bool,
        *,
        retry_prerequisites: bool,
    ) -> str:
        """Sort one finished pool item into the bag.

        Returns what its track should show now: "downloaded", "failed", or
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
            self.profile_items.append((track, gate_url))
        elif retry_prerequisites and isinstance(result, soundcloud.SoundCloudLoginRequired):
            self.auth_items.append((track, gate_url))
        elif isinstance(result, gate_models.BROWSER_REQUIRED_ERRORS) and _is_hypeddit(gate_url):
            if len(self.browser_items) < BROWSER_BATCH_MAX:
                self.browser_items.append((track, gate_url))
                self.browser_reasons[track.key] = str(result)
            else:
                self.deferred += 1
        else:
            self.failed += 1
            if isinstance(result, Exception):
                self.failure_groups[_gate_failure_group(result)] += 1
            return "failed"
        return "waiting"


@dataclass(frozen=True)
class DownloadRequest:
    source: str
    generation: str
    directory: Path
    timeout: float


@dataclass(frozen=True)
class DownloadSummary:
    completed: int
    failed: int
    total: int
    pending: int
    failure_groups: dict[str, int]


@dataclass(frozen=True)
class DownloadEvent:
    operation_id: str
    kind: Literal[
        "progress",
        "downloaded",
        "failed",
        "waiting",
        "metadata",
        "hubs",
        "status",
        "browser_started",
        "browser_finished",
        "summary",
        "deferred",
    ]
    key: str = ""
    path: Path | None = None
    message: str = ""
    label: str = ""
    progress: float = 0.0
    count: int = 0
    fields: dict = field(default_factory=dict)
    summary: DownloadSummary | None = None


class DownloadWorkflow:
    """One admitted operation, retaining the existing HTTP pool/browser sequence.

    Inputs are private track copies. Events contain keys and an operation ID;
    effects are committed before delivery. The prerequisite callback waits for
    typed user answers and returns only approved pending items.
    """

    def __init__(self, service, request, handle, *, client, config, emit, prerequisites):
        self.service = service
        self.request = request
        self.handle = handle
        self.client = client
        self.config = config
        self.emit = emit
        self.prerequisites = prerequisites

    def send(self, kind, **values):
        if "message" in values:
            values["message"] = log_safe_text(values["message"])
        self.emit(DownloadEvent(self.handle.id, kind, **values))

    def normalise(self, track, url):
        if not self.request.source or not _is_hypeddit(url):
            return url, False
        changed = bool(
            collection.expand_link_hubs(
                [track], timeout=self.request.timeout, cancel=self.handle.cancel
            )
        )
        if changed:
            fields = {
                name: deepcopy(getattr(track, name))
                for name in (
                    "purchase_url",
                    "purchase_title",
                    "extra_links",
                    "description",
                )
            }
            self.service.metadata.merge_track_metadata(
                self.request.source, self.request.generation, {track.key: fields}
            )
            self.send("metadata", key=track.key, fields=fields)
        return find_gate_url(links_module.categorise(track)), changed

    def fetch(self, track, url):
        def on_progress(downloaded, total):
            pct = min(1.0, downloaded / total) if total and total > 0 else 0.5
            self.send("progress", key=track.key, progress=pct)

        self.send("progress", key=track.key)
        return self.service.fetch(
            self.client(),
            track,
            self.request.directory,
            gate_url=url,
            on_progress=on_progress,
            cancel=self.handle.cancel,
        )

    def attempt(self, item):
        track, url = item
        changed = False
        try:
            check_cancelled(self.handle.cancel)
            url, changed = self.normalise(track, url)
            if not downloadable(track, url):
                return track, url, "hub", None, changed
            return track, url, "downloaded", self.fetch(track, url), changed
        except Cancelled:
            return track, url, "cancelled", None, changed
        except Exception as exc:
            return track, url, "failed", exc, changed

    def run_one(self, track, url, allow_retry=True):
        track, url, outcome, result, changed = self.attempt((track, url))
        if changed:
            self.send("hubs")
        if outcome == "hub":
            self.send(
                "failed",
                key=track.key,
                message="Hypeddit link is a store hub rather than a download gate",
            )
            return
        if (
            outcome == "failed"
            and isinstance(result, gate_models.BROWSER_REQUIRED_ERRORS)
            and _is_hypeddit(url)
        ):
            self.send("status", message=f"Finishing in the hidden browser: {result}")
            try:
                result = self.service.finish_gate(
                    track,
                    url,
                    self.request.directory,
                    self.handle.cancel,
                    config=self.config,
                    status=lambda message: self.send("status", message=message),
                )
                outcome = "downloaded"
            except Cancelled:
                outcome = "cancelled"
            except PublishedFileUnrecorded as exc:
                result = exc
            except Exception as exc:
                result = RuntimeError(f"{exc} (after: {result})")
        if outcome == "downloaded":
            self.send("downloaded", key=track.key, path=Path(result))
        elif outcome == "cancelled":
            self.send("waiting", key=track.key)
        elif allow_retry and isinstance(
            result, (gate_models.GateProfileRequired, soundcloud.SoundCloudLoginRequired)
        ):
            self.send("waiting", key=track.key)
            item = (track, url)
            ready = self.prerequisites(
                [item] if isinstance(result, gate_models.GateProfileRequired) else [],
                [item] if isinstance(result, soundcloud.SoundCloudLoginRequired) else [],
            )
            if ready:
                self.run_one(*ready[0], allow_retry=False)
        else:
            self.send("failed", key=track.key, message=str(result))

    def run_batch(self, items, allow_retry=True):
        items = [item for item in items if downloadable(*item)]
        progress = _BatchProgress(total=len(items))
        # Exiting the pool waits for actual thread completion, including cancel.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self.attempt, item) for item in items]
            for future in as_completed(futures):
                track, url, outcome, result, changed = future.result()
                verdict = progress.record(
                    track, url, outcome, result, changed, retry_prerequisites=allow_retry
                )
                if verdict == "downloaded":
                    self.send("downloaded", key=track.key, path=Path(result))
                elif verdict == "failed":
                    self.send("failed", key=track.key, message=str(result), label=track.label)
                else:
                    self.send("waiting", key=track.key)
        if progress.hubs_changed:
            self.send("hubs")
        if progress.browser_items:
            self.browser_pass(progress)
        if progress.deferred:
            self.send("deferred", count=progress.deferred)
        pending = len(progress.profile_items) + len(progress.auth_items)
        self.send(
            "summary",
            summary=DownloadSummary(
                progress.completed,
                progress.failed,
                progress.total,
                pending,
                dict(progress.failure_groups),
            ),
        )
        if pending:
            ready = self.prerequisites(progress.profile_items, progress.auth_items)
            if ready:
                self.run_batch(ready, False)

    def browser_pass(self, progress):
        tracks = {track.key: track for track, _url in progress.browser_items}
        self.send("browser_started", count=len(tracks))
        try:
            result = self.service.finish_gates(
                progress.browser_items,
                self.request.directory,
                self.handle.cancel,
                config=self.config,
                status=lambda message: self.send("status", message=message),
            )
        except Exception as exc:
            result = FileBatchResult(
                failures=tuple((key, gate_models.GateUnavailable(str(exc))) for key in tracks)
            )
        finally:
            self.send("browser_finished")
        for complete in result.completed:
            progress.completed += 1
            self.send("downloaded", key=complete.key, path=complete.path)
        for key, exc in result.failures:
            progress.failed += 1
            progress.failure_groups[_gate_failure_group(exc)] += 1
            reason = progress.browser_reasons.get(key)
            message = f"{exc} (after: {reason})" if reason else str(exc)
            self.send("failed", key=key, message=message, label=tracks[key].label)

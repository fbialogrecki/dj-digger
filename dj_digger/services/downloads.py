"""Persist completed file effects independently of the lifetime of the view."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..files import copy_local_file


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
        super().__init__(f'File saved to {result.path}; library update failed')


class DownloadService:
    def __init__(self, state: FileState):
        self.state = state

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
            track, url, directory, cancel, social=config.gate_social_actions,
            config=config, status=status,
        )
        self.record(track.key, path)
        return path

    def finish_gates(self, items, directory, cancel, *, config, status=None):
        from ..gates.browser import download_hypeddit_batch_in_browser
        result = download_hypeddit_batch_in_browser(
            items, directory, cancel, social=config.gate_social_actions,
            config=config, status=status,
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

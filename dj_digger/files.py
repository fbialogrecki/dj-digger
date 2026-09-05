"""Validated file publication shared by HTTP, Chromium and local copies."""

import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .gates import providers as gates
from .models import Cancelled, Track, check_cancelled
from .paths import unique_target
from .soundcloud_errors import SoundCloudError

LOGGER = logging.getLogger(__name__)

# Windows refuses these as a filename whatever the extension - CON.mp3 is as
# reserved as CON - and says so with an OSError at the moment of writing, which
# is after the whole file has already been fetched. A track called "Aux" is not
# a hypothetical.
WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
# A ceiling on what one track may write to disk. A gate hands over whatever it
# likes and Content-Length is only a claim, so without this the write loop ends
# when the server decides it does. Two gigabytes clears any real DJ file - a
# 10-minute WAV at 24/96 is under 400 MB - by a wide margin.
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
# Concurrent downloads in the TUI pool race between target.exists() and
# os.replace when two tracks sanitise to the same stem; the lock makes
# pick-a-unique-name-and-rename one atomic step.
_DOWNLOAD_NAME_LOCK = threading.Lock()


class _WebPageNotFile(SoundCloudError):
    """The download answered with a page - a gate that was not satisfied.

    Without this that page was saved as a perfectly ordinary .mp3, and the
    first sign of trouble was a player refusing to open a track you thought
    you owned. Its own type so a gate-derived download can recolour it as a
    protocol failure rather than a transfer one.
    """

    def __init__(self) -> None:
        super().__init__("That link returned a web page rather than an audio file")


def _download_stem(track: Track) -> str:
    """The file name a track is saved under, safe on every filesystem."""

    raw = " - ".join(part for part in (track.artist, track.title) if part).strip()
    raw = re.sub(r"[^\w .-]+", "", raw, flags=re.UNICODE).strip(" .")
    raw = re.sub(r"\s+", " ", raw)
    stem = (raw or f"track-{track.id or 'soundcloud'}")[:180].strip(" .")
    return f"_{stem}" if stem.upper() in WINDOWS_RESERVED else stem


def _looks_like_html(prefix: bytes) -> bool:
    sample = prefix.lstrip().lower()
    return sample.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _extension_for(content_disp: str, content_type: str) -> str:
    """Pick a file suffix from the response headers; .mp3 when nothing matches."""

    cd_lower = content_disp.lower()
    ct_lower = content_type.lower()
    for candidate, disp_needles, type_needles in (
        (".wav", (".wav",), ("wav",)),
        (".flac", (".flac",), ("flac",)),
        (".aiff", (".aiff", ".aif"), ("aiff", "aif")),
        (".zip", (".zip",), ("zip",)),
    ):
        if any(n in cd_lower for n in disp_needles) or any(
            n in ct_lower for n in type_needles
        ):
            return candidate
    return ".mp3"


def _stream_to_file(
    response: Any,
    temporary: Path,
    on_progress: Callable[[int, int | None], None] | None,
    total_size: int | None,
    cancel: threading.Event | None = None,
) -> None:
    """Stream the body to the temp file, stopping at the size ceiling."""

    downloaded = 0
    with temporary.open("wb") as handle:
        try:
            # 128 KB: disk writes want fewer, larger chunks than the 64 KB the
            # player streams with, where latency to first audio matters instead.
            for chunk in response.iter_content(chunk_size=1024 * 128):
                check_cancelled(cancel)
                if chunk:
                    handle.write(chunk)
                    downloaded += len(chunk)
                    # Content-Length is a claim, and a gate that never
                    # stops sending would otherwise fill the disk: the
                    # loop above had no end but the server's goodwill.
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise SoundCloudError(
                            "Download exceeded "
                            f"{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB - stopped"
                        )
                    if on_progress:
                        on_progress(downloaded, total_size)
        except Cancelled:
            raise
        except Exception as stream_exc:
            raise SoundCloudError(f"Download stream read failed: {stream_exc}") from stream_exc


def _part_file(directory: Path, prefix: str) -> Path:
    """An empty, owner-only temp file in the download directory itself.

    Same filesystem as the final name, so the rename at the end is atomic.
    """

    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=prefix, suffix=".part", dir=directory)
    os.close(descriptor)
    return Path(temporary)


def _claim_target(directory: Path, track: Track, suffix: str, temporary: Path, cancel=None) -> Path:
    """Move a finished temp file to a unique final name under the shared lock."""

    return publish(temporary, directory, _download_stem(track), suffix, cancel)


def publish(temporary: Path, directory: Path, stem: str, suffix: str, cancel=None) -> Path:
    with _DOWNLOAD_NAME_LOCK:
        check_cancelled(cancel)
        target = unique_target(directory, stem, suffix)
        os.replace(temporary, target)
    return target


def _save_stream(
    response: Any,
    track: Track,
    directory: Path,
    suffix: str,
    on_progress: Callable[[int, int | None], None] | None,
    total_size: int | None,
    cancel: threading.Event | None,
) -> Path:
    """Stream a response into the directory and keep it under the track's name."""

    temporary = _part_file(directory, ".dj-digger-")
    try:
        _stream_to_file(response, temporary, on_progress, total_size, cancel)
        check_cancelled(cancel)
    except BaseException:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
    return _keep_download(temporary, track, directory, suffix, cancel)


def _keep_download(temporary: Path, track: Track, directory: Path, suffix: str, cancel=None) -> Path:
    """Check a finished temp file and move it to its final name; remove it otherwise."""

    try:
        if temporary.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise SoundCloudError(
                f"Download exceeded {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB"
            )
        with temporary.open("rb") as stream:
            prefix = stream.read(512)
        if _looks_like_html(prefix):
            raise _WebPageNotFile()
        return _claim_target(directory, track, suffix, temporary, cancel)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def save_browser_download(download: Any, track: Track, directory: Path, cancel=None) -> Path:
    """Validate and atomically keep a Playwright download."""

    directory = Path(directory)
    suggested = Path(str(getattr(download, "suggested_filename", "") or ""))
    suffix = suggested.suffix.lower()
    if suffix not in gates.DOWNLOAD_SUFFIXES:
        suffix = ".mp3"
    temporary = _part_file(directory, ".dj-digger-browser-")
    try:
        download.save_as(str(temporary))
    except BaseException:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise
    return _keep_download(temporary, track, directory, suffix, cancel)



def copy_local_file(source: Path, directory: Path, cancel=None) -> Path:
    source = source.resolve(strict=True)
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    if source.is_relative_to(directory):
        return source
    temporary = _part_file(directory, ".dj-digger-copy-")
    try:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            total = 0
            while chunk := incoming.read(128 * 1024):
                check_cancelled(cancel)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SoundCloudError("Local file exceeds download size limit")
                if total == len(chunk) and _looks_like_html(chunk[:512]):
                    raise _WebPageNotFile()
                outgoing.write(chunk)
        shutil.copystat(source, temporary)
        return publish(temporary, directory, source.stem, source.suffix, cancel)
    finally:
        temporary.unlink(missing_ok=True)

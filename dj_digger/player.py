"""Previewing a track before you buy it.

SoundCloud offers a ``progressive`` transcoding next to HLS, which is a plain
MP3 behind a signed URL. Measured: resolving takes about 0.3 s and a 7 minute
track downloads in about 1.5 s at 6.6 MB. The file lands in a per-session
temporary directory rather than a cache, because a persistent cache of whole
tracks reaches gigabytes after an evening of digging and would then need an
eviction policy nobody asked for.

Playback is miniaudio driving a local file, which makes seeking instant (0.14 s
to jump 200 s in, measured). ``just-playback`` was the first choice but has no
wheel for Python 3.14 and fails to build without system headers.

Everything degrades: a machine with no audio sink refuses to open a device, and
that must never take the app down.
"""

from __future__ import annotations

import array
import logging
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from rich.text import Text
from textual.widgets import Static

from .models import Track
from .soundcloud import SoundCloudClient, SoundCloudError

LOGGER = logging.getLogger(__name__)

SEEK_STEP = 10.0
VOLUME_STEP = 0.1
# Rendered from the 1800 samples SoundCloud publishes per track.
BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


class PlaybackUnavailable(RuntimeError):
    """No audio output, or miniaudio is missing."""


def _import_miniaudio():
    try:
        import miniaudio
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise PlaybackUnavailable(
            "Audio preview needs miniaudio: pip install 'dj-soundcloud-digger[play]'"
        ) from exc
    return miniaudio


def unplayable_reason(payload: dict) -> Optional[str]:
    """Why this track cannot be previewed in full, if so."""

    if payload.get("policy") == "SNIP":
        return "SoundCloud only offers a 30 second snippet of this one"
    if payload.get("streamable") is False:
        return "This track is not streamable"
    transcodings = (payload.get("media") or {}).get("transcodings") or []
    if not any(
        (item.get("format") or {}).get("protocol") == "progressive" for item in transcodings
    ):
        return "No plain MP3 stream offered for this track"
    return None


def resolve_stream(client: SoundCloudClient, track_id: int) -> Tuple[str, str]:
    """Signed MP3 URL plus the waveform URL, from one refetch.

    The payload is fetched fresh every time because ``track_authorization`` and
    the signature on the returned URL both expire.
    """

    payload = client.fetch_track(track_id)
    reason = unplayable_reason(payload)
    if reason:
        raise SoundCloudError(reason)

    progressive = next(
        item
        for item in payload["media"]["transcodings"]
        if (item.get("format") or {}).get("protocol") == "progressive"
    )
    authorized = client.authorize(
        progressive["url"], track_authorization=payload.get("track_authorization")
    )
    url = authorized.get("url")
    if not url:
        raise SoundCloudError("SoundCloud did not hand back a stream URL")
    return url, payload.get("waveform_url") or ""


@lru_cache(maxsize=256)
def _cached_waveform(client: SoundCloudClient, waveform_url: str) -> tuple:
    """Kept in memory for the session - 7 KB from a CDN is not worth a cache file."""

    try:
        payload = client.session.get(waveform_url, timeout=15).json()
    except Exception as exc:  # a missing waveform must not stop playback
        LOGGER.debug("Could not read waveform %s: %s", waveform_url, exc)
        return ()
    samples = payload.get("samples")
    return tuple(int(value) for value in samples) if isinstance(samples, list) else ()


def fetch_waveform(client: SoundCloudClient, waveform_url: str) -> List[int]:
    if not waveform_url:
        return []
    return list(_cached_waveform(client, waveform_url))


def render_waveform(samples: List[int], width: int, played_fraction: float = 0.0) -> Text:
    """Squash the samples to the given width and draw them as block characters."""

    text = Text()
    if width <= 0:
        return text
    if not samples:
        text.append("\u2500" * width, style="bright_black")
        return text

    peak = max(samples) or 1
    per_column = len(samples) / width
    played_columns = int(width * max(0.0, min(1.0, played_fraction)))
    for column in range(width):
        start = int(column * per_column)
        end = max(start + 1, int((column + 1) * per_column))
        level = max(samples[start:end]) / peak
        glyph = BLOCKS[min(len(BLOCKS) - 1, int(level * (len(BLOCKS) - 1) + 0.5))]
        text.append(glyph, style="cyan" if column < played_columns else "bright_black")
    return text


@dataclass
class Loaded:
    track: Track
    path: Path
    duration: float
    waveform: List[int] = field(default_factory=list)


class Player:
    """Play, pause, seek and volume over a locally downloaded MP3."""

    def __init__(self) -> None:
        self._miniaudio = None
        self._device = None
        self._info = None
        self._loaded: Optional[Loaded] = None
        self._frames = 0
        self._offset = 0.0
        self._playing = False
        self._volume = 0.8
        self._muted = False
        self._tempdir: Optional[tempfile.TemporaryDirectory] = None
        self.unavailable_reason: Optional[str] = None

    # Setup

    @property
    def tempdir(self) -> Path:
        if self._tempdir is None:
            self._tempdir = tempfile.TemporaryDirectory(prefix="dj-digger-")
        return Path(self._tempdir.name)

    def _device_for(self, sample_rate: int, channels: int):
        if self.unavailable_reason:
            # Already established there is no output; stop hammering the backend.
            raise PlaybackUnavailable(self.unavailable_reason)
        miniaudio = self._miniaudio or _import_miniaudio()
        self._miniaudio = miniaudio
        if self._device is not None:
            return self._device
        try:
            self._device = miniaudio.PlaybackDevice(
                sample_rate=sample_rate, nchannels=channels
            )
        except Exception as exc:
            # The raw miniaudio error is a numbered tuple, no use to anyone here.
            LOGGER.debug("Could not open an audio device: %s", exc)
            self.unavailable_reason = "No audio output on this machine or session"
            raise PlaybackUnavailable(self.unavailable_reason) from exc
        return self._device

    # State

    @property
    def loaded(self) -> Optional[Loaded]:
        return self._loaded

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def duration(self) -> float:
        return self._loaded.duration if self._loaded else 0.0

    @property
    def position(self) -> float:
        if self._info is None:
            return 0.0
        return min(self.duration, self._offset + self._frames / self._info.sample_rate)

    @property
    def fraction(self) -> float:
        return self.position / self.duration if self.duration else 0.0

    @property
    def volume(self) -> float:
        return 0.0 if self._muted else self._volume

    # Controls

    def load(self, track: Track, path: Path, waveform: Optional[List[int]] = None) -> Loaded:
        miniaudio = self._miniaudio or _import_miniaudio()
        self._miniaudio = miniaudio
        self.stop()
        self._info = miniaudio.get_file_info(str(path))
        self._loaded = Loaded(
            track=track, path=path, duration=self._info.duration, waveform=waveform or []
        )
        self._frames = 0
        self._offset = 0.0
        return self._loaded

    def _feed(self, seek_frame: int):
        miniaudio = self._miniaudio
        stream = miniaudio.stream_file(
            str(self._loaded.path),
            sample_rate=self._info.sample_rate,
            nchannels=self._info.nchannels,
            seek_frame=seek_frame,
        )
        chunk = next(stream)
        required = yield b""
        while True:
            if chunk is None:
                chunk = stream.send(required)
            if not len(chunk):
                self._playing = False
                return
            self._frames += len(chunk) // self._info.nchannels
            volume = self.volume
            out = (
                chunk
                if volume >= 0.999
                else array.array("h", [int(sample * volume) for sample in chunk])
            )
            chunk = None
            required = yield out

    def play(self) -> None:
        if self._loaded is None:
            return
        device = self._device_for(self._info.sample_rate, self._info.nchannels)
        device.start(self._feed(int(self.position * self._info.sample_rate)))
        self._offset = self.position
        self._frames = 0
        self._playing = True

    def pause(self) -> None:
        if self._device is not None and self._playing:
            self._offset = self.position
            self._frames = 0
            self._device.stop()
        self._playing = False

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    def stop(self) -> None:
        if self._device is not None:
            self._device.stop()
        self._playing = False
        self._frames = 0
        self._offset = 0.0

    def seek(self, seconds: float) -> None:
        if self._loaded is None:
            return
        target = max(0.0, min(self.duration - 0.5, seconds))
        was_playing = self._playing
        if self._device is not None:
            self._device.stop()
        self._offset = target
        self._frames = 0
        self._playing = False
        if was_playing:
            self.play()

    def nudge(self, seconds: float) -> None:
        self.seek(self.position + seconds)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        self._muted = False

    def change_volume(self, delta: float) -> None:
        self.set_volume(self._volume + delta)

    def toggle_mute(self) -> None:
        self._muted = not self._muted

    def close(self) -> None:
        try:
            self.stop()
            if self._device is not None:
                self._device.close()
        except Exception as exc:
            LOGGER.debug("Closing the audio device complained: %s", exc)
        self._device = None
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None


def download_stream(client: SoundCloudClient, url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = client.session.get(url, timeout=60)
    if response.status_code >= 400:
        raise SoundCloudError(f"Stream download failed with HTTP {response.status_code}")
    dest.write_bytes(response.content)
    return dest


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PlayerBar(Static):
    """Title, clock, volume and a clickable waveform."""

    DEFAULT_CSS = """
    PlayerBar {
        height: 2;
        padding: 0 1;
        display: none;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    PlayerBar.active {
        display: block;
    }
    """

    def __init__(self, player: Player, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player = player
        self.message = ""

    def refresh_bar(self) -> None:
        self.set_class(self.player.loaded is not None or bool(self.message), "active")
        self.update(self._content())

    def _content(self) -> Text:
        loaded = self.player.loaded
        if loaded is None:
            return Text(self.message, style="bright_black")

        head = Text()
        head.append("\u25b6 " if self.player.playing else "\u23f8 ", style="bold cyan")
        head.append(loaded.track.label)
        head.append(
            f"  {format_time(self.player.position)} / {format_time(self.player.duration)}",
            style="bright_black",
        )
        head.append(f"  vol {int(self.player.volume * 100)}%", style="bright_black")
        if self.message:
            head.append(f"  {self.message}", style="yellow")
        head.append("\n")
        head.append_text(
            render_waveform(loaded.waveform, self._bar_width(), self.player.fraction)
        )
        return head

    def _bar_width(self) -> int:
        return max(1, self.size.width - 2)

    def seconds_at(self, x: int) -> float:
        """Turn a click position into a time, for seeking on the waveform."""

        width = self._bar_width()
        fraction = min(1.0, max(0.0, (x - 1) / width)) if width else 0.0
        return fraction * self.player.duration

    def on_click(self, event) -> None:
        # Only the waveform row seeks; the text row above it is not a scrubber.
        if self.player.loaded is None or event.y != 1:
            return
        event.stop()
        try:
            self.player.seek(self.seconds_at(event.x))
        except PlaybackUnavailable as exc:
            self.message = str(exc)
        self.refresh_bar()

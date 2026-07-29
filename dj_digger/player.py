"""Previewing a track before you buy it.

SoundCloud offers a ``progressive`` transcoding next to HLS, which is a plain
MP3 behind a signed URL. Nothing is downloaded to disk: the MP3 is decoded
straight off the socket through miniaudio's ``stream_any``, so audio starts after
about 0.5 s instead of waiting out a 6.6 MB download. Seeking re-opens the stream
with an HTTP Range header, which CloudFront serves, and costs the same 0.5 s.

``just-playback`` was the first choice but has no wheel for Python 3.14 and fails
to build without system headers, so this drives miniaudio directly.

Everything degrades: a machine with no audio sink refuses to open a device, and
that must never take the app down.
"""

from __future__ import annotations

import array
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional

from rich.text import Text
from textual.widgets import Static

from .models import Track
from .soundcloud import SoundCloudClient, SoundCloudError

LOGGER = logging.getLogger(__name__)

SEEK_STEP = 10.0
VOLUME_STEP = 0.1
SAMPLE_RATE = 44100
CHANNELS = 2

# Two rows of bottom-anchored blocks give 16 levels instead of 8, which is what
# stops a loud master from rendering as a solid rectangle.
BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
WAVEFORM_ROWS = 2
# Loud tracks sit in the top tenth of the range, so the curve has to expand it.
WAVEFORM_GAMMA = 3.0


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


@dataclass
class Stream:
    url: str
    waveform_url: str = ""
    duration: float = 0.0


def resolve_stream(client: SoundCloudClient, track_id: int) -> Stream:
    """Signed MP3 URL, waveform URL and duration, from one refetch.

    The payload is fetched fresh every time because ``track_authorization`` and
    the signature on the returned URL both expire. Duration comes from here too,
    since nothing is written to disk to measure.
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
    milliseconds = payload.get("full_duration") or payload.get("duration") or 0
    return Stream(
        url=url,
        waveform_url=payload.get("waveform_url") or "",
        duration=float(milliseconds) / 1000.0,
    )


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


def column_levels(samples: List[int], width: int) -> List[float]:
    """One 0..1 level per column, with the loud end of the range expanded.

    Two deliberate choices. Columns average their samples rather than taking the
    peak: at roughly sixteen samples per column the peak almost always hits the
    ceiling, which is most of why this looked like a brick. And the level is
    measured against the track's own maximum rather than stretched between its
    min and max - stretching made a track with no dynamics at all look the most
    dynamic of the lot, because it amplified its noise to full scale. The power
    curve then spreads the top of the range, which is where mastered music sits.
    """

    if width <= 0 or not samples:
        return []

    peak = max(samples)
    if peak <= 0:
        return [0.0] * width

    per_column = len(samples) / width
    levels = []
    for column in range(width):
        start = int(column * per_column)
        end = max(start + 1, int((column + 1) * per_column))
        window = samples[start:end]
        levels.append((sum(window) / len(window) / peak) ** WAVEFORM_GAMMA)
    return levels


def render_waveform(
    samples: List[int], width: int, played_fraction: float = 0.0, rows: int = WAVEFORM_ROWS
) -> Text:
    """Draw the samples as a stack of block rows, bottom row filling first."""

    text = Text()
    if width <= 0:
        return text
    if not samples:
        for row in range(rows):
            text.append("\u2500" * width, style="bright_black")
            if row < rows - 1:
                text.append("\n")
        return text

    levels = column_levels(samples, width)
    played_columns = int(width * max(0.0, min(1.0, played_fraction)))
    steps = len(BLOCKS) - 1
    for row in range(rows):
        # Row 0 is the top of the bar, so it draws the highest slice of the level.
        slice_index = rows - 1 - row
        for column, level in enumerate(levels):
            eighths = level * steps * rows - slice_index * steps
            glyph = BLOCKS[max(0, min(steps, int(eighths + 0.5)))]
            text.append(glyph, style="cyan" if column < played_columns else "bright_black")
        if row < rows - 1:
            text.append("\n")
    return text


class HttpSourceMixin:
    """Feeds miniaudio from an HTTP response, seeking with Range requests.

    The logic lives in a mixin because miniaudio's ``StreamableSource`` base is
    only importable when miniaudio is, and the digger has to work without it.
    """

    def __init__(self, session, url: str, timeout: float = 30.0) -> None:
        self.session = session
        self.url = url
        self.timeout = timeout
        self.offset = 0
        self.length: Optional[int] = None
        self._response = None
        self._open(0)

    def _open(self, offset: int) -> None:
        self.close()
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        self._response = self.session.get(
            self.url, headers=headers, stream=True, timeout=self.timeout
        )
        if self.length is None:
            declared = self._response.headers.get("Content-Length")
            self.length = int(declared) + offset if declared else None
        self.offset = offset

    def read(self, num_bytes: int) -> bytes:
        # raw.read can come up short on a socket; the decoder reads a short
        # answer as the end of the file, so keep pulling until it is satisfied.
        parts = []
        remaining = num_bytes
        while remaining > 0:
            try:
                chunk = self._response.raw.read(remaining)
            except Exception as exc:
                LOGGER.debug("Stream read failed: %s", exc)
                break
            if not chunk:
                break
            parts.append(chunk)
            remaining -= len(chunk)
        data = b"".join(parts)
        self.offset += len(data)
        return data

    def seek(self, offset: int, origin) -> bool:
        target = offset
        if getattr(origin, "value", origin) == 1:  # SeekOrigin.CURRENT
            target = self.offset + offset
        elif getattr(origin, "value", origin) == 2 and self.length:  # END
            target = self.length + offset
        try:
            self._open(max(0, target))
        except Exception as exc:
            LOGGER.debug("Range seek failed: %s", exc)
            return False
        return True

    def close(self) -> None:
        if self._response is not None:
            self._response.close()
            self._response = None


_http_source_type = None


def http_source_type(miniaudio):
    """The mixin welded onto miniaudio's StreamableSource, built once."""

    global _http_source_type
    if _http_source_type is None:
        _http_source_type = type(
            "HttpSource", (HttpSourceMixin, miniaudio.StreamableSource), {}
        )
    return _http_source_type


@dataclass
class Loaded:
    track: Track
    stream: Stream
    duration: float
    waveform: List[int] = field(default_factory=list)


class Player:
    """Play, pause, seek and volume over an MP3 streamed straight from SoundCloud."""

    def __init__(self) -> None:
        self._miniaudio = None
        self._device = None
        self._loaded: Optional[Loaded] = None
        self._session = None
        self._source = None
        self._generator = None
        self._frames = 0
        self._offset = 0.0
        self._playing = False
        self._volume = 0.8
        self._muted = False
        self.unavailable_reason: Optional[str] = None

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
        if self._loaded is None:
            return 0.0
        return min(self.duration, self._offset + self._frames / SAMPLE_RATE)

    @property
    def fraction(self) -> float:
        return self.position / self.duration if self.duration else 0.0

    @property
    def volume(self) -> float:
        return 0.0 if self._muted else self._volume

    # Controls

    def load(
        self,
        track: Track,
        stream: Stream,
        session,
        waveform: Optional[List[int]] = None,
    ) -> Loaded:
        self._miniaudio = self._miniaudio or _import_miniaudio()
        self.stop()
        self._session = session
        self._loaded = Loaded(
            track=track,
            stream=stream,
            duration=stream.duration,
            waveform=waveform or [],
        )
        self._frames = 0
        self._offset = 0.0
        return self._loaded

    def _open_stream(self, seek_frame: int):
        miniaudio = self._miniaudio
        self._close_source()
        self._source = http_source_type(miniaudio)(self._session, self._loaded.stream.url)
        return miniaudio.stream_any(
            self._source,
            source_format=miniaudio.FileFormat.MP3,
            sample_rate=SAMPLE_RATE,
            nchannels=CHANNELS,
            seek_frame=seek_frame,
        )

    def _close_source(self) -> None:
        if self._source is not None:
            self._source.close()
            self._source = None
        self._generator = None

    def _feed(self, stream):
        chunk = next(stream)
        required = yield b""
        while True:
            if chunk is None:
                chunk = stream.send(required)
            if not len(chunk):
                self._playing = False
                return
            self._frames += len(chunk) // CHANNELS
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
        device = self._device_for(SAMPLE_RATE, CHANNELS)
        if self._generator is None:
            # Reopening the socket costs about half a second, so a plain resume
            # keeps the existing generator and only a seek reopens it.
            self._offset = self.position
            self._frames = 0
            self._generator = self._feed(self._open_stream(int(self._offset * SAMPLE_RATE)))
        device.start(self._generator)
        self._playing = True

    def pause(self) -> None:
        if self._device is not None and self._playing:
            self._device.stop()
        self._playing = False

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    def stop(self) -> None:
        if self._device is not None:
            self._device.stop()
        self._close_source()
        self._playing = False
        self._frames = 0
        self._offset = 0.0

    def seek(self, seconds: float) -> None:
        if self._loaded is None:
            return
        target = max(0.0, min(max(0.0, self.duration - 0.5), seconds))
        was_playing = self._playing
        if self._device is not None:
            self._device.stop()
        # Drop the socket so the next play reopens it with a Range header.
        self._close_source()
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


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PlayerBar(Static):
    """Title, clock, volume and a clickable waveform."""

    DEFAULT_CSS = """
    PlayerBar {
        height: 3;
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
        # Only the waveform rows seek; the text row above them is not a scrubber.
        if self.player.loaded is None or event.y == 0:
            return
        event.stop()
        try:
            self.player.seek(self.seconds_at(event.x))
        except PlaybackUnavailable as exc:
            self.message = str(exc)
        self.refresh_bar()

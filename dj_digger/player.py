"""Previewing a track before you buy it.

SoundCloud offers a ``progressive`` transcoding next to HLS, which is a plain
MP3 behind a signed URL. Nothing is downloaded to disk: the MP3 is decoded
straight off the socket through miniaudio's ``stream_any``, so audio starts after
about 0.5 s instead of waiting out a 6.6 MB download.

A copy is kept in memory as it goes, though. Without one, seeking ten seconds
back into audio that just played meant a fresh connection and half a second of
silence, and nothing about the next track was known until the current one ended.
The copy fixes both: the decoder reads from a bytearray, and a seek is a move of
an index.

``just-playback`` was the first choice but has no wheel for Python 3.14 and fails
to build without system headers, so this drives miniaudio directly.

Everything degrades: a machine with no audio sink refuses to open a device, and
that must never take the app down.
"""

import array
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from queue import Empty, SimpleQueue
from typing import Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static

from .models import Track
from .soundcloud import SoundCloudClient, SoundCloudError

LOGGER = logging.getLogger(__name__)

SEEK_STEP = 10.0
VOLUME_STEP = 0.1
SAMPLE_RATE = 44100
CHANNELS = 2
# int16, so this is the loudest a sample can be.
FULL_SCALE = 32768.0
# One reading per frame of the interface. A callback hands over about a tenth of
# a second at a time, and the loudest sample in a tenth of a second of techno is
# a kick every single time - so a reading per callback is a meter that sits still.
LEVEL_WINDOW = SAMPLE_RATE * CHANNELS // 30
# A quarter of a second of readings. Past that the meter would be showing the
# past rather than falling behind gracefully, so the oldest go.
LEVEL_QUEUE = 8

DOWNLOAD_CHUNK = 64 * 1024
# A two hour set is not a track, and a response that will not declare its size
# could be anything. Both stream off the socket the way everything used to.
MAX_BUFFER_BYTES = 50 * 1024 * 1024
SOURCE_TIMEOUT = 30.0

# Rows of bottom-anchored blocks, eight levels each. Two of them gave 16 and
# stopped a loud master rendering as a solid rectangle; four give 32 and are
# what the bar spends the row the title used to have on.
BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
WAVEFORM_ROWS = 4
# Loud tracks sit in the top tenth of the range, so the curve has to expand it.
WAVEFORM_GAMMA = 3.0

PLAYED_STYLE = "cyan"
UNPLAYED_STYLE = "bright_black"
# How far back from the playhead the sound of this instant is allowed to show.
# Two columns: twelve was a band wide enough that its 30fps pulsing read as the
# whole tail of the played waveform flickering.
GLOW_COLUMNS = 2
# Steps within one hue, no white: a colour that changes on every frame reads as
# flicker rather than as a pulse, and white against cyan was the harshest jump
# of all. The first is the ordinary played colour, so a silent or paused track
# looks exactly as it did before any of this.
GLOW_STYLES = (PLAYED_STYLE, "bold cyan", "bold bright_cyan", "bold bright_cyan")


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


def unplayable_reason(payload: dict) -> str | None:
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


def fetch_waveform(client: SoundCloudClient, waveform_url: str) -> list[int]:
    if not waveform_url:
        return []
    return list(_cached_waveform(client, waveform_url))


def column_levels(samples: list[int], width: int) -> list[float]:
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


def glow_style(level: float) -> str:
    step = int(max(0.0, min(1.0, level)) * len(GLOW_STYLES))
    return GLOW_STYLES[min(step, len(GLOW_STYLES) - 1)]


class LevelMeter:
    """Turns raw peaks into something that reads as a pulse.

    Three things stop a peak from reading as movement, and each gets a fix.

    It jumps between readings, so a hit shows at once and is then made to fall
    away slowly - fast up, slow down, which is what makes a kick look like a
    kick. The decay is floored by whatever is arriving now, or a steady sound
    would chop itself into a two frame flicker.

    It is measured against a window that follows the loudest and the quietest of
    the last second or two rather than against full scale. Measured on real
    tracks, a brickwalled hard techno master lives between 0.92 and 1.00 from
    beginning to end: against full scale it would sit at maximum and never move,
    and against its own recent range it moves plenty.

    And when that window closes to nothing, nothing is happening - so it reads
    as dark, rather than as its own hiss stretched to full height.
    """

    def __init__(
        self,
        release: float = 0.72,
        adapt: float = 0.03,
        gamma: float = 1.6,
        quietest_span: float = 0.02,
    ) -> None:
        self.release = release
        self.adapt = adapt
        self.gamma = gamma
        self.quietest_span = quietest_span
        self.reset()

    def reset(self) -> None:
        self._value = 0.0
        self._floor = 1.0
        self._ceiling = 0.0

    def feed(self, peak: float) -> float:
        peak = max(0.0, min(1.0, peak))
        self._value = max(peak, self._value * self.release)

        # Both ends open instantly for anything outside the window and close in
        # on it slowly, so one stray transient does not black out the next
        # second and a breakdown is not still being measured against the drop.
        span = max(0.0, self._ceiling - self._floor)
        self._ceiling = max(peak, self._ceiling - span * self.adapt)
        self._floor = min(peak, self._floor + span * self.adapt)

        span = self._ceiling - self._floor
        if span < self.quietest_span:
            return 0.0
        return min(1.0, max(0.0, (self._value - self._floor) / span)) ** self.gamma


def waveform_rows(
    samples: list[int], width: int, rows: int = WAVEFORM_ROWS
) -> list[str]:
    """The block glyphs for a waveform, one string per row.

    These do not change while a track plays, so they are worth building once and
    keeping - only the colours move from frame to frame.
    """

    if width <= 0:
        return []
    if not samples:
        return ["\u2500" * width] * rows

    levels = column_levels(samples, width)
    steps = len(BLOCKS) - 1
    drawn = []
    for row in range(rows):
        # Row 0 is the top of the bar, so it draws the highest slice of the level.
        slice_index = rows - 1 - row
        drawn.append(
            "".join(
                BLOCKS[max(0, min(steps, int(level * steps * rows - slice_index * steps + 0.5)))]
                for level in levels
            )
        )
    return drawn


def paint_waveform(rows: list[str], played_fraction: float, level: float = 0.0) -> Text:
    """Colour prebuilt rows: what has played, what has not, and the leading edge.

    A frame costs a handful of style ranges rather than an append per character,
    which is what makes thirty of them a second cheaper than the four this
    managed when every glyph was styled on its own.
    """

    text = Text("\n".join(rows))
    if not rows:
        return text

    width = len(rows[0])
    played = int(width * max(0.0, min(1.0, played_fraction)))
    # The played region is history and flicker there only tires the eye, so the
    # pulse is confined to the columns just behind the playhead.
    glow_from = max(0, played - GLOW_COLUMNS)
    head = glow_style(level)
    for index in range(len(rows)):
        start = index * (width + 1)
        if glow_from:
            text.stylize(PLAYED_STYLE, start, start + glow_from)
        if played > glow_from:
            text.stylize(head, start + glow_from, start + played)
        text.stylize(UNPLAYED_STYLE, start + played, start + width)
    return text


class HttpSourceMixin:
    """Feeds miniaudio from an HTTP response, keeping a copy of it as it goes.

    A thread pulls the response into memory alongside playback, so the decoder
    reads out of a bytearray instead of a socket. Playback still starts on the
    first bytes to land - nothing waits for the download to finish - but by a few
    seconds in the whole track is here, and seeking into it costs nothing.

    A response too large to hold, or one that will not say how large it is, takes
    the unbuffered path instead: reads go straight to the socket and a seek
    reopens it with a Range header, which is what everything used to do.

    The logic lives in a mixin because miniaudio's ``StreamableSource`` base is
    only importable when miniaudio is, and the digger has to work without it.
    """

    def __init__(
        self,
        session,
        url: str,
        max_buffer: int = MAX_BUFFER_BYTES,
    ) -> None:
        self.session = session
        self.url = url
        self.timeout = SOURCE_TIMEOUT
        self.offset = 0
        self.length: int | None = None
        self._response = None
        self._buffer = bytearray()
        # Where the buffer starts in the file. It only moves when a seek lands
        # somewhere we neither hold nor are on our way to.
        self._base = 0
        self._buffering = False
        self._done = False
        self._failed = False
        self._closed = False
        # Bumped on every restart, so a thread left over from the previous one
        # knows the bytes it is holding are no longer wanted.
        self._generation = 0
        self._lock = threading.Lock()
        self._arrived = threading.Condition(self._lock)
        self._open(0)
        if self.length and self.length <= max_buffer:
            self._buffering = True
            self._spawn()

    # Connection

    def _open(self, offset: int) -> None:
        self._close_response()
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        self._response = self.session.get(
            self.url, headers=headers, stream=True, timeout=self.timeout
        )
        if self.length is None:
            declared = self._response.headers.get("Content-Length")
            self.length = int(declared) + offset if declared else None
        self.offset = offset

    def _close_response(self) -> None:
        if self._response is not None:
            self._response.close()
            self._response = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._arrived.notify_all()
        self._close_response()

    # Buffering

    def _spawn(self) -> None:
        thread = threading.Thread(
            target=self._download,
            args=(self._response, self._generation),
            daemon=True,
        )
        thread.start()

    def _download(self, response, generation: int) -> None:
        """Pull the rest of the response into the buffer, off the audio thread."""

        while True:
            try:
                chunk = response.raw.read(DOWNLOAD_CHUNK)
            except Exception as exc:
                # Half a track in memory is still better than none: reads inside
                # it stay instant, and anything past it goes back to the socket.
                LOGGER.debug("Buffering %s stopped early: %s", self.url, exc)
                chunk = None
            with self._lock:
                if generation != self._generation or self._closed:
                    return
                if chunk:
                    self._buffer.extend(chunk)
                else:
                    self._done = True
                    self._failed = chunk is None
                self._arrived.notify_all()
                if not chunk:
                    return

    def _outside_buffer(self) -> bool:
        """Is the read head somewhere this buffer does not and will not hold?"""

        with self._lock:
            if self.offset < self._base:
                return True
            complete = self._done and not self._failed
            return self.offset > self._base + len(self._buffer) and not complete

    def _restart(self, target: int) -> None:
        """Point the buffer at a new part of the file, dropping what it held."""

        with self._lock:
            self._generation += 1
            self._buffer = bytearray()
            self._base = target
            self._done = False
            self._failed = False
        try:
            self._open(target)
        except Exception as exc:
            LOGGER.debug("Could not reopen %s at %d: %s", self.url, target, exc)
            with self._lock:
                self._done = True
                self._failed = True
                self._arrived.notify_all()
            return
        self._spawn()

    # Reading

    def read(self, num_bytes: int) -> bytes:
        if num_bytes <= 0:
            return b""
        if self._buffering:
            return self._read_buffered(num_bytes)
        return self._read_direct(num_bytes)

    def _read_buffered(self, num_bytes: int) -> bytes:
        if self._outside_buffer():
            self._restart(self.offset)
        data = self._take(num_bytes)
        if len(data) < num_bytes and self._died_early():
            # The download dropped with file still to come, so go back to the
            # socket for the rest rather than reporting the track as over.
            self._restart(self.offset)
            data += self._take(num_bytes - len(data))
        return data

    def _take(self, num_bytes: int) -> bytes:
        """Bytes from the buffer, waiting for the download only if it is behind."""

        deadline = time.monotonic() + self.timeout
        with self._lock:
            while True:
                start = self.offset - self._base
                end = start + num_bytes
                if end <= len(self._buffer) or self._done or self._closed:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._arrived.wait(remaining):
                    break
            data = bytes(self._buffer[start:end])
        self.offset += len(data)
        return data

    def _died_early(self) -> bool:
        with self._lock:
            if self._closed or not self._failed:
                return False
        return self.length is not None and self.offset < self.length

    def _read_direct(self, num_bytes: int) -> bytes:
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

    # Seeking

    def seek(self, offset: int, origin) -> bool:
        target = max(0, self._target(offset, origin))
        if self._buffering:
            # Nothing else to do: the read that follows either finds the bytes
            # here, waits the moment out, or sends us back for them.
            self.offset = target
            return True
        try:
            self._open(target)
        except Exception as exc:
            LOGGER.debug("Range seek failed: %s", exc)
            return False
        return True

    def _target(self, offset: int, origin) -> int:
        if getattr(origin, "value", origin) == 1:  # SeekOrigin.CURRENT
            return self.offset + offset
        if getattr(origin, "value", origin) == 2 and self.length:  # END
            return self.length + offset
        return offset


def open_source(session, url: str):
    """Start pulling a track into memory before anything has asked to hear it."""

    return http_source_type(_import_miniaudio())(session, url)


@lru_cache(maxsize=None)
def http_source_type(miniaudio):
    """The mixin welded onto miniaudio's StreamableSource, built once."""

    return type("HttpSource", (HttpSourceMixin, miniaudio.StreamableSource), {})


@dataclass
class Loaded:
    track: Track
    stream: Stream
    waveform: list[int] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.stream.duration


@dataclass(frozen=True)
class PlaybackEvent:
    """One terminal event from the audio callback, tagged against stale playback."""

    kind: Literal["finished", "error"]
    generation: int
    message: str = ""


class Player:
    """Play, pause, seek and volume over an MP3 streamed straight from SoundCloud."""

    def __init__(self) -> None:
        self._miniaudio = None
        self._device = None
        self._loaded: Loaded | None = None
        self._session = None
        self._source = None
        self._generator = None
        self._frames = 0
        self._offset = 0.0
        self._playing = False
        self._ended = False
        self._generation = 0
        self._events: SimpleQueue[PlaybackEvent] = SimpleQueue()
        self._volume = 0.8
        self._muted = False
        self._level = 0.0
        # Written on the audio thread and read on the interface's, which a deque
        # is safe for on its own - appends and pops are single bytecodes.
        self._levels: deque[float] = deque(maxlen=LEVEL_QUEUE)
        self.unavailable_reason: str | None = None

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
    def loaded(self) -> Loaded | None:
        return self._loaded

    @property
    def playing(self) -> bool:
        return self._playing

    def take_event(self) -> PlaybackEvent | None:
        """Return the next terminal event for the current playback generation."""

        while True:
            try:
                event = self._events.get_nowait()
            except Empty:
                return None
            if event.generation == self._generation:
                return event

    def take_finished(self) -> bool:
        """Compatibility helper for callers interested only in a clean EOF."""

        event = self.take_event()
        return event is not None and event.kind == "finished"

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

    def _silence(self) -> None:
        """Nothing is going out, so nothing measured before it still applies."""

        self._level = 0.0
        self._levels.clear()

    def take_level(self) -> float:
        """The next reading of how loud the audio going out is, 0 to 1.

        Read off the samples on their way to the device, which is the only place
        the actual sound exists - the waveform picture is an average of the whole
        track and says nothing about this instant.

        Oldest first, one per call, because the readings are made faster than
        anything asks for them. When they run out the last one stands, which is
        better than dropping to silence between callbacks.
        """

        if self._levels:
            self._level = self._levels.popleft()
        elif not self._playing:
            self._level = 0.0
        return self._level

    # Controls

    def load(
        self,
        track: Track,
        stream: Stream,
        session,
        waveform: list[int] | None = None,
        source=None,
    ) -> Loaded:
        """``source`` is a stream someone opened ahead of time, already filling."""

        self._miniaudio = self._miniaudio or _import_miniaudio()
        self.stop()
        self._session = session
        self._source = source
        self._loaded = Loaded(track=track, stream=stream, waveform=waveform or [])
        self._frames = 0
        self._offset = 0.0
        return self._loaded

    def _open_stream(self, seek_frame: int):
        miniaudio = self._miniaudio
        self._drop_generator()
        # The source outlives a seek. Replacing it is what used to throw away
        # the buffered track and put a connection in front of every seek.
        if self._source is None:
            self._source = open_source(self._session, self._loaded.stream.url)
        return miniaudio.stream_any(
            self._source,
            source_format=miniaudio.FileFormat.MP3,
            sample_rate=SAMPLE_RATE,
            nchannels=CHANNELS,
            seek_frame=seek_frame,
        )

    def _drop_generator(self) -> None:
        if self._generator is not None:
            self._generator.close()
            self._generator = None

    def _close_source(self) -> None:
        self._drop_generator()
        if self._source is not None:
            self._source.close()
            self._source = None

    def _measure(self, chunk) -> None:
        """Note how loud each frame's worth of this chunk is.

        Runs on the audio callback thread, so it is two calls into C per slice
        and nothing else. Taken before the volume scaling, because it is the
        music that should show and not the fader.
        """

        for start in range(0, len(chunk), LEVEL_WINDOW):
            window = chunk[start : start + LEVEL_WINDOW]
            if len(window):
                self._levels.append(max(max(window), -min(window)) / FULL_SCALE)

    def _feed(self, stream, generation: int):
        # miniaudio sends a frame count into the callback generator, so the first
        # yield must happen before any decoding. It also makes an empty stream end
        # on the callback thread rather than raising while ``play`` primes us.
        required = yield b""
        first = True
        while True:
            if generation != self._generation:
                return
            try:
                # miniaudio can send 0; asking the decoder for nothing reads as EOF.
                frames = required or 1024
                chunk = next(stream) if first else stream.send(frames)
                first = False
                if not len(chunk):
                    raise StopIteration
                self._frames += len(chunk) // CHANNELS
                self._measure(chunk)
                volume = self.volume
                out = (
                    chunk
                    # >= 0.999 rather than == 1.0: a float comparison guard, and at
                    # full volume the per-sample rescale loop is skipped entirely.
                    if volume >= 0.999
                    else array.array("h", [int(sample * volume) for sample in chunk])
                )
            except StopIteration:
                if generation == self._generation:
                    self._playing = False
                    self._ended = True
                    self._generator = None
                    self._silence()
                    self._events.put(PlaybackEvent("finished", generation))
                return
            except Exception as exc:
                if generation == self._generation:
                    self._playing = False
                    self._generator = None
                    self._silence()
                    self._events.put(PlaybackEvent("error", generation, str(exc)))
                return
            required = yield out

    def _stop_device(self) -> None:
        """Stop the output, and let go of a device that will not stop."""

        if self._device is None:
            return
        try:
            self._device.stop()
        except Exception as exc:
            LOGGER.debug("Stopping the audio device complained: %s", exc)
            self._drop_device()

    def _drop_device(self) -> None:
        """Let go of a device that has misbehaved, so the next play rebuilds it.

        Not ``unavailable_reason``: that is for a machine with no output at all,
        and stands until the app is restarted. A device that fails once after
        working deserves another try.
        """

        if self._device is not None:
            try:
                self._device.close()
            except Exception as exc:
                LOGGER.debug("Closing a failed audio device complained: %s", exc)
        self._device = None
        self._playing = False
        self._silence()

    def play(self) -> None:
        if self._loaded is None:
            return
        device = self._device_for(SAMPLE_RATE, CHANNELS)
        if self._ended:
            # At the end of the list, pressing play means replay this track rather
            # than asking an exhausted decoder to seek to its own end again.
            self._drop_generator()
            self._offset = 0.0
            self._frames = 0
            self._ended = False
        if self._generator is None:
            # Reopening the socket costs about half a second, so a plain resume
            # keeps the existing generator and only a seek reopens it.
            self._offset = self.position
            self._frames = 0
            self._generation += 1
            self._generator = self._feed(
                self._open_stream(int(self._offset * SAMPLE_RATE)), self._generation
            )
            # miniaudio sends into the generator without priming it first, and
            # its own docstring says the caller must start it.
            next(self._generator)
        # A very short or broken stream can finish before ``start`` returns, so
        # publish the intended state first and let the callback have the last word.
        self._playing = True
        self._ended = False
        try:
            device.start(self._generator)
        except Exception as exc:
            # miniaudio answers with a numbered failure nobody can act on, and a
            # device that has just been stopped is enough to produce one -
            # pressing play twice in quick succession did it. Raised as the
            # degraded state the app already knows how to show, rather than out
            # through the message pump, where it took the whole TUI with it.
            LOGGER.debug("Could not start the audio device: %s", exc)
            self._drop_device()
            raise PlaybackUnavailable("The audio device would not start - try again") from exc

    def pause(self) -> None:
        if self._playing:
            self._stop_device()
        self._playing = False
        self._silence()

    def toggle(self) -> None:
        self.pause() if self._playing else self.play()

    def stop(self) -> None:
        # Invalidate a callback before asking the device to stop. A late EOF from
        # the old generator must not advance whatever is loaded next.
        self._generation += 1
        self._stop_device()
        self._close_source()
        self._playing = False
        self._ended = False
        self._frames = 0
        self._offset = 0.0
        self._silence()

    def seek(self, seconds: float) -> None:
        if self._loaded is None:
            return
        # Half a second short of the end: seeking to the exact end delivers an
        # immediate end-of-stream and the track reads as finished.
        target = max(0.0, min(max(0.0, self.duration - 0.5), seconds))
        was_playing = self._playing
        self._generation += 1
        self._stop_device()
        # Only the decoder is rebuilt at the new frame. The source stays, and
        # with it the copy of the track, which is what makes this instant.
        self._drop_generator()
        self._offset = target
        self._frames = 0
        self._playing = False
        self._ended = False
        self._silence()
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

    def unload(self) -> None:
        """Stop and forget the track, so the bar has nothing left to say.

        ``stop`` on its own rewinds and keeps the track loaded, which is what
        the end of a track wants; closing the player wants it gone.
        """

        self.stop()
        self._loaded = None

    def close(self) -> None:
        try:
            self.stop()
            if self._device is not None:
                self._device.close()
        except Exception as exc:
            LOGGER.debug("Closing the audio device complained: %s", exc)
        self._device = None
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:
                LOGGER.debug("Closing the playback session complained: %s", exc)


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# The bar is the waveform and nothing else, so this is one and the same number.
PLAYER_HEIGHT = WAVEFORM_ROWS
PLAYER_GROW = 0.2


class PlayerBar(Static):
    """The clickable waveform, and whatever the player has to say for itself.

    The title, the clock and the play state used to head this widget on a line
    of their own. They sit in ``PlayerControls`` now, beside the buttons that
    change them, which is a row this has to draw anyway - so the waveform got
    that row instead.
    """

    DEFAULT_CSS = """
    PlayerBar {
        height: 0;
        padding: 0 1;
        overflow: hidden;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, player: Player, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player = player
        self.message = ""
        self.meter = LevelMeter()
        self.wanted_height = 0
        # The glyphs for the loaded track at the current width, which only need
        # rebuilding when one of those two changes.
        self._shape: list[str] = []
        self._shape_for = (None, 0)

    def refresh_bar(self) -> None:
        self.update(self._content())
        loaded = self.player.loaded is not None
        # A message with nothing loaded - "Loading X", or a dead audio device -
        # is one line of text and does not need the waveform's four rows.
        self._want(PLAYER_HEIGHT if loaded else (1 if self.message else 0))
        # The controls are a sibling widget rather than part of this one, because
        # buttons cannot live inside a Static that repaints thirty times a second.
        for controls in self.screen.query(PlayerControls):
            controls.display = loaded
            if loaded:
                controls.refresh_controls(self.message)

    def _want(self, height: int) -> None:
        """Grow or fold away, rather than blinking in and out of existence."""

        if height == self.wanted_height:
            return
        self.wanted_height = height
        self.styles.animate("height", value=height, duration=PLAYER_GROW)

    def _content(self) -> Text:
        loaded = self.player.loaded
        if loaded is None:
            self.meter.reset()
            return Text(self.message, style="bright_black")
        level = self.meter.feed(self.player.take_level())
        return paint_waveform(self._rows(loaded), self.player.fraction, level)

    def _rows(self, loaded: Loaded) -> list[str]:
        width = self._bar_width()
        wanted = (loaded.track.key, width)
        if self._shape_for != wanted:
            self._shape = waveform_rows(loaded.waveform, width)
            self._shape_for = wanted
        return self._shape

    def _bar_width(self) -> int:
        return max(1, self.size.width - 2)

    def seconds_at(self, x: int) -> float:
        """Turn a click position into a time, for seeking on the waveform."""

        width = self._bar_width()
        fraction = min(1.0, max(0.0, (x - 1) / width)) if width else 0.0
        return fraction * self.player.duration

    def on_click(self, event) -> None:
        if self.player.loaded is None:
            return
        event.stop()
        try:
            self.player.seek(self.seconds_at(event.x))
        except PlaybackUnavailable as exc:
            self.message = str(exc)
        except Exception as exc:  # a bad backend must not take the app down
            LOGGER.exception("Seeking failed")
            self.message = f"Seek failed ({exc})"
        self.refresh_bar()


# Text presentation throughout - no emoji, which every terminal draws in its
# own colour and at its own size. A glyph cannot be made larger than its cell,
# so the buttons read as controls through their chip background and bold weight
# instead. Doubled arrows for the steps: two cells of glyph in a six-cell chip.
PREVIOUS_GLYPH = "\u25c0\u25c0"
PLAY_GLYPH = "\u25b6"
PAUSE_GLYPH = "\u275a\u275a"
NEXT_GLYPH = "\u25b6\u25b6"
CLOSE_GLYPH = "\u2715"

# Twelve cells to aim at. Under about ten a click lands two steps from where you
# meant it, and the whole row still has to fit beside the transport buttons.
VOLUME_TRACK = 12
# Volume glyph and the space after it: where the draggable track starts.
VOLUME_TRACK_START = 2


class VolumeSlider(Static):
    """The speaker and its track. Click or drag anywhere along it to set the volume."""

    DEFAULT_CSS = """
    VolumeSlider {
        width: 22;
        height: 3;
        content-align: left middle;
    }
    """

    def __init__(self, player: Player, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player = player

    def render(self) -> Text:
        volume = self.player.volume
        filled = round(volume * VOLUME_TRACK)
        bar = Text("\u00d8 " if volume <= 0 else "\u266a ", style="bold")
        bar.append("━" * filled, style="cyan")
        bar.append("●", style="bold cyan")
        bar.append("─" * (VOLUME_TRACK - filled), style="bright_black")
        bar.append(f" {int(volume * 100):>3}%", style="bright_black")
        return bar

    def set_from_x(self, x: int) -> None:
        fraction = (x - VOLUME_TRACK_START) / VOLUME_TRACK
        # Rounded to the step the track can actually draw, so the number beside
        # it does not read 63% on a knob sitting exactly where 60% was.
        self.player.set_volume(round(min(1.0, max(0.0, fraction)) * VOLUME_TRACK) / VOLUME_TRACK)
        self.refresh()

    def on_mouse_down(self, event) -> None:
        event.stop()
        # Captured so the knob keeps following once the pointer leaves the row,
        # which is what tells a slider apart from a row of buttons.
        self.capture_mouse()
        self.set_from_x(event.x)

    def on_mouse_move(self, event) -> None:
        if self.app.mouse_captured is not self:
            return
        if not event.button:
            # The release went missing - a drag that ended off the terminal, say.
            # Left captured, this widget would swallow every click in the app.
            self.release_mouse()
            return
        self.set_from_x(event.x)

    def on_mouse_up(self, event) -> None:
        self.release_mouse()


class PlayerControls(Horizontal):
    """Transport, volume and the way out, under the waveform.

    Every one of these has a key already; the buttons are for the hand that is
    on the mouse anyway, having just clicked the waveform to seek.
    """

    DEFAULT_CSS = """
    PlayerControls {
        display: none;
        height: 3;
        width: 100%;
        padding: 0 1;
    }
    /* One row, no border: a Textual button is three rows tall by default, which
       spent more of the terminal on three glyphs than on the track list.
       By id rather than `PlayerControls Button`, because Textual keys its own
       button borders on a class - which out-specifies a plain type selector, so
       a `border: none` there loses and every button keeps its border row. */
    #player-prev, #player-play, #player-next, #player-close {
        height: 3;
        /* Six against a two-cell glyph: both even, so the icon lands dead
           centre. An odd width either way leaves it half a cell off. */
        width: 6;
        min-width: 6;
        margin: 0 1 0 0;
        border: none;
        /* $boost, not $panel: a translucent lift of whatever is under it, so
           the chip reads in any theme instead of committing to a colour. */
        background: $boost;
        color: $text;
        text-style: bold;
    }
    #player-prev:hover, #player-play:hover, #player-next:hover, #player-close:hover {
        background: $accent;
    }
    /* Takes the slack, and gives the title up an ellipsis at a time rather than
       pushing the clock and the volume off the row. */
    #player-title {
        width: 1fr;
        height: 3;
        content-align: left middle;
        padding: 0 2;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    #player-time {
        width: 14;
        height: 3;
        content-align: right middle;
        padding: 0 2 0 0;
    }
    """

    def __init__(self, player: Player, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player = player
        # What the buttons were last drawn for. A tick repaints the bar thirty
        # times a second and none of that reaches the DOM unless this changes.
        self._shown: tuple | None = None

    def compose(self) -> ComposeResult:
        yield Button(PREVIOUS_GLYPH, id="player-prev", tooltip="Previous track (p)")
        yield Button(PLAY_GLYPH, id="player-play", tooltip="Play or pause (space)")
        yield Button(NEXT_GLYPH, id="player-next", tooltip="Next track (n)")
        yield Static("", id="player-title")
        yield Static("", id="player-time")
        yield VolumeSlider(self.player, id="player-volume")
        yield Button(CLOSE_GLYPH, id="player-close", tooltip="Stop and close the player (ctrl+w)")

    def refresh_controls(self, message: str = "") -> None:
        loaded = self.player.loaded
        if loaded is None:
            return
        # The clock is the only part of this that moves on its own, and it moves
        # once a second - the other twenty-nine ticks have nothing to write.
        state = (
            self.player.playing,
            self.player.volume,
            int(self.player.position),
            loaded.track.key,
            message,
        )
        if state == self._shown:
            return
        self._shown = state
        self.query_one("#player-play", Button).label = (
            PAUSE_GLYPH if self.player.playing else PLAY_GLYPH
        )
        # Text(), not markup: a title like "Rido - Sexy Thing [Clip]" keeps its
        # brackets, and a message is the one thing worth the room over a title.
        self.query_one("#player-title", Static).update(
            Text(message, style="yellow")
            if message
            else Text(loaded.track.label, no_wrap=True, overflow="ellipsis")
        )
        self.query_one("#player-time", Static).update(
            Text(
                f"{format_time(self.player.position)} / {format_time(self.player.duration)}",
                style="bright_black",
            )
        )
        self.query_one(VolumeSlider).refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "player-prev": lambda: self.app.action_play_step(-1),
            "player-play": self.app.action_toggle_loaded,
            "player-next": lambda: self.app.action_play_step(1),
            "player-close": self.app.action_close_player,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

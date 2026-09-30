"""Local PCM playback that keeps what it decoded; subprocess and pipe work stays off the audio callback."""
import array
import threading
from pathlib import Path

import numpy as np

from .media import MediaError, pcm_blocks, probe
from .services.playback import Prepared, Stream

# Protect loaded and prefetched media, including the short replacement commit.
LEASE_LOCK = threading.RLock()
LEASES: dict[Path, int] = {}
SOURCES = set()


BYTES_PER_FRAME = 4
BYTES_PER_SECOND = 44100 * BYTES_PER_FRAME
# Decoded PCM is kept rather than streamed through a two-second window: a seek
# into audio already decoded moves an index instead of restarting FFmpeg, and
# after a few seconds a whole track shorter than this cap sits in memory.
RETAINED_BYTES = 64 * 1024 * 1024
# Once the cap is reached the oldest audio goes first, keeping this much
# behind the read head so short backwards seeks still cost nothing.
KEEP_BEHIND_BYTES = 30 * BYTES_PER_SECOND


class LocalSource:
    def __init__(self, path: Path, retained=RETAINED_BYTES):
        self.path = path.resolve(strict=True)
        self.retained = retained
        self._lock = threading.Condition()
        self._buffer = bytearray()
        # Byte offsets in the decoded stream: where the buffer starts and where
        # the callback reads. Both move in whole frames.
        self._base = 0
        self._position = 0
        self._cancel = threading.Event()
        self._generation = 0
        self._request = 0.0
        self._eof = False
        self._error = None
        self.last_frames = 0
        self.decodes = 0
        self._closed = False
        with LEASE_LOCK:
            LEASES[self.path] = LEASES.get(self.path, 0) + 1
            SOURCES.add(self)
        self._thread = threading.Thread(target=self._produce, name='local-audio-decoder')
        self._thread.start()

    @staticmethod
    def _offset(seconds):
        return max(0, int(seconds * 44100)) * BYTES_PER_FRAME

    def restart(self, seconds):
        """Move the read head; only audio outside the retained PCM needs a decoder."""
        target = self._offset(seconds)
        with self._lock:
            held = self._base <= target <= self._base + len(self._buffer)
            if held and (target < self._base + len(self._buffer) or self._eof or self._request is None):
                self._position = target
                return
            self._cancel.set()
            self._generation += 1
            self._request = seconds
            self._buffer.clear()
            self._base = self._position = target
            self._eof, self._error = False, None
            self._lock.notify_all()

    def _produce(self):
        try:
            while True:
                with self._lock:
                    while self._request is None and not self._closed:
                        self._lock.wait()
                    if self._closed:
                        return
                    seconds, generation = self._request, self._generation
                    self._request = None
                    cancel = self._cancel = threading.Event()
                    self.decodes += 1
                try:
                    for chunk in pcm_blocks(self.path, rate=44100, channels=2, sample_format='s16le', seek=seconds, cancel=cancel):
                        with self._lock:
                            while not self._room(len(chunk)) and not cancel.is_set():
                                self._lock.wait(.1)
                            if cancel.is_set() or generation != self._generation:
                                break
                            self._buffer.extend(chunk)
                            self._lock.notify_all()
                except Exception as exc:
                    with self._lock:
                        if generation == self._generation and not cancel.is_set():
                            self._error = str(exc)
                finally:
                    with self._lock:
                        if generation == self._generation:
                            self._eof = True
                        self._lock.notify_all()
        finally:
            with LEASE_LOCK:
                LEASES[self.path] -= 1
                if not LEASES[self.path]:
                    del LEASES[self.path]
                SOURCES.discard(self)

    def _room(self, size):
        """Make space for ``size`` more bytes by dropping audio far behind the head."""
        excess = len(self._buffer) + size - self.retained
        if excess <= 0:
            return True
        keep = min(KEEP_BEHIND_BYTES, self.retained // 4)
        drop = min(excess, self._position - self._base - keep) // BYTES_PER_FRAME * BYTES_PER_FRAME
        if drop > 0:
            del self._buffer[:drop]
            self._base += drop
        return len(self._buffer) + size <= self.retained

    def take(self, frames):
        with self._lock:
            start = self._position - self._base
            count = max(0, min(frames * BYTES_PER_FRAME, len(self._buffer) - start)) // BYTES_PER_FRAME * BYTES_PER_FRAME
            chunk = bytes(self._buffer[start:start + count])
            self._position += count
            self.last_frames = count // BYTES_PER_FRAME
            self._lock.notify_all()
            if not count and self._eof:
                if self._error:
                    raise MediaError(self._error)
                return array.array('h')
            if not self._eof:
                chunk += bytes(frames * BYTES_PER_FRAME - count)
        result = array.array('h')
        result.frombytes(chunk)
        import sys
        if sys.byteorder != 'little':
            result.byteswap()
        return result

    def stream(self, seek_frame):
        if seek_frame:
            self.restart(seek_frame / 44100)
        frames = 1024
        while True:
            frames = (yield self.take(frames)) or 1024

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        with self._lock:
            self._lock.notify_all()

    def join(self):
        self.close()
        self._thread.join()


def prepare_local(track):
    path = Path(track.local_path)
    with LEASE_LOCK:
        metadata = probe(path)
        source = LocalSource(path)
    return Prepared(track, Stream(str(path), duration=metadata['duration']), source=source)


def close_all():
    with LEASE_LOCK:
        sources = tuple(SOURCES)
    for source in sources:
        source.close()
    for source in sources:
        source.join()


def _fold_peaks(peaks, block, frame, frames_per_bin):
    """Fold one s16le stereo block into the per-bin peaks; returns the next frame number."""

    # int32 before abs: -32768 has no positive int16.
    samples = np.frombuffer(block, dtype='<i2', count=len(block) // 4 * 2).astype(np.int32)
    loudest = np.abs(samples).reshape(-1, 2).max(axis=1, initial=0)
    bins = np.minimum(1023, (frame + np.arange(len(loudest))) // frames_per_bin)
    np.maximum.at(peaks, bins, loudest)
    return frame + len(loudest)


def waveform(path, cancel=None):
    """Independent low-resolution envelope; never a playback gate. The library keeps it."""
    from .media import signature
    path = Path(path)
    before = signature(path)
    metadata = probe(path, cancel)
    frames_per_bin = max(1, round(metadata['duration'] * 4000 / 1024))
    peaks = np.zeros(1024, dtype=np.int32)
    frame = 0
    for block in pcm_blocks(path, rate=4000, channels=2, sample_format='s16le', cancel=cancel):
        frame = _fold_peaks(peaks, block, frame, frames_per_bin)
    return peaks.tolist() if signature(path) == before else []


def encoded_waveform(data):
    """The same 1024 peaks from a whole encoded file held in memory (a streamed MP3)."""
    import miniaudio
    decoded = miniaudio.decode(bytes(data), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2, sample_rate=4000)
    peaks = np.zeros(1024, dtype=np.int32)
    _fold_peaks(peaks, decoded.samples.tobytes(), 0, max(1, -(-decoded.num_frames // 1024)))
    return peaks.tolist()


ARTWORK_NAMES = ('cover', 'folder', 'front', 'albumart')
MAX_ARTWORK_BYTES = 16 * 1024 * 1024


def artwork(path, cancel=None):
    """Picture embedded in the file, else a cover image beside it; None without one."""
    from .media import binary, input_args, run
    path = Path(path)
    pictures = probe(path, cancel)['artwork']
    if pictures:
        data = run([binary('ffmpeg'), '-v', 'error', *input_args(path), '-map', f"0:{pictures[0]['index']}",
                    '-c', 'copy', '-f', 'image2pipe', '-'], cancel=cancel, timeout=30, output_limit=MAX_ARTWORK_BYTES)
        if data:
            return data
    for entry in sorted(path.parent.iterdir()):
        if (entry.stem.lower() in ARTWORK_NAMES and entry.suffix.lower() in ('.jpg', '.jpeg', '.png')
                and entry.is_file() and entry.stat().st_size <= MAX_ARTWORK_BYTES):
            return entry.read_bytes()
    return None

"""How much a kick drum is sounding, every 10 ms, from decoded PCM; nothing is predicted.

The light follows a continuous level instead of yes/no decisions, so it never
stops on a missed hit. The level is the 40-130 Hz energy rising above what holds
there (a sustained bass, its sidechain recovery, a pad), weighted by how much the
onset sounds like a kick: like a generic kick at first, then like the kick this
track keeps repeating. A bass note moves in pitch and holds; a kick drops in
pitch and is the same sound every time. Any other low hit still shows, dimmer,
in proportion to its energy. The player decodes 0.4 s ahead of the device, so
each level is ready about 0.2 s before it is heard.
"""
import array
from collections import deque

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2
DOWNSAMPLE = 4
RATE = SAMPLE_RATE / DOWNSAMPLE
WINDOW = 512  # 46 ms at 11 kHz
STEP = 110  # 10 ms hops
HOP = STEP / RATE
BANDS = slice(1, 11)  # 21-215 Hz in 21.5 Hz bins
FREQUENCIES = np.fft.rfftfreq(WINDOW, 1 / RATE)[BANDS]
KICK_BAND = (FREQUENCIES >= 40) & (FREQUENCIES <= 130)
FLOOR = .003  # Log floor in |FFT|/128 units: silence is not a sound.
# Each bin's median over hops n±SPAN is what holds there. Only what rises above it
# can be a kick.
SPAN = 12
PRINT = 8  # Hops of residual spectrum in an onset's fingerprint.
DELAY = SPAN + PRINT - 1  # Hops between a sound and its level.
OFFSET = -.003  # Seconds from a window's centre to the attack whose level peaks there.
# Kick-likeness of a fingerprint, as similarity ramps to a weight of WEIGHT_FLOOR-1.
# The floor keeps fills, a filtered kick and bass hits visible in proportion.
GENERIC_RAMP = (.45, .8)
TRACK_RAMP = (.6, .85)
WEIGHT_FLOOR = .4
HOLD = 15  # Hops an onset's weight carries through its tail.
# Loudness: a level is the weighted energy against the loudest of the last few
# seconds, never below a share of the loudest of the last half minute (so a
# breakdown stays dark after a drop) or a floor (so silence stays dark).
FAST = 3.  # Seconds
SLOW = 30.
SLOW_SHARE = .35
LEVEL_FLOOR = .05
KNEE = .1
KEEP = 3.  # Seconds of levels kept for readers.
# Learning the track's kick from repeated onsets:
FLUX = 2.6  # Summed log rise of an onset.
PEAK = 3  # An onset is the largest rise within ±3 hops.
WARM_UP = 13  # Hops before a fresh analysis may hear an onset.
SEED = .6  # Similarity to the generic kick for an onset to teach the track's kick.
TWIN = .92  # Similarity between repeats of the same kick.
GROUP = 5  # Repeats that make a kick.
ACCEPT = .85  # Similarity that counts as the track's kick.
ADAPT = .9  # Similarity that lets an onset refine it.
DROP = 10.  # Hz the band's centroid must fall over a kick's first 70 ms.
RESEED = 4.  # Seconds without a match before another kick may take over.
# ponytail: a low hit that sounds like the track's kick lights up like one, and a
# kick without a pitch drop (a flat sine) never teaches the track's kick; it then
# glows like any other low hit, in proportion to its energy.


def _spectra(down):
    """|FFT|/128 in BANDS for every whole window, stepping STEP, over (n, 2) floats."""
    count = (len(down) - WINDOW) // STEP + 1
    if count <= 0:
        return np.zeros((0, BANDS.stop - BANDS.start))
    frames = down[np.arange(WINDOW)[None, :] + (np.arange(count) * STEP)[:, None]] * _HANN
    # Combine channel powers: opposite stereo phases must not cancel bass.
    return np.sqrt(np.mean(abs(np.fft.rfft(frames, axis=1)) ** 2, axis=2))[:, BANDS] / 128


def _residual(spectra):
    """Per hop of ``spectra[SPAN:-SPAN]``: the log rise above what holds, and the residual spectrum."""
    logs = np.log(spectra + FLOOR)
    held = np.median(np.lib.stride_tricks.sliding_window_view(logs, 2 * SPAN + 1, axis=0), axis=2)
    rise = np.maximum(0, logs[SPAN:-SPAN] - held)
    return rise, np.maximum(0, spectra[SPAN:-SPAN] - (np.exp(held) - FLOOR))


def _unit(vector):
    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else None


def _falls(fingerprint) -> bool:
    """A kick drops in pitch over its first hops; a bass note does not."""
    energy = fingerprint.reshape(PRINT, -1) ** 2
    centroid = energy @ FREQUENCIES / np.maximum(energy.sum(axis=1), 1e-12)
    return centroid[:2].mean() - centroid[4:7].mean() >= DROP


def _ramp(similarity, ramp) -> float:
    low, high = ramp
    return min(1., max(0., (similarity - low) / (high - low)))


def _generic_kick():
    """The mean fingerprint of a few synthetic kicks: plain, 808, clicky, driven, deep."""
    prints = []
    for start, end, sweep, decay, click, drive, length in (
            (150, 50, 40, 12, 0, 1, .5), (110, 45, 25, 3, 0, 1, .9), (220, 55, 60, 18, .3, 1, .5),
            (160, 52, 45, 10, 0, 3, .5), (90, 42, 30, 7, 0, 1, .5)):
        t = np.arange(int(length * SAMPLE_RATE)) / SAMPLE_RATE
        kick = np.exp(-t * decay) * np.sin(2 * np.pi * (end * t + (start - end) * (1 - np.exp(-t * sweep)) / sweep))
        kick += click * np.random.default_rng(7).standard_normal(len(t)) * np.exp(-t * 400)
        kick = np.tanh(drive * kick) / np.tanh(drive)
        audio = np.zeros(2 * SAMPLE_RATE)
        audio[SAMPLE_RATE // 2:SAMPLE_RATE // 2 + len(kick)] = .5 * kick
        down = audio.reshape(-1, DOWNSAMPLE).mean(axis=1)[:, None].repeat(CHANNELS, axis=1)
        spectra = _spectra(down)
        rise, residual = _residual(np.concatenate((spectra[:1].repeat(SPAN, axis=0), spectra, spectra[-1:].repeat(SPAN, axis=0))))
        flux = np.r_[0, np.maximum(0, np.diff(rise, axis=0)).sum(axis=1)]
        onset = int(np.argmax(flux))
        prints.append(_unit(residual[onset:onset + PRINT].ravel()))
    return _unit(np.mean(prints, axis=0))


_HANN = np.hanning(WINDOW)[None, :, None]
GENERIC = _generic_kick()


class KickEnergy:
    """Per track: a kick level of 0-1 every HOP seconds, written from the audio thread.

    ``feed`` takes contiguous stereo s16 PCM; the first call after ``seeked``
    sets the track time of its first frame. A seek restarts the analysis but
    keeps what the track has taught it: its kick and its loudness.
    """

    def __init__(self):
        self.kick_print = None  # The track's kick fingerprint, once learned.
        self.matched = 0.  # When an onset last matched it.
        self.pool: list = []  # Recent kick-like onset fingerprints to learn from.
        self._fast = self._slow = 0.
        self._series = (0., ())  # (time of the first level, levels): one immutable snapshot.
        self.seeked()

    def seeked(self) -> None:
        self._start = None  # Track time of the first fed frame.
        self._rest = array.array("h")
        self._down = np.zeros((0, CHANNELS))
        self._hops = 0  # Spectra computed so far.
        self._spectra = None  # Spectra of the last 2·SPAN hops, edge-padded at the start.
        self._rise = None  # Log rise of the last analysed hop.
        self._pending = deque()  # (hop, flux, residual) awaiting the hops after them.
        self._weights = deque([0.] * HOLD, maxlen=HOLD)
        self._series = (0., ())

    def levels(self, start: float, end: float) -> tuple[float, float, list[float]]:
        """(time of the first level, seconds per level, levels) within start..end."""
        first, levels = self._series
        low = max(0, int(np.ceil((start - first) / HOP)))
        high = min(len(levels), int((end - first) / HOP) + 1)
        return first + low * HOP, HOP, list(levels[low:high]) if high > low else []

    def feed(self, chunk, start: float) -> None:
        if self._start is None:
            self._start = start
        data = self._rest
        data.extend(chunk)
        whole = len(data) - len(data) % (DOWNSAMPLE * CHANNELS)
        self._rest = data[whole:]
        if not whole:
            return
        pcm = np.frombuffer(data, dtype=np.int16, count=whole) / 32768
        self._down = np.concatenate((self._down, pcm.reshape(-1, DOWNSAMPLE, CHANNELS).mean(axis=1)))
        spectra = _spectra(self._down)
        if not len(spectra):
            return
        self._down = self._down[len(spectra) * STEP:]
        if self._spectra is None:  # The first hop stands in for the hops before it.
            self._spectra = spectra[:1].repeat(SPAN, axis=0)
        self._spectra = np.concatenate((self._spectra, spectra))
        self._hops += len(spectra)
        if len(self._spectra) <= 2 * SPAN:
            return
        rise, residual = _residual(self._spectra)
        previous = self._rise if self._rise is not None else rise[:1]
        flux = np.maximum(0, np.diff(np.concatenate((previous, rise)), axis=0)).sum(axis=1)
        first = self._hops - len(self._spectra) + SPAN  # Hop of rise[0].
        self._pending.extend((first + i, flux[i], residual[i]) for i in range(len(rise)))
        self._rise = rise[-1:]
        self._spectra = self._spectra[-2 * SPAN:]
        self._weigh()

    def _weigh(self) -> None:
        pending, added = self._pending, []
        fast = np.exp(-HOP / FAST)
        slow = np.exp(-HOP / SLOW)
        while len(pending) >= PEAK + max(PEAK + 1, PRINT):
            hop, flux, residual = pending[PEAK]
            time = self._start + hop * HOP + WINDOW / 2 / RATE + OFFSET
            fingerprint = _unit(np.ravel([r for _, _, r in list(pending)[PEAK:PEAK + PRINT]]))
            weight = 0.
            if fingerprint is not None:
                generic = float(fingerprint @ GENERIC)
                track = float(fingerprint @ self.kick_print) if self.kick_print is not None else None
                weight = max(WEIGHT_FLOOR, _ramp(track, TRACK_RAMP) if track is not None else _ramp(generic, GENERIC_RAMP))
                if hop >= WARM_UP and flux >= FLUX and flux >= max(f for _, f, _ in list(pending)[:2 * PEAK + 1]):
                    self._onset(fingerprint, generic, track, time)
            self._weights.append(weight)
            energy = float(residual[KICK_BAND].sum()) * max(self._weights)
            self._fast = max(energy, self._fast * fast)
            self._slow = max(energy, self._slow * slow)
            reference = max(self._fast, SLOW_SHARE * self._slow, LEVEL_FLOOR)
            # A plain float: the window cannot read a numpy one, and showed only 0 and 1.
            level = float(min(1., max(0., (energy / reference - KNEE) / (1 - KNEE))))
            added.append((time, level))
            pending.popleft()
        if added:
            first, levels = self._series
            if not levels:
                first = added[0][0]
            levels = levels + tuple(level for _, level in added)
            drop = max(0, len(levels) - int(KEEP / HOP))
            self._series = (first + drop * HOP, levels[drop:])

    def _onset(self, fingerprint, generic: float, track: float | None, time: float) -> None:
        """Learn and refine the track's kick from onsets that resemble a kick."""
        if track is not None and track >= ACCEPT:
            self.matched = time
            if track >= ADAPT:
                self.kick_print = _unit(.9 * self.kick_print + .1 * fingerprint)
        if generic < SEED:
            return
        self.pool = (self.pool + [fingerprint])[-16:]
        if (self.kick_print is None or time - self.matched >= RESEED) and len(self.pool) > GROUP:
            prints = np.array(self.pool)
            twins = prints @ prints.T >= TWIN
            best = int(np.argmax(twins.sum(axis=1)))
            if twins[best].sum() >= GROUP:
                kick = _unit(prints[twins[best]].mean(axis=0))
                if kick is not None and _falls(kick):
                    self.kick_print, self.matched, self.pool = kick, time, []

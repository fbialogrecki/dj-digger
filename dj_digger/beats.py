"""Causal bass attacks from queued PCM; no predicted beats during breaks."""
import array
import math
import statistics
from collections import deque
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2
# A short stereo FFT keeps bass pitch movement distinct from a fresh attack.
DOWNSAMPLE = 4
WINDOW = 512
HOP_FRAMES = 440
HOP = HOP_FRAMES / SAMPLE_RATE
WINDOW_SECONDS = WINDOW * DOWNSAMPLE / SAMPLE_RATE
KICK_GAP = .06  # Sixteenths up to 210 BPM, without double-counting a kick tail.
# A kick's body peaks 40-60 ms after its attack, when a sidechained sub is still
# ducked; a bass stab is as loud at the attack as it gets. Levels are read over
# this many hops after the confirmed attack before a hit is published.
BODY_FRAMES = 4
# Below this bass level a hit is dust from a fade, never the loudest kick around.
NOISE_LEVEL = .05


@dataclass(frozen=True)
class Kick:
    time: float  # On the track, in seconds.
    strength: float  # 0..1 attack contrast against recent attacks
    level: float  # 30-180 Hz level at the peak, in spectrum units


class KickDetector:
    """Frequency-dilated spectral novelty with a local, gain-relative threshold.

    A moving harmonic is compared to neighbouring bins of the previous frame.
    Sensitivity follows recent attacks, not the loudness of a sustained bass.
    """

    # ponytail: onset detection, not instrument separation; percussive synth
    # bass can still resemble a kick in a mastered mix.

    def __init__(self, tracker: "PulseHistory | None" = None):
        self._tracker = tracker
        self._rest = array.array("h")
        self._samples = np.zeros((WINDOW, CHANNELS))
        self._window = np.hanning(WINDOW)[:, None]
        self._spectrum = np.zeros(WINDOW // 2 + 1)
        frequencies = np.fft.rfftfreq(WINDOW, DOWNSAMPLE / SAMPLE_RATE)
        self._low = (frequencies >= 30) & (frequencies < 180)
        self._upper = (frequencies >= 180) & (frequencies < 1500)
        self._frequencies = frequencies[self._low]
        self._novelty = deque([0.] * 60, maxlen=60)
        self._power = deque([0.] * 4, maxlen=4)
        self._levels = deque([0.] * 2, maxlen=2)
        self._candidate = None
        self._pending = None  # (time, strength, peak level, hops left, bass-led, previous hit, novelty)
        self._last = -1.

    def feed(self, chunk, start: float) -> list[Kick]:
        """Consume stereo s16 PCM; ``start`` is the first input frame's track time."""
        time = start - len(self._rest) / CHANNELS / SAMPLE_RATE
        data = self._rest
        data.extend(chunk)
        step = HOP_FRAMES * CHANNELS
        whole = len(data) - len(data) % step
        self._rest = data[whole:]
        kicks = []
        for offset in range(0, whole, step):
            pcm = np.frombuffer(data, dtype=np.int16, count=step, offset=offset * 2)
            down = pcm.reshape(-1, DOWNSAMPLE, CHANNELS).mean(axis=1) / 32768
            self._samples = np.concatenate((self._samples[len(down):], down))
            time += HOP
            if kick := self._onset(time):
                kicks.append(kick)
                if self._tracker is not None:
                    self._tracker.kick(kick)
        return kicks

    def _onset(self, time: float) -> Kick | None:
        fft = np.fft.rfft(self._samples * self._window, axis=0)
        # Combine channel powers: opposite stereo phases must not cancel bass.
        spectrum = np.sqrt(np.mean(abs(fft) ** 2, axis=1))
        level = float(spectrum[self._low].sum())
        padded = np.pad(self._spectrum, (2, 2))
        expanded = np.maximum.reduce([padded[i:i + len(spectrum)] for i in range(5)])
        novelty = np.maximum(0., spectrum - expanded)
        self._spectrum = spectrum
        bass = float(novelty[self._low].sum())
        other = float(novelty[self._upper].sum())
        upper_level = float(spectrum[self._upper].sum())
        centroid = float(novelty[self._low] @ self._frequencies) / max(bass, 1e-9)
        # Sub-bass swelling and a kick tail need more contrast than its attack.
        sharpness = 3 if centroid < 75 else 2
        power = float(np.linalg.norm(spectrum))
        rise = max(power - self._power[-2], self._power[-1] - self._power[-3])
        previous_rise = max(0., self._power[-2] - self._power[-4])
        attack = rise > .10 * power and rise > 3 * previous_rise
        self._power.append(power)
        # A kick whose click fades while its body swells keeps full-band power level;
        # its bass alone still grows clearly.
        body = level - min(self._levels) >= .15 * level
        self._levels.append(level)
        previous = self._novelty[-1]
        prior = sum(list(self._novelty)[-3:]) / 3
        self._novelty.append(bass)
        reference = max(self._novelty)
        share = .35 if centroid >= 75 and time - WINDOW_SECONDS / 2 - self._last < .18 else .5
        threshold = max(.02, 1.5 * sum(self._novelty) / len(self._novelty), share * reference)
        # A clap or snare on the kick can outweigh its bass at the attack. The kick
        # then shows in its body: the bass outweighs the rest once the click is gone.
        balanced = level >= upper_level
        kick = None
        fresh = False
        if self._candidate is not None and bass <= previous:
            at, strength, peak, bassy, onset = self._candidate
            pending = self._pending
            if pending is not None and at - pending[0] < KICK_GAP and onset > pending[6]:
                # The leading edge peaked first; the kick's own attack is stronger.
                self._pending = (at, strength, max(peak, pending[2], level), BODY_FRAMES,
                                 bassy or balanced or pending[4], pending[5], onset)
                self._last, fresh = at, True
            elif at - self._last >= KICK_GAP:
                if pending is not None:  # A slow earlier hit is due now, before this one.
                    kick = Kick(pending[0], pending[1], pending[2]) if pending[4] else None
                self._pending = (at, strength, max(peak, level), BODY_FRAMES, bassy or balanced, self._last, onset)
                self._last, fresh = at, True
        if self._pending is not None and not fresh:
            at, strength, peak, left, bassy, before, onset = self._pending
            peak, left, bassy = max(peak, level), left - 1, bassy or balanced
            if not left and bass > previous and bass > onset and time - WINDOW_SECONDS / 2 - at < .08:
                left = 1  # A stronger attack is still rising: wait for it to take over.
            self._pending = (at, strength, peak, left, bassy, before, onset) if left else None
            if not left:
                if bassy:
                    kick = Kick(at, strength, peak)
                else:
                    self._last = before  # Not a kick; the gap belongs to the last one.
        # Confirm a local peak one frame later. A tiny positive full-band rise
        # rejects steady-power pitch sweeps without demanding a loudness jump
        # or subsequent RMS decay from a compressed recording.
        eligible = (bass > previous and (bass >= threshold and bass >= sharpness * prior
                                         or attack and centroid >= 75 and bass >= .3 * reference)
                    and bass >= max(.02, .03 * level) and (rise >= .005 * power or body))
        at = max(0., time - WINDOW_SECONDS / 2)
        bassy = bass >= .5 * other and level >= .25 * upper_level
        self._candidate = (at, min(1., (bass / max(.02, reference)) ** .5), level, bassy, bass) if eligible else None
        return kick


class PulseHistory:
    """Bounded hits from the audio thread; readers get one immutable snapshot.

    Each hit is weighed against the loudest bass of the last two bars, so a stab
    at 75 % of the kick pulses dimly and one under 60 % of it not at all, while
    a quieter section recovers its full pulse within two bars. There is no BPM
    warm-up, predicted grid or persistent roll charge.
    """

    def __init__(self, period: float | None = None):
        self._period = period  # Seconds per beat when the track's BPM is known.
        self._pulses: tuple[tuple[float, float], ...] = ()
        self._reference = 0.
        self._reference_at = 0.

    def kick(self, kick: Kick) -> None:
        memory = 8 * self._period if self._period else 4.0  # Two bars.
        decayed = self._reference * math.exp(-max(0., kick.time - self._reference_at) / memory)
        self._reference = max(kick.level, decayed, NOISE_LEVEL)
        self._reference_at = kick.time
        amplitude = min(1., max(0., (kick.level / self._reference - .6) / .4))
        kept = tuple(p for p in self._pulses if p[0] >= kick.time - 2.0)
        self._pulses = kept + ((kick.time, amplitude),) if amplitude > 0 else kept

    def seeked(self) -> None:
        self._pulses = ()

    def period(self) -> float:
        """Seconds per beat: the track's BPM, else the median gap of recent hits."""
        if self._period:
            return self._period
        gaps = [b[0] - a[0] for a, b in zip(self._pulses, self._pulses[1:]) if .25 <= b[0] - a[0] <= 1.0]
        return statistics.median(gaps) if gaps else .5

    def pulses(self, start: float, end: float) -> tuple[list[tuple[float, float]], float]:
        pulses = self._pulses
        return [p for p in pulses if start <= p[0] <= end], self.period()

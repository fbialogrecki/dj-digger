"""Shared positive waveform envelope for terminal and Qt renderers."""
import numpy as np

WAVEFORM_GAMMA = 3.0
# Colour bands of the 4 kHz envelope decode, in 62.5 Hz FFT bins of 16 ms frames:
# lows 62-125 Hz (kick, bass), mids 187-625 Hz, highs 687-2000 Hz.
# ponytail: highs end at the 2 kHz Nyquist of the envelope decode, so hats and air
# never colour a bar; decode the envelope faster if that matters.
BAND_FRAME = 64
BAND_EDGES = (1, 3, 11, 33)


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


def fold_bands(bands, block, frame: int, frames_per_bin: int) -> None:
    """Add one s16le stereo block's low/mid/high energy into the per-bin sums.

    The block's last frames short of a 16 ms frame are left out; the bins still
    line up because ``frame`` is the block's first frame.
    """

    samples = np.frombuffer(block, dtype='<i2', count=len(block) // 4 * 2).astype(np.float32).reshape(-1, 2)
    count = len(samples) // BAND_FRAME
    if not count:
        return
    frames = samples[:count * BAND_FRAME].reshape(count, BAND_FRAME, 2) * np.hanning(BAND_FRAME)[:, None]
    # Channel powers add, so opposite stereo phases do not cancel.
    power = (np.abs(np.fft.rfft(frames, axis=1)) ** 2).sum(axis=2)
    energy = np.stack([power[:, a:b].sum(axis=1) for a, b in zip(BAND_EDGES, BAND_EDGES[1:])], axis=1)
    bins = np.minimum(len(bands) - 1, (frame + np.arange(count) * BAND_FRAME + BAND_FRAME // 2) // frames_per_bin)
    np.add.at(bands, bins, energy)


def band_levels(bands) -> list[list[int]]:
    """Per bin, each band's amplitude against its own loudest bin, 0..255.

    Each band against itself, as Rekordbox does: music has far less energy up
    high, so a shared scale would paint every bar as bass.
    """

    amplitude = np.sqrt(np.asarray(bands, dtype=np.float64))
    loudest = amplitude.max(axis=0, initial=0)
    return np.round(amplitude / np.where(loudest > 0, loudest, 1) * 255).astype(int).tolist()


def column_bands(bands: list, width: int) -> list[list[float]]:
    """Low, mid and high shares (summing to 1) per column; [] without bands."""

    if width <= 0 or not bands:
        return []
    values = np.asarray(bands, dtype=np.float64).reshape(-1, 3)
    per_column = len(values) / width
    columns = []
    for column in range(width):
        start = int(column * per_column)
        mean = values[start:max(start + 1, int((column + 1) * per_column))].mean(axis=0)
        total = mean.sum()
        columns.append([round(float(v), 3) for v in (mean / total if total > 0 else (1 / 3, 1 / 3, 1 / 3))])
    return columns


def drop(samples: list[int], bands: list | None = None) -> float:
    """Where the first drop lands, as a fraction of the track; -1 when none stands out.

    ponytail: a plain energy heuristic, not a structure analysis. The low band
    (kick and bass; the envelope without bands) is smoothed over 1/128 of the
    track and the drop is the steepest rise inside the first window, after the
    first 5 %, where the 1/40 of the track before it sits under 60 % of the
    track's typical loud level and the 1/40 after reaches 85 % of it, rising by
    at least 30 %. A track that never breaks down (no quiet stretch before a
    loud one) has no drop.
    """

    energy = np.asarray([b[0] for b in bands], dtype=np.float64) if bands else np.asarray(samples or [], dtype=np.float64)
    count = len(energy)
    if count < 64 or energy.max() <= 0:
        return -1.0
    smooth = np.convolve(energy, np.ones(max(1, count // 128)) / max(1, count // 128), mode='same')
    loud = np.percentile(smooth, 80)
    if loud <= 0:
        return -1.0
    span = max(2, count // 40)
    for index in range(max(span, count // 20), count - span):
        before, after = smooth[index - span:index].mean(), smooth[index:index + span].mean()
        if before < .6 * loud and after >= .85 * loud and after - before >= .3 * loud:
            rise = np.diff(smooth[index - span // 2:index + span // 2])
            return (index - span // 2 + int(np.argmax(rise)) + 1) / count
    return -1.0

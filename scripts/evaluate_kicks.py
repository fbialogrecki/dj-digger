"""Offline kick evaluation: a local IDMT-SMT-Drums V2 archive, or your own tracks.

Usage: uv run python scripts/evaluate_kicks.py /path/to/IDMT-SMT-DRUMS-V2.zip
       uv run python scripts/evaluate_kicks.py --audio track.mp3 [more files...]
Dataset: https://www.idmt.fraunhofer.de/en/publications/datasets/drums.html
Audio and annotations are not distributed with dj-digger. This script does not
download anything, play audio, write files, or access application state. Dataset
licensing is separate from the application's license.
"""
import argparse
import array
import io
import json
import wave
import zipfile
from pathlib import Path
from time import perf_counter
from xml.etree import ElementTree

from dj_digger.beats import CHANNELS, HOP, SAMPLE_RATE, KickEnergy, _falls


def evaluate(archive: Path) -> dict:
    true_positive = false_positive = missed = 0
    errors = []
    audio_seconds = cpu_seconds = 0.
    with zipfile.ZipFile(archive) as files:
        names = sorted(n for n in files.namelist() if n.endswith('#MIX.wav'))
        if not names:
            raise ValueError('No #MIX.wav drum loops in the archive')
        for name in names:
            annotation = 'annotation_svl/' + Path(name).name.replace('#MIX.wav', '#KD.svl')
            root = ElementTree.fromstring(files.read(annotation))
            truth = [int(point.attrib['frame']) / SAMPLE_RATE for point in root.iter('point')]
            with wave.open(io.BytesIO(files.read(name))) as audio:
                if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                    raise ValueError(f'Expected mono 44.1 kHz s16 audio: {name}')
                mono = array.array('h', audio.readframes(audio.getnframes()))
            # Trailing silence: a kick is judged about 0.2 s after it hits.
            stereo = array.array('h', (value for sample in mono for value in (sample, sample)))
            stereo.extend(bytes(4 * int(.3 * SAMPLE_RATE)))
            started = perf_counter()
            predicted = peaks(analyse(stereo))
            cpu_seconds += perf_counter() - started
            audio_seconds += len(mono) / SAMPLE_RATE
            remaining = set(range(len(truth)))
            for time in predicted:
                index = min(remaining, key=lambda i: abs(truth[i] - time), default=None)
                if index is not None and abs(truth[index] - time) <= .05:
                    remaining.remove(index)
                    true_positive += 1
                    errors.append(abs(truth[index] - time))
                else:
                    false_positive += 1
            missed += len(remaining)
    return dict(loops=len(names), matching_tolerance_ms=50,
                true_positive=true_positive, false_positive=false_positive, missed=missed,
                precision=round(true_positive / max(1, true_positive + false_positive), 4),
                recall=round(true_positive / max(1, true_positive + missed), 4),
                mean_error_ms=round(1000 * sum(errors) / max(1, len(errors)), 2),
                processing_seconds=round(cpu_seconds, 2), audio_seconds=round(audio_seconds, 2))


def analyse(pcm, energy=None) -> list[tuple[float, float]]:
    """(time, level) of every kick level, fed in 50 ms device periods."""
    energy = energy or KickEnergy()
    period = 2205 * CHANNELS
    found = []
    for offset in range(0, len(pcm), period):
        energy.feed(pcm[offset:offset + period], offset / CHANNELS / SAMPLE_RATE)
        first, step, levels = energy.levels(found[-1][0] + HOP / 2 if found else -1, 1e9)
        found += [(first + i * step, level) for i, level in enumerate(levels)]
    return found


def peaks(series, threshold=.5, gap=.06) -> list[float]:
    """Times where the level peaks at or above ``threshold``: the kicks it lights up for."""
    times = []
    for (_, before), (time, level), (_, after) in zip(series, series[1:], series[2:]):
        if level >= threshold and level >= before and level > after and (not times or time - times[-1] >= gap):
            times.append(time)
    return times


def report(path: Path) -> str:
    """Pulses per second and the share of time lit, in every 15 s, with the learned kick."""
    import miniaudio
    pcm = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.SIGNED16,
                                nchannels=CHANNELS, sample_rate=SAMPLE_RATE).samples
    energy = KickEnergy()
    started = perf_counter()
    series = analyse(pcm, energy)
    seconds = len(pcm) / CHANNELS / SAMPLE_RATE
    kicks = peaks(series)
    windows = range(0, int(seconds), 15)
    rates = ' '.join(f'{sum(start <= t < start + 15 for t in kicks) / 15:.1f}' for start in windows)
    lit = ' '.join(f'{sum(start <= t < start + 15 and v > .3 for t, v in series) * HOP / 15:.2f}' for start in windows)
    learned = 'none' if energy.kick_print is None else 'falls' if _falls(energy.kick_print) else 'flat'
    return (f'{path.name}\n  {len(kicks) / seconds:.2f} pulses/s over {seconds:.0f} s, '
            f'{100 * (perf_counter() - started) / seconds:.2f} % of real time, learned kick: {learned}\n'
            f'  pulses/s per 15 s: {rates}\n  share lit per 15 s: {lit}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path, nargs='?')
    parser.add_argument('--audio', type=Path, nargs='+', default=[])
    arguments = parser.parse_args()
    if not arguments.archive and not arguments.audio:
        parser.error('give an IDMT archive or --audio files')
    for audio in arguments.audio:
        print(report(audio))
    if arguments.archive:
        print(json.dumps(evaluate(arguments.archive), indent=2))

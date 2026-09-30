"""Offline kick-onset evaluation against a local IDMT-SMT-Drums V2 archive.

Usage: uv run python scripts/evaluate_kicks.py /path/to/IDMT-SMT-DRUMS-V2.zip
Dataset: https://www.idmt.fraunhofer.de/en/publications/datasets/drums.html
Audio and annotations are not distributed with dj-digger. This script does not
download anything, play audio, or access application state. Dataset licensing
is separate from the application's license.
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

from dj_digger.beats import SAMPLE_RATE, KickDetector


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
            stereo = array.array('h', (value for sample in mono for value in (sample, sample)))
            detector = KickDetector()
            started = perf_counter()
            predicted = [hit.time for offset in range(0, len(stereo), 4410)
                         for hit in detector.feed(stereo[offset:offset + 4410], offset / 88200)]
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    print(json.dumps(evaluate(parser.parse_args().archive), indent=2))

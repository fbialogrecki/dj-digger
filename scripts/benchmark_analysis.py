"""Measure raw analysis, never database/tag overrides or the user's audio files.

uv run --extra analyze python scripts/benchmark_analysis.py --output /tmp/digger-benchmark
Optional: --corpus DIRECTORY --references references.json. References map relative
filenames to {"bpm": 120, "key": "Am", "verified": true}. Unverified references
are retained for comparison but excluded from accuracy statistics.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
import wave
from array import array
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dj_digger.analysis import ALGORITHM, PARAMETERS, analyze_spawned  # noqa: E402
from dj_digger.media import FORMATS  # noqa: E402


def metrics(result, reference):
    measured = {}
    bpm, target = result.get('bpm'), reference.get('bpm')
    if bpm and target:
        error = abs(bpm - target) / target
        measured.update(bpm_error_percent=round(error * 100, 4), bpm_within_2_percent=error <= .02,
                        tempo_half=abs(bpm / target - .5) <= .01,
                        tempo_double=abs(bpm / target - 2) <= .04)
    key, expected = result.get('key'), reference.get('key')
    if key and expected:
        aliases = {'Db': 'C#', 'D#': 'Eb', 'Gb': 'F#', 'G#': 'Ab', 'A#': 'Bb'}
        def canonical(value):
            minor = value.endswith('m')
            note = value[:-1] if minor else value
            return aliases.get(note, note) + ('m' if minor else '')
        measured['key_exact'] = canonical(key) == canonical(expected)
    return measured


def controls(folder):
    folder.mkdir(parents=True, exist_ok=True)
    rate, duration = 22050, 20
    cases = [('silence', None, None), *[(f'pulse-{bpm}', bpm, None) for bpm in (60, 90, 120, 174)],
             ('c-major-cadence', None, 'C'), ('a-minor-cadence', None, 'Am')]
    paths = []
    for name, bpm, key in cases:
        path = folder / f'{name}.wav'
        if path.exists():
            raise FileExistsError(f'Use a fresh output directory: {path}')
        samples = array('h')
        progression = [(60, 64, 67), (65, 69, 72), (67, 71, 74), (60, 64, 67)]
        if key == 'Am':
            progression = [(57, 60, 64), (62, 65, 69), (64, 68, 71), (57, 60, 64)]
        for index in range(rate * duration):
            seconds = index / rate
            value = 0.0
            if bpm:
                phase = seconds % (60 / bpm)
                if phase < .035:
                    value = .7 * math.exp(-phase * 100) * math.sin(2 * math.pi * 1500 * phase)
            elif key:
                chord = progression[int(seconds / 2.5) % len(progression)]
                value = sum(math.sin(2 * math.pi * 440 * 2 ** ((note - 69) / 12) * seconds)
                            for note in chord) * .16
                value *= min(1, (seconds % 2.5) * 20, (2.5 - seconds % 2.5) * 20)
            samples.append(round(value * 32767))
        if sys.byteorder != 'little':
            samples.byteswap()
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            output.writeframes(samples.tobytes())
        paths.append((path, {'bpm': bpm, 'key': key, 'verified': True, 'kind': 'synthetic'}))
    return paths


def summarize(rows):
    summary = {'files': len(rows), 'errors': sum('error' in row for row in rows)}
    for field, metric in [('bpm', 'bpm_within_2_percent'), ('key', 'key_exact')]:
        reference = [row for row in rows if row['reference'].get('verified') is True and row['reference'].get(field)]
        answered = [row for row in reference if row.get('result', {}).get(field)]
        correct = sum(row.get('comparison', {}).get(metric, False) for row in reference)
        summary[field] = {'verified_references': len(reference), 'answered': len(answered),
                          'correct': correct, 'missing_or_failed': len(reference) - len(answered),
                          'accuracy_all_references': correct / len(reference) if reference else None,
                          'accuracy_answered': correct / len(answered) if answered else None}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--corpus', type=Path)
    parser.add_argument('--references', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = args.output / 'benchmark.json'
    if destination.exists():
        parser.error('Choose a fresh output directory; existing reports are not overwritten')
    refs = json.loads(args.references.read_text(encoding='utf-8')) if args.references else {}
    selected = controls(args.output / 'controls')
    if args.corpus:
        selected += [(path, refs.get(path.name, {'verified': False, 'kind': 'user'}))
                     for path in sorted(args.corpus.iterdir())
                     if path.is_file() and not path.is_symlink() and path.suffix.lower() in FORMATS]
    versions = {'python': platform.python_version()}
    for package in ('librosa', 'numpy'):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = 'unavailable'
    report = {'algorithm': ALGORITHM, 'parameters': PARAMETERS, 'versions': versions,
              'references_note': 'Synthetic tonal labels describe intended cadences, not a validated real-music corpus.',
              'rows': []}
    for path, reference in selected:
        row = {'file': path.name, 'reference': reference}
        start = time.monotonic()
        try:
            row['result'] = analyze_spawned(path)
            row['comparison'] = metrics(row['result'], reference)
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
        row['seconds'] = round(time.monotonic() - start, 3)
        report['rows'].append(row)
        report['summary'] = summarize(report['rows'])
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"{path.name}: {row.get('error', row.get('comparison', {}))}", flush=True)
    print(destination)
    return int(report['summary']['errors'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())

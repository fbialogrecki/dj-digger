"""Optional bounded audio analysis. This module does not import librosa at startup."""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from .media import MediaError, digest, pcm_blocks, probe, run, signature
from .models import check_cancelled

ALGORITHM = 'onset-chroma-2'
RATE, FFT, HOP = 22050, 2048, 512
PARAMETERS = {'rate': RATE, 'fft': FFT, 'hop': HOP, 'center': False}
NOTES = ('C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B')
MAJOR_CAMELOT = (8, 3, 10, 5, 12, 7, 2, 9, 4, 11, 6, 1)
MINOR_CAMELOT = (5, 12, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10)


def camelot(key: str) -> str:
    for index, note in enumerate(NOTES):
        if key == note:
            return f'{MAJOR_CAMELOT[index]}B'
        if key == note + 'm':
            return f'{MINOR_CAMELOT[index]}A'
    return key


def analyze_file(path: str, cancel=None, *, temporary_root=None) -> dict:
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise MediaError("Analysis needs the optional extra: pip install 'dj-digger[analyze]'") from exc
    source = Path(path)
    metadata = probe(source, cancel)
    if metadata['channels'] not in (1, 2):
        raise MediaError('Analyze mono or stereo audio; select a downmix explicitly for multichannel files')
    channels = metadata['channels']
    pending = np.empty((0, channels), dtype=np.float64)
    window = np.hanning(FFT + 1)[:-1]
    chroma_filter = librosa.filters.chroma(sr=RATE, n_fft=FFT)
    chroma_sum = np.zeros(12)
    section_sum, sections = np.zeros(12), []
    previous = None
    frame_count = 0
    with tempfile.TemporaryDirectory(prefix='dj-digger-analysis-', dir=temporary_root) as temporary:
        envelope_path = Path(temporary) / 'onset.f32'
        with envelope_path.open('wb') as output:
            for block in pcm_blocks(source, rate=RATE, cancel=cancel):
                check_cancelled(cancel)
                samples = np.frombuffer(block, dtype='<f8').reshape(-1, channels)
                pending = np.concatenate((pending, samples))
                consumed = 0
                while len(pending) - consumed >= FFT:
                    segment = pending[consumed:consumed + FFT]
                    # Average channel POWER, never sum waveforms: anti-phase
                    # stereo must remain audible to the analyzer.
                    power = np.mean(abs(np.fft.rfft(segment * window[:, None], axis=0)) ** 2, axis=1)
                    spectrum = np.log1p(np.sqrt(power))
                    onset = 0. if previous is None else np.maximum(0, spectrum - previous).mean()
                    output.write(np.float32(onset).tobytes())
                    previous = spectrum
                    chroma = chroma_filter @ power
                    total = chroma.sum()
                    if total > 1e-8:
                        chroma /= total
                        chroma_sum += chroma
                        section_sum += chroma
                    frame_count += 1
                    if frame_count % 2584 == 0:  # roughly one minute
                        sections.append(section_sum.tolist())
                        section_sum[:] = 0
                    consumed += HOP
                pending = pending[consumed:]
        check_cancelled(cancel)
        bpm = None
        bpm_reason = 'insufficient_audio'
        if frame_count > 8:
            bpm_reason = 'no_reliable_pulse'
            envelope = np.memmap(envelope_path, dtype='float32', mode='r')
            try:
                if np.max(envelope) > 1e-5 and np.std(envelope) > 1e-5:
                    tempo, beats = librosa.beat.beat_track(onset_envelope=envelope, sr=RATE, hop_length=HOP)
                    if len(beats) >= 4:
                        bpm = round(float(np.asarray(tempo).reshape(-1)[0]), 2)
                        bpm_reason = None
            finally:
                # NumPy/Numba may retain views; Windows requires an explicit
                # release before the temporary directory can be removed.
                envelope._mmap.close()
                del envelope
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    def estimate(chroma):
        if np.std(chroma) < 1e-6:
            return '', 'no_tonal_evidence'
        scores = sorted((float(np.corrcoef(chroma, np.roll(profile, shift))[0, 1]), NOTES[shift] + suffix)
                        for profile, suffix in ((major, ''), (minor, 'm')) for shift in range(12))
        # Conservative abstention heuristics, explicitly not a probability.
        if scores[-1][0] <= .5:
            return '', 'weak_key_match'
        if scores[-1][0] - scores[-2][0] <= .04:
            return '', 'ambiguous_key'
        return scores[-1][1], None

    key, key_reason = estimate(chroma_sum)
    if section_sum.sum():
        sections.append(section_sum.tolist())
    section_keys = [estimate(np.array(section))[0] for section in sections]
    if key and sum(candidate not in ('', key) for candidate in section_keys) > len(section_keys) / 2:
        key = ''
        key_reason = 'conflicting_sections'
    check_cancelled(cancel)
    if signature(source) != metadata['signature']:
        raise MediaError('Audio changed during analysis')
    return {'bpm': bpm, 'key': key, 'bpm_reason': bpm_reason, 'key_reason': key_reason, 'section_keys': section_keys,
            'parameters': PARAMETERS, 'estimated': True, 'signature': metadata['signature'], 'sha256': digest(source, cancel)}


def _child(path, temporary_root):
    os.environ["DJ_DIGGER_ANALYSIS_CHILD"] = "1"
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ[variable] = "1"
    try:
        result = ('ok', analyze_file(path, temporary_root=temporary_root))
    except Exception as exc:
        result = ('error', {'message': str(exc), 'traceback': traceback.format_exc()})
    print(json.dumps(result))


def analyze_spawned(path: Path, cancel=None) -> dict:
    # Textual's stderr capture returns fileno() == -1. multiprocessing's
    # resource tracker passes it to spawnv_passfds, breaking process startup.
    # A fresh interpreter with explicit pipes needs no inherited tracker FDs.
    check_cancelled(cancel)
    with tempfile.TemporaryDirectory(prefix='dj-digger-analysis-job-') as temporary:
        payload = run([sys.executable, '-m', 'dj_digger.analysis', str(path.absolute()), temporary],
                      cancel=cancel, timeout=24 * 3600, process_tree=True)
    status, result = json.loads(payload)
    if status != 'ok':
        import logging
        logging.getLogger(__name__).error('Analyzer process failed:\n%s', result['traceback'])
        raise MediaError(result['message'])
    return result


def analyze_track(db, track, cancel=None):
    record = db.media(track.local_id)
    if record is None:
        raise MediaError('Local file is not indexed')
    values = db.media_values(track.local_id)
    if values.get('signature') == signature(Path(record['path'])) and values.get('algorithm') == ALGORITHM:
        cached = json.loads(values['result_json'])
        if cached.get('parameters') == PARAMETERS and cached.get('sha256') == digest(Path(record['path']), cancel):
            return cached
    result = analyze_spawned(Path(record['path']), cancel)
    if not db.save_analysis(track.local_id, result['signature'], ALGORITHM, result):
        raise MediaError('File changed; stale analysis discarded')
    return result


if __name__ == '__main__':
    _child(sys.argv[1], sys.argv[2])

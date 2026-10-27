"""Optional bounded audio analysis. This module does not import librosa at startup."""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from .media import MediaError, digest, pcm_blocks, probe, run, signature
from .models import check_cancelled

ALGORITHM = 'onset-chroma-3'
RATE, FFT, HOP = 22050, 2048, 512
FPS = RATE / HOP
KICK_HZ = 150  # onset bands: kick/bass below, snares, claps and hats above
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


SEMITONES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def key_names(value) -> tuple[str, str] | None:
    """(classic, Camelot) for the usual spellings of a key found in tags and
    analysis - 'Am', 'A minor', 'Amin', 'A#m', 'Bb', '8A' - or None."""
    import re

    text = str(value or '').strip()
    if len(text) > 12:
        return None
    if match := re.fullmatch(r'(1[0-2]|0?[1-9])\s*([AaBb])', text):
        number, minor = int(match[1]), match[2] in 'Aa'
        index = (MINOR_CAMELOT if minor else MAJOR_CAMELOT).index(number)
        return NOTES[index] + ('m' if minor else ''), f'{number}{"A" if minor else "B"}'
    match = re.fullmatch(r'([A-Ga-g])\s*([#b\u266f\u266d]?)\s*(m|min|minor|maj|major)?', text, re.IGNORECASE)
    if not match or (match[2] == 'B'):
        return None
    index = (SEMITONES[match[1].upper()] + {'#': 1, '\u266f': 1, 'b': -1, '\u266d': -1}.get(match[2], 0)) % 12
    minor = bool(match[3]) and not match[3].lower().startswith('maj')
    classic = NOTES[index] + ('m' if minor else '')
    return classic, camelot(classic)


MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _scan(source: Path, channels: int, envelope_path: Path, cancel):
    """Stream PCM once: write the low/high onset envelopes and sum chroma overall and per section."""
    import librosa
    import numpy as np

    pending = np.empty((0, channels), dtype=np.float64)
    window = np.hanning(FFT + 1)[:-1]
    frequencies = np.fft.rfftfreq(FFT, 1 / RATE)
    # Bands, not a mean over linear bins: the kick spans ~1% of the bins and
    # would vanish under snares and hats, leaving only half-tempo backbeats.
    low_band, high_band = (frequencies >= 30) & (frequencies < KICK_HZ), frequencies >= KICK_HZ
    chroma_filter = librosa.filters.chroma(sr=RATE, n_fft=FFT)
    chroma_sum = np.zeros(12)
    section_sum, sections = np.zeros(12), []
    previous = None
    frame_count = 0
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
                rise = np.zeros_like(spectrum) if previous is None else np.maximum(0, spectrum - previous)
                output.write(np.array((rise[low_band].mean(), rise[high_band].mean()), dtype='<f4').tobytes())
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
    return chroma_sum, section_sum, sections, frame_count


def _autocorrelation(values):
    import numpy as np

    second = round(FPS)
    values = values - np.convolve(values, np.ones(second) / second, 'same')  # drop loudness drift
    result = np.fft.irfft(abs(np.fft.rfft(values, 2 * len(values))) ** 2)[:len(values)]
    result /= result[0] or 1  # a silent band stays all zero
    # Onsets rarely sit on frame boundaries: let each lag see its neighbours' peak.
    return np.maximum(np.maximum(result, np.roll(result, 1)), np.roll(result, -1))


def _pick_tempo(low, high):
    """BPM in the DJ range from per-frame kick/bass and snare/clap/hat onset strength, or None."""
    import numpy as np

    low, high = (band / band.std() if band.std() > 0 else band for band in (low, high))
    full, bass, bright = _autocorrelation(low + high), _autocorrelation(low), _autocorrelation(high)

    def at(curve, bpm):  # the curve at one beat's lag, between frames too
        return np.interp(60 * FPS / bpm, np.arange(len(curve)), curve, right=0)

    # Comb: mean autocorrelation over two bars of whole beats. The tempo's metrical
    # family outscores 3:2 relatives (116 vs 174), and long lags resolve the tempo
    # finer than one frame (a frame is 172.3 vs 184.6 BPM at 174). No tempo prior.
    tempos = np.arange(60, 200, .05)
    comb = np.mean([at(full, tempos / beats) for beats in range(1, 9)], axis=0)
    if comb.max() < .1:
        return None
    best = tempos[np.argmax(comb)]

    # Half tempo shares every comb peak, so the octave needs other evidence:
    # snares and claps mark the backbeat, repeating in the bright band at two
    # beats more than at one. Hats repeat at both lags and cancel; the kick
    # band's own difference removes what kick clicks add to the bright band.
    def backbeat(bpm):
        return at(bright, bpm / 2) - at(bright, bpm) - at(bass, bpm / 2) + at(bass, bpm)

    # ponytail: rhythm alone cannot tell half-time DnB from 87 BPM hip-hop; a user
    # BPM-range preference is the upgrade path if real crates disagree.
    choice = max((bpm for bpm in (best / 2, best, best * 2) if 60 <= bpm <= 200), key=backbeat)
    if backbeat(choice) < backbeat(best) + .1:
        choice = best
    while choice < 78:  # half-time feel (dubstep, trap) is labelled at the full tempo
        choice *= 2
    return float(choice)


def _estimate_bpm(envelope_path: Path, frame_count: int):
    import numpy as np

    bpm = None
    bpm_reason = 'insufficient_audio'
    if frame_count > 8:
        bpm_reason = 'no_reliable_pulse'
        envelope = np.fromfile(envelope_path, dtype='<f4').astype(np.float64).reshape(-1, 2)
        if np.max(envelope) > 1e-5 and np.std(envelope) > 1e-5:
            tempo = _pick_tempo(envelope[:, 0], envelope[:, 1])
            if tempo is not None:
                bpm = round(tempo, 2)
                bpm_reason = None
    return bpm, bpm_reason


def _match_key(chroma):
    import numpy as np

    if np.std(chroma) < 1e-6:
        return '', 'no_tonal_evidence'
    scores = sorted((float(np.corrcoef(chroma, np.roll(profile, shift))[0, 1]), NOTES[shift] + suffix)
                    for profile, suffix in ((MAJOR_PROFILE, ''), (MINOR_PROFILE, 'm')) for shift in range(12))
    # Conservative abstention heuristics, explicitly not a probability.
    if scores[-1][0] <= .5:
        return '', 'weak_key_match'
    if scores[-1][0] - scores[-2][0] <= .04:
        return '', 'ambiguous_key'
    return scores[-1][1], None


def _estimate_key(chroma_sum, section_sum, sections):
    """Whole-track key, dropped when most per-section keys disagree with it."""
    import numpy as np

    key, key_reason = _match_key(chroma_sum)
    if section_sum.sum():
        sections.append(section_sum.tolist())
    section_keys = [_match_key(np.array(section))[0] for section in sections]
    if key and sum(candidate not in ('', key) for candidate in section_keys) > len(section_keys) / 2:
        key = ''
        key_reason = 'conflicting_sections'
    return key, key_reason, section_keys


def analyze_file(path: str, cancel=None, *, temporary_root=None) -> dict:
    try:
        import librosa  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        raise MediaError("Analysis needs the optional extra: pip install 'dj-sc-digger[analyze]'") from exc
    source = Path(path)
    metadata = probe(source, cancel)
    if metadata['channels'] not in (1, 2):
        raise MediaError('Analyze mono or stereo audio; select a downmix explicitly for multichannel files')
    with tempfile.TemporaryDirectory(prefix='dj-digger-analysis-', dir=temporary_root) as temporary:
        envelope_path = Path(temporary) / 'onset.f32'
        chroma_sum, section_sum, sections, frame_count = _scan(source, metadata['channels'], envelope_path, cancel)
        check_cancelled(cancel)
        bpm, bpm_reason = _estimate_bpm(envelope_path, frame_count)
    key, key_reason, section_keys = _estimate_key(chroma_sum, section_sum, sections)
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
    # A captured stderr can return fileno() == -1. multiprocessing's
    # resource tracker passes it to spawnv_passfds, breaking process startup.
    # A fresh interpreter with explicit pipes needs no inherited tracker FDs.
    check_cancelled(cancel)
    with tempfile.TemporaryDirectory(prefix='dj-digger-analysis-job-') as temporary:
        from .bundled import tool
        helper = tool('dj-digger-analysis')
        command = [str(helper)] if helper else [sys.executable, '-m', 'dj_digger.analysis']
        payload = run([*command, str(path.absolute()), temporary],
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

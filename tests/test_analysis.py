"""Algorithm invariants; these do not claim accuracy on real DJ repertoire."""
import importlib.util
import shutil
import subprocess

import pytest

from dj_digger.analysis import analyze_file, analyze_spawned, camelot

pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg') or importlib.util.find_spec('librosa') is None,
                                reason='FFmpeg and analyze extra required')


def make_audio(path, filter):
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', filter, '-t', '5',
                    '-c:a', 'pcm_s16le', str(path)], check=True)
    return path


def test_silence_abstains_and_spawn_returns(tmp_path):
    path = make_audio(tmp_path / 'silence.wav', 'anullsrc=r=22050:cl=stereo')
    result = analyze_spawned(path)
    assert result['bpm'] is None and result['key'] == ''
    assert result['key_reason'] == 'no_tonal_evidence'
    assert result['bpm_reason'] == 'no_reliable_pulse'
    assert result['estimated']


def test_repeated_spawn_from_worker_thread(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = make_audio(tmp_path / 'batch.wav', 'anullsrc=r=22050:cl=stereo')
    with ThreadPoolExecutor(max_workers=1) as executor:
        for _ in range(3):
            assert executor.submit(analyze_spawned, path).result(timeout=60)['estimated']


def test_cancelling_analyzer_reaps_process_and_cleans_workspace(tmp_path, monkeypatch):
    import threading

    from dj_digger import media
    from dj_digger.models import Cancelled

    path = make_audio(tmp_path / 'cancel.wav', 'anullsrc=r=22050:cl=stereo')
    processes = []
    original_register = media.register

    def record(process):
        processes.append(process)
        original_register(process)

    monkeypatch.setattr(media, 'register', record)
    cancel = threading.Event()
    timer = threading.Timer(.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            analyze_spawned(path, cancel)
    finally:
        timer.cancel()
        timer.join()
    assert len(processes) == 1
    assert processes[0].poll() is not None
    from pathlib import Path
    assert not Path(processes[0].args[-1]).exists()


def test_antiphase_stereo_and_arbitrary_block_boundaries(tmp_path, monkeypatch):
    import numpy as np

    from dj_digger import analysis
    from dj_digger.media import pcm_blocks
    path = make_audio(tmp_path / 'chord.wav', 'aevalsrc=0.1*(sin(2*PI*261.626*t)+sin(2*PI*329.628*t)+sin(2*PI*391.995*t)):s=22050')
    blocks = b''.join(pcm_blocks(path, rate=analysis.RATE))
    mono = np.frombuffer(blocks, dtype='<f8')
    stereo_path = make_audio(tmp_path / 'stereo.wav', 'anullsrc=r=22050:cl=stereo')

    def run(chunk_frames):
        values = np.column_stack([mono, -mono]).astype('<f8').tobytes()
        monkeypatch.setattr(analysis, 'pcm_blocks', lambda *args, **kwargs: (values[index:index + chunk_frames * 16] for index in range(0, len(values), chunk_frames * 16)))
        return analyze_file(str(stereo_path))

    reference, streaming = run(len(mono)), run(731)
    # A single triad is not a labelled musical key. Check stream and channel invariance.
    assert reference['key'] == streaming['key']
    assert reference['key']
    assert reference['bpm'] == streaming['bpm']
    assert camelot('C') == '8B' and camelot('Am') == '8A'


def drum_loop(path, bpm, kicks, snares, hats, snare_level=.9, seconds=30):
    """Mono WAV of 16-step patterns: a falling kick with a click, a noisy snare, hats and a sub-bass."""
    import wave

    import numpy as np

    rate, rng = 22050, np.random.default_rng(7)
    t = np.arange(rate // 4) / rate
    kick = np.sin(2 * np.pi * np.cumsum(50 + 100 * np.exp(-30 * t)) / rate) * np.exp(-12 * t)
    kick[:110] += rng.standard_normal(110) * np.exp(-np.arange(110) / 25)
    snare = (rng.standard_normal(len(t)) * .8 + .5 * np.sin(2 * np.pi * 190 * t)) * np.exp(-18 * t) * snare_level
    hat = np.diff(rng.standard_normal(882), prepend=0) * np.exp(-90 * t[:882]) * .15
    step = 60 / bpm / 4
    seconds_axis = np.arange(rate * seconds) / rate
    bass_note = np.where(seconds_axis % (32 * step) < 16 * step, 41.2, 49.0)
    audio = .3 * np.sin(2 * np.pi * np.cumsum(bass_note) / rate)
    for index in range(int(seconds / step)):
        start = round(index * step * rate)
        for sound, pattern in ((kick, kicks), (snare, snares), (hat, hats)):
            if pattern[index % 16] == 'x':
                piece = sound[:len(audio) - start]
                audio[start:start + len(piece)] += piece
    audio *= .8 / abs(audio).max()
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        output.writeframes((audio * 32767).astype('<i2').tobytes())
    return path


@pytest.mark.parametrize('bpm, kicks, snares, hats, snare_level', [
    # Drum & bass two-step: snare on 2 and 4, kicks on 1 and the "and" of 3, 16th hats.
    # The previous estimator heard the snares' half tempo, 86 BPM.
    (174, 'x.........x.....', '....x.......x...', 'x' * 16, .9),
    # Kick-heavy mix with kicks on 1, the "and" of 2 and the "and" of 3: previously
    # the dotted-quarter kick spacing won under a 120 BPM prior, 117 BPM.
    (174, 'x.....x...x.....', '....x.......x...', 'x' * 16, .4),
    # Regressions: four-on-the-floor house and a hip-hop beat keep their tempo.
    (124, 'x...x...x...x...', '....x.......x...', '..x...x...x...x.', .9),
    (90, 'x.......x.x.....', '....x.......x...', 'x.x.x.x.x.x.x.x.', .9),
])
def test_tempo_is_the_dj_tempo_not_a_half_or_two_thirds_relative(tmp_path, bpm, kicks, snares, hats, snare_level):
    result = analyze_file(str(drum_loop(tmp_path / 'loop.wav', bpm, kicks, snares, hats, snare_level)))
    assert result['bpm'] == pytest.approx(bpm, abs=2)
    assert type(result['bpm']) is float

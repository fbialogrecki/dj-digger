import array
import threading
import time

from dj_digger.local_audio import LEASES, LocalSource


def test_underrun_is_silence_not_eof_and_seek_discards_buffer(tmp_path, monkeypatch):
    from dj_digger import local_audio
    path = tmp_path / 'test.wav'
    path.touch()
    release = threading.Event()
    calls = []

    def blocks(path, *, seek, cancel, **kwargs):
        calls.append(seek)
        while not release.wait(.01):
            if cancel.is_set():
                return
        if not cancel.is_set():
            yield array.array('h', [1000 if seek == 0 else 2000] * 8192).tobytes()

    monkeypatch.setattr(local_audio, 'pcm_blocks', blocks)
    source = LocalSource(path)
    try:
        assert list(source.take(10)) == [0] * 20
        assert source.last_frames == 0
        for index in range(100):
            source.restart(index + 1)
        assert sum(thread.name == 'local-audio-decoder' for thread in threading.enumerate()) == 1
        release.set()
        deadline = time.monotonic() + 3
        while source.last_frames == 0 and time.monotonic() < deadline:
            chunk = source.take(10)
            time.sleep(.01)
        assert max(chunk) == 2000
        assert path in LEASES
    finally:
        source.join()
    assert path not in LEASES


def test_seeks_inside_retained_audio_need_no_decoder(tmp_path, monkeypatch):
    from dj_digger import local_audio
    path = tmp_path / 'test.wav'
    path.touch()
    seeks = []

    def blocks(path, *, seek, cancel, **kwargs):
        seeks.append(seek)
        for second in range(10):  # ten seconds of audio, each second stamped with its time
            if cancel.is_set():
                return
            yield array.array('h', [int(seek) * 100 + second] * (44100 * 2)).tobytes()

    def settled(source):
        deadline = time.monotonic() + 3
        while not source._eof and time.monotonic() < deadline:
            time.sleep(.01)
        assert source._eof

    def first(source):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            chunk = source.take(10)
            if source.last_frames:
                return chunk[0]
            time.sleep(.01)
        raise AssertionError('no audio arrived')

    monkeypatch.setattr(local_audio, 'pcm_blocks', blocks)
    source = LocalSource(path)
    try:
        settled(source)
        assert source.take(10)[0] == 0
        source.restart(3.5)
        assert source.take(10)[0] == 3 and source.decodes == 1
        source.restart(1)
        assert source.take(10)[0] == 1 and seeks == [0.0]
        source.restart(30)  # beyond what is held: a decoder from there
        settled(source)
        assert source.take(10)[0] == 3000 and seeks == [0.0, 30]
        source.restart(2)  # dropped when the head jumped forward
        settled(source)
        assert source.take(10)[0] == 200 and seeks == [0.0, 30, 2]
    finally:
        source.join()
    # A small cap trims the audio far behind the head instead of stalling the decoder.
    seeks.clear()
    source = LocalSource(path, retained=4 * 44100 * 4)
    try:
        for second in range(10):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                chunk = source.take(44100)
                if source.last_frames == 44100:
                    break
                time.sleep(.01)
            assert chunk[0] == second, second
        assert seeks == [0.0]
        source.restart(8.5)
        assert source.take(10)[0] == 8 and seeks == [0.0]
        source.restart(1)
        assert first(source) == 100 and seeks == [0.0, 1]
    finally:
        source.join()

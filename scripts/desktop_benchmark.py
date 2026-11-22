"""Measure the real Qt window with a disposable library. No account or audio device required."""
import asyncio
import json
import os
import statistics
import tempfile
import time
import wave
from pathlib import Path


def stats(samples):
    return dict(first_ms=round(samples[0], 2), median_ms=round(statistics.median(samples[1:]), 2),
                p95_ms=round(statistics.quantiles(samples[1:], n=100, method='inclusive')[94], 2))


def measure(root):
    import shiboken6
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtTest import QSignalSpy

    from dj_digger.config import AppConfig
    from dj_digger.crate_models import CrateRecord
    from dj_digger.db import database
    from dj_digger.gui.bridge import Bridge
    from dj_digger.models import Track

    QQuickStyle.setStyle('Basic')
    app = QGuiApplication([])
    app.setQuitOnLastWindowClosed(False)
    config = AppConfig()
    config.onboarded = True
    config.scan_directories = [str(root)]
    config.save()
    tracks = [Track(f'Track {i}', f'https://soundcloud.com/artist/track-{i}', artist='Artist', id=i)
              for i in range(1, 10001)]
    db = database()
    db.save_crate(CrateRecord('fixture', 'Fixture', tracks).to_json())
    engine = QQmlApplicationEngine()
    bridge = Bridge(engine, home_path=root)
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', 'en')
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / 'dj_digger/gui/qml/Main.qml')))
    window = engine.rootObjects()[0]

    def until(predicate):
        start = time.perf_counter()
        while not predicate():
            app.processEvents()
            if time.perf_counter() - start > 15:
                raise TimeoutError('Desktop benchmark timed out')
            time.sleep(.001)

    def frame():
        painted = QSignalSpy(window.frameSwapped)
        window.update()
        if not painted.wait(3000):
            raise TimeoutError('Window did not present a frame')

    try:
        until(lambda: bridge._ready)
        opened, marked = [], []
        for i in range(21):
            generation = bridge.view['generation']
            start = time.perf_counter()
            bridge.load('fixture')
            until(lambda: bridge.view['generation'] > generation and bridge.table.rowCount() == len(tracks))
            frame()
            opened.append(1000 * (time.perf_counter() - start))
            bridge.table.select(0)
            status = 'got' if i % 2 == 0 else 'new'
            start = time.perf_counter()
            bridge.mark(status)
            until(lambda: bridge.table.visible[0]['status'] == status)
            frame()
            marked.append(1000 * (time.perf_counter() - start))
        music = root / 'Music'
        music.mkdir(exist_ok=True)
        source = music / 'one.wav'
        with wave.open(str(source), 'wb') as wav:
            wav.setparams((2, 2, 44100, 0, 'NONE', ''))
            wav.writeframes(bytes(44100 * 4))
        folder_times, tags, audio = [], [], []
        for _ in range(21):
            start = time.perf_counter()
            generation = bridge.view['generation']
            bridge.openFolder(str(music))
            until(lambda: bridge.view['generation'] > generation and bridge.table.rowCount() == 1)
            folder_times.append(1000 * (time.perf_counter() - start))
            until(lambda: bridge.table.visible[0]['duration'] > 0)
            tags.append(1000 * (time.perf_counter() - start))
            start = time.perf_counter()
            prepared = bridge.backend.prepare_track(bridge.backend.rows[0].track)
            local = asyncio.run_coroutine_threadsafe(prepared, bridge.backend.loop).result(timeout=15)
            try:
                while not local.source.last_frames:
                    local.source.take(1024)
                    if time.perf_counter() - start > 15:
                        raise TimeoutError('Decoder did not produce frames')
                    time.sleep(.001)
                audio.append(1000 * (time.perf_counter() - start))
            finally:
                local.source.join()
        print(json.dumps(dict(platform=app.platformName(), playlist_to_frame=stats(opened),
                              mark_to_frame=stats(marked), folder_to_rows=stats(folder_times),
                              folder_to_tag=stats(tags), prepare_to_first_pcm=stats(audio)), indent=2))
    finally:
        bridge._folders.stop()
        bridge.backend.close()
        until(lambda: not bridge.backend.thread.is_alive())
        shiboken6.delete(engine)
        db.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        for name in ('HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'):
            os.environ[name] = directory
        for name in ('CONFIG', 'DATA', 'CACHE', 'STATE'):
            os.environ[f'XDG_{name}_HOME'] = str(Path(directory) / name)
        os.environ.pop('SOUNDCLOUD_OAUTH_TOKEN', None)
        from unittest.mock import patch
        with patch('dj_digger.auth._keyring', return_value=None):
            measure(Path(directory))

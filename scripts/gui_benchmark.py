"""Synthetic table benchmark; no account, database, media or network access."""
import json
import os
import statistics
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication

from dj_digger.gui.model import TrackModel


def main():
    app = QGuiApplication([])
    model = TrackModel()
    rows = [dict(key=str(i), title=f'Track {i:05}', artist='Synthetic artist', genre='House',
                 bpm=100+i % 50, keySignature='8A', year=2026, label='', duration=180000,
                 status='new', stores='bandcamp', search=f'House track {i:05}') for i in range(10000)]
    timings = {'replace_ms': [], 'sort_ms': [], 'filter_ms': []}
    for _ in range(7):
        for name, action in [('replace_ms', lambda: model.replace(rows)),
                             ('sort_ms', lambda: model.sortBy(3)),
                             ('filter_ms', lambda: model.filter('track 001', '', False))]:
            start = time.perf_counter()
            action()
            timings[name].append((time.perf_counter() - start) * 1000)
        model.filter('', '', False)
        app.processEvents()
    print(json.dumps({'rows': len(rows), 'median': {k: round(statistics.median(v), 2) for k, v in timings.items()},
                      'scope': 'Qt table model only; excludes QML rendering, audio and browser RAM'}, indent=2))


if __name__ == '__main__':
    main()

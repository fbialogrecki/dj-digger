"""Offline contracts for the optional Qt desktop and its worker boundary."""
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QGuiApplication

from dj_digger.config import AppConfig
from dj_digger.gui.backend import Backend
from dj_digger.gui.model import TrackModel
from dj_digger.services.runtime import ApplicationServices
from dj_digger.state import TrackState


@pytest.fixture(scope='module')
def app():
    from PySide6.QtQuickControls2 import QQuickStyle
    QQuickStyle.setStyle('Basic')
    return QGuiApplication.instance() or QGuiApplication([])


def row(key, title, bpm=120):
    return dict(key=key, title=title, artist='', genre='', bpm=bpm, keySignature='', year='',
                label='', duration=60000, status='new', stores='bandcamp', search=title)


def test_table_selection_survives_sort_and_status_updates(app):
    model = TrackModel()
    model.replace([row('b', 'Beta'), row('a', 'Alpha')])
    model.select(0)
    model.sortBy(2)
    assert model.keys() == ['b']
    assert model.data(model.index(0, 2)) == 'Alpha'
    assert model.data(model.index(1, 1), Qt.UserRole + 1)
    updated = [row('b', 'Beta'), row('a', 'Alpha')]
    updated[0]['status'] = 'got'
    model.update_rows(updated)
    assert model.keys() == ['b']
    model.filter('', '', True)
    assert model.rowCount() == 1
    assert model.keys() == []
    assert model.thread() == QThread.currentThread()


def test_table_literal_text_and_numeric_sort(app):
    model = TrackModel()
    model.replace([row('1', '<img src="http://127.0.0.1/private">', 99), row('2', 'Other', 120)])
    model.sortBy(4)
    assert model.data(model.index(0, 2)).startswith('<img')
    model.filter('other', 'bandcamp', False)
    assert model.rowCount() == 1
    assert model.visible[0]['key'] == '2'


def wait_event(events, kind):
    while True:
        received, value = events.get(timeout=10)
        if received == 'error':
            pytest.fail(value['text'])
        if received == kind:
            return value


@pytest.fixture
def backend(tmp_path, monkeypatch):
    for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        monkeypatch.setenv(name, str(tmp_path / name))
    events = queue.Queue()
    def factory():
        return ApplicationServices(state=TrackState(tmp_path / 'test.sqlite'), config=AppConfig(tmp_path / 'config.json'))
    backend = Backend(lambda kind, value: events.put((kind, value)), factory)
    backend.submit('start')
    wait_event(events, 'ready')
    yield backend, events
    if backend.thread.is_alive():
        backend.close()
        backend.thread.join(timeout=10)
    assert not backend.thread.is_alive()


def test_backend_empty_folder_and_shutdown(backend, tmp_path):
    worker, events = backend
    folder = tmp_path / 'music'
    folder.mkdir()
    worker.submit('folder', {'path': str(folder)})
    view = wait_event(events, 'view')
    assert view['rows'] == []
    assert view['title'] == str(folder)
    worker.close()
    wait_event(events, 'closed')
    worker.thread.join(timeout=2)
    assert not worker.thread.is_alive()


def test_stale_folder_results_do_not_replace_new_view(backend, tmp_path):
    worker, events = backend
    entered, release = threading.Event(), threading.Event()
    def page(folder, offset):
        if folder.name == 'old':
            entered.set()
            assert release.wait(5)
        return [], [], 0, []
    worker.local.page = page
    worker.submit('folder', {'path': str(tmp_path / 'old')})
    assert entered.wait(5)
    worker.submit('folder', {'path': str(tmp_path / 'new')})
    view = wait_event(events, 'view')
    assert view['title'].endswith('new')
    release.set()
    # Closing joins pending workers, so a late result cannot escape the assertion.
    worker.close()
    wait_event(events, 'closed')
    while not events.empty():
        kind, value = events.get_nowait()
        assert kind != 'view' or value['title'].endswith('new')


def test_shutdown_dismisses_pending_confirmation(backend):
    worker, events = backend
    worker.submit('dig')
    question = wait_event(events, 'question')
    worker.close()
    wait_event(events, 'closed')
    assert question['id'] not in worker.questions


def test_gui_starts_and_closes_without_real_user_state(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               LOCALAPPDATA=str(tmp_path / 'local'), APPDATA=str(tmp_path / 'roaming'),
               QT_QPA_PLATFORM='offscreen')
    for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
        env[name] = str(tmp_path / name)
    command = [sys.executable, '-c', 'from dj_digger.gui import main; raise SystemExit(main())', '--smoke-test']
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'TypeError' not in result.stderr
    assert 'ReferenceError' not in result.stderr
    assert json.loads((Path(env['XDG_CONFIG_HOME']) / 'dj-digger/gui.json').read_text())['theme'] == 'system'


def test_finished_progress_emits_redraw_even_if_row_data_is_unchanged(app):
    model = TrackModel()
    rows = [row('1', 'Track')]
    model.replace(rows)
    redraws = []
    model.dataChanged.connect(lambda *args: redraws.append(args))
    model.update_progress({'1': 0.5})
    first, last, *_ = redraws[-1]
    assert (first.column(), last.column()) == (0, model.columnCount()-1)
    assert model.data(model.index(0, 0)) == '50%'
    redraws.clear()
    model.update_rows(rows)
    assert redraws
    assert model.progress == {}


def test_cli_does_not_import_optional_qt():
    result = subprocess.run([sys.executable, '-c',
                             'import sys; import dj_digger.cli; assert not any(k.startswith("PySide6") for k in sys.modules)'],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def online_rows(count):
    from dj_digger.models import LinkRecord, Track
    from dj_digger.rows import Row
    result = []
    for i in range(count):
        track = Track(f'Track {i}', f'https://soundcloud.com/example/track{i}', id=i+1)
        records = [LinkRecord(store, track, f'https://{store}.com/track/{i}', '')
                   for store in ('bandcamp', 'beatport')]
        result.append(Row(i, track, records))
    return result


def test_open_uses_only_selected_store(backend):
    worker, events = backend
    worker.rows = online_rows(1)
    opened = []
    worker.services.opening.open_one = lambda url, *args: opened.append(url)
    worker.submit('open', {'keys': ['1'], 'generation': 0, 'store': 'beatport'})
    wait_event(events, 'rows')
    assert opened == ['https://beatport.com/track/0']


def test_cancel_bulk_open_has_no_browser_side_effect(backend):
    worker, events = backend
    worker.rows = online_rows(21)
    opened = []
    worker.services.opening.open_one = lambda url, *args: opened.append(url)
    # Twenty links open without a prompt, as in the TUI; the twenty-first asks first.
    worker.submit('open', {'keys': [str(i) for i in range(1, 22)], 'generation': 0})
    question = wait_event(events, 'question')
    assert question['fields'][0]['kind'] == 'log' and question['ok'] == 'Open'
    worker.answer(question['id'], None)
    assert wait_event(events, 'message')['text'] == 'Cancelled'
    assert not opened


@pytest.mark.parametrize('title,base_name,subfolder,count,local', [
    ('Warehouse / Session: 01', 'Downloads', 'Warehouse Session 01', 1, False),
    ('Warehouse / Session: 01', 'Downloads', 'Warehouse Session 01', 2, False),
    ('Warehouse / Session: 01', 'Downloads', 'Warehouse Session 01', 1, True),
    ('Warehouse Session 01', 'warehouse session 01', '', 1, False),
    ('', 'Downloads', '', 1, False),
])
def test_download_uses_playlist_folder(backend, tmp_path, title, base_name, subfolder, count, local):
    from dj_digger.crate_models import CrateRecord

    worker, events = backend
    worker.rows = online_rows(count)
    worker.record = CrateRecord(source='playlist', title=title)
    base = tmp_path / base_name
    worker.services.config.download_directory = str(base)
    worker.services.config.first_run = False
    for row in worker.rows:
        row.track.downloadable = True
        row.track.has_downloads_left = True
        if local:
            source = tmp_path / 'existing.wav'
            source.write_bytes(b'audio')
            row.track.local_path = str(source)

    class Client:
        def download_track(self, track, directory, **kwargs):
            assert not local
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f'{track.id}.wav'
            path.write_bytes(b'audio')
            return path

        def close(self):
            pass

    worker.services._client = Client()
    keys = [row.track.key for row in worker.rows]
    worker.submit('download', {'keys': keys, 'generation': worker.generation})
    wait_event(events, 'rows')
    expected = base / subfolder if subfolder else base
    paths = worker.services.state.db.all_track_local_files()
    for key in keys:
        assert worker.services.state.get(key) == 'got'
        assert Path(paths[key]).parent == expected
        assert Path(paths[key]).read_bytes() == b'audio'


def test_form_reopens_with_entered_values_until_valid(backend, tmp_path):
    worker, events = backend
    # Settings validation keeps what was typed instead of dropping the dialog.
    worker.submit('settings', {})
    question = wait_event(events, 'question')
    browser = next(f for f in question['fields'] if f['name'] == 'browser')
    assert browser['kind'] == 'choice' and browser['options'][0] == ['', 'System default']
    answer = {f['name']: f['value'] for f in question['fields']}
    answer.update(user_email='not-an-email', user_name='Digger', download_directory=str(tmp_path))
    worker.answer(question['id'], answer)
    retry = wait_event(events, 'question')
    assert retry['error'] == 'Enter a valid email' and retry['title'] == 'Settings'
    assert {f['name']: f['value'] for f in retry['fields']}['user_name'] == 'Digger'
    worker.answer(retry['id'], dict(answer, user_email='dj@example.com'))
    wait_event(events, 'dismiss')
    wait_event(events, 'sidebar')
    assert worker.services.config.user_email == 'dj@example.com'


def test_model_counts_store_summary_and_status_labels(app):
    model = TrackModel()
    rows = [row('a', 'Alpha'), row('b', 'Beta'), row('c', 'Gamma')]
    rows[1]['status'] = 'got'
    rows[2]['stores'] = 'beatport, bandcamp'
    model.replace(rows)
    assert model.summary() == dict(visible=3, total=3, got=1, skipped=0, selected=0)
    assert model.store_counts() == [dict(name='bandcamp', count=3), dict(name='beatport', count=1)]
    assert model.data(model.index(1, 0)) == '\u2713 Got'
    assert model.data(model.index(0, 4)) == '120'
    model.selectAll()
    assert model.summary()['selected'] == 3
    model.clearSelection()
    assert model.keys() == []


def test_summary_overwrite_needs_separate_confirmation(backend, tmp_path):
    worker, events = backend
    worker.rows = online_rows(1)
    path = tmp_path / 'summary.json'
    path.write_text('keep existing content', encoding='utf-8')
    worker.submit('summary', {'keys': ['1'], 'generation': 0})
    question = wait_event(events, 'question')
    worker.answer(question['id'], {'path': str(path), 'format': 'json'})
    confirmation = wait_event(events, 'question')
    assert confirmation['title'] == 'Replace existing file'
    worker.answer(confirmation['id'], None)
    wait_event(events, 'message')
    assert path.read_text(encoding='utf-8') == 'keep existing content'


def test_local_waveform_arrives_after_pause_without_blocking_play(backend, tmp_path, monkeypatch):
    import array
    import asyncio
    import math
    import shutil
    import wave
    from types import SimpleNamespace

    from dj_digger import local_audio
    from dj_digger.models import Track
    from dj_digger.rows import Row

    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg is not installed')
    path = tmp_path / 'local.wav'
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
        samples = array.array('h', (int(10000 * math.sin(i * math.tau * 440 / 22050)) for i in range(22050)))
        if sys.byteorder != 'little':
            samples.byteswap()
        output.writeframes(samples.tobytes())
    worker, events = backend
    track = Track('Local waveform', '', local_path=str(path), local_id='test')
    worker.rows = [Row(0, track, [])]
    device = SimpleNamespace(start=lambda generator: None)
    monkeypatch.setattr(worker.services.player, '_device_for', lambda *args: device)
    entered, release = threading.Event(), threading.Event()
    original = local_audio.waveform

    def delayed(source, cancel):
        entered.set()
        assert release.wait(5)
        return original(source, cancel)

    monkeypatch.setattr(local_audio, 'waveform', delayed)
    play = asyncio.run_coroutine_threadsafe(worker.action_play({'generation': 0, 'keys': [track.key]}), worker.loop)
    try:
        assert entered.wait(5)
        # Decoding the envelope must not hold up play/automatic-next orchestration.
        play.result(timeout=2)
        pause = asyncio.run_coroutine_threadsafe(worker.action_transport({'operation': 'toggle'}), worker.loop)
        pause.result(timeout=2)
    finally:
        release.set()
    snapshot = wait_event(events, 'audio')
    while not snapshot.get('waveform'):
        snapshot = wait_event(events, 'audio')
    assert not snapshot['playing']
    assert len(snapshot['waveform']) == 1024
    assert max(snapshot['waveform']) > 0


def test_stopped_local_waveform_is_cancelled_and_cannot_restore_audio(backend, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from dj_digger import local_audio
    from dj_digger.models import Track
    from dj_digger.player import Loaded
    from dj_digger.services.playback import Stream

    worker, events = backend
    loaded = Loaded(Track('Old local', '', local_path='synthetic.wav'), Stream('synthetic.wav', duration=10))
    worker.services.player._loaded = loaded
    entered, release = threading.Event(), threading.Event()
    cancel = worker.waveform_cancel
    def delayed(path, stop):
        assert stop is cancel
        entered.set()
        assert release.wait(5)
        return [123]  # Even a late worker ignoring cancellation must be discarded.
    monkeypatch.setattr(local_audio, 'waveform', delayed)
    future = asyncio.run_coroutine_threadsafe(worker.local_waveform(loaded, cancel), worker.loop)
    try:
        assert entered.wait(5)
        asyncio.run_coroutine_threadsafe(worker.action_transport({'operation': 'stop'}), worker.loop).result(timeout=2)
        assert cancel.is_set()
        assert wait_event(events, 'audio') == {}
        replacement = SimpleNamespace(track=loaded.track, waveform=[456])
        worker.services.player._loaded = replacement
    finally:
        release.set()
    future.result(timeout=5)
    assert replacement.waveform == [456]
    assert events.empty()
    worker.services.player._loaded = None


def test_qml_home_tree_and_one_sided_waveform(app, tmp_path, monkeypatch):
    import time

    import shiboken6
    from PySide6.QtCore import QPointF, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QSignalSpy, QTest

    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            self.calls = []
        def submit(self, *args):
            self.calls.append(args)

    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    home = tmp_path / 'home'
    (home / 'Music' / 'House').mkdir(parents=True)
    (home / 'Sets').mkdir()
    (home / 'hidden-from-folder-tree.wav').write_bytes(b'')
    engine = QQmlApplicationEngine()
    bridge = Bridge(engine, backend_factory=PassiveBackend, home_path=home)
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', 'en')
    engine.load(QUrl.fromLocalFile(str(Path('dj_digger/gui/qml/Main.qml').resolve())))
    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(str(w) for w in warnings))
    try:
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        bridge.receive('ready', {})
        bridge.receive('audio', dict(title='Local fixture.wav', playing=True, position=1, duration=4,
                                     waveform=[1000] * 512 + [200] * 512))
        tree = window.findChild(QQuickItem, 'directoryTree')
        canvas = window.findChild(QQuickItem, 'waveform')
        deadline = time.monotonic() + 5
        while tree.property('rows') != 2 and time.monotonic() < deadline:
            QTest.qWait(10)
        assert tree.property('rows') == 2
        assert bridge.homePath == str(home)
        assert Path(bridge.directoryModel.filePath(bridge.homeIndex)) == home
        # Expand and select through the actual delegate, not just the filesystem API.
        def find_item(item, name):
            if item.objectName() == name:
                return item
            return next((found for child in item.childItems() if (found := find_item(child, name))), None)
        music = find_item(tree, 'directory-Music')
        assert music is not None
        point = music.mapToScene(QPointF(80, 16)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        assert any(call[0] == 'folder' and Path(call[1]['path']) == home / 'Music' and call[1]['offset'] == 0
                   for call in bridge.backend.calls)
        painted = QSignalSpy(window.frameSwapped)
        window.update()
        assert painted.wait(3000)  # Use the settled delegate geometry for the next click.
        music = find_item(tree, 'directory-Music')
        indicator = music.property('indicator')
        point = indicator.mapToScene(QPointF(indicator.width()/2, indicator.height()/2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        deadline = time.monotonic() + 5
        while find_item(tree, 'directory-House') is None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert find_item(tree, 'directory-House') is not None
        painted = QSignalSpy(window.frameSwapped)
        window.update()
        assert painted.wait(3000)
        frame = window.grabWindow()
        assert not frame.isNull()
        origin = canvas.mapToScene(QPointF(0, 0))
        scale = frame.devicePixelRatio()
        x, y = int(origin.x() * scale), int(origin.y() * scale)
        width, height = int(canvas.width() * scale), int(canvas.height() * scale)
        # Bars rise from the bottom edge, never mirrored: the loud first half
        # reaches the top rows, the quiet second half leaves them empty while
        # still touching the bottom row.
        background = frame.pixelColor(x+width-2, y+2)
        assert any(frame.pixelColor(x+dx, y+dy) != background
                   for dx in range(2, width//2-2, 2) for dy in range(2, height//4))
        assert all(frame.pixelColor(x+dx, y+dy) == background
                   for dx in range(width//2+4, width-2, 2) for dy in range(2, height//4))
        assert any(frame.pixelColor(x+dx, y+height-1) != background
                   for dx in range(width//2+4, width-2, 2))
        # Switch in place as in the user's screenshots; rendered delegates must
        # not retain light host-palette roles when the app selects a dark theme.
        def contrast(a, b):
            def luminance(color):
                rgb = [color.redF(), color.greenF(), color.blueF()]
                linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb]
                return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
            light, dark = sorted((luminance(a), luminance(b)), reverse=True)
            return (light + .05) / (dark + .05)

        search = window.findChild(QQuickItem, 'trackSearch')
        store = window.findChild(QQuickItem, 'storeFilter')
        for theme in ('dark', 'light', 'dark'):
            window.setProperty('themeChoice', theme)
            painted = QSignalSpy(window.frameSwapped)
            window.update()
            assert painted.wait(3000)
            search_background = search.property('background').property('color')
            assert contrast(search.property('placeholderTextColor'), search_background) >= 4.5
            assert contrast(search.property('color'), search_background) >= 4.5
            assert contrast(store.property('indicator').property('color'), store.property('background').property('color')) >= 3
            deadline = time.monotonic() + 3
            while any(find_item(tree, 'directory-' + name) is None for name in ('Music', 'House')) and time.monotonic() < deadline:
                QTest.qWait(10)
            for name in ('Music', 'House'):
                item = find_item(tree, 'directory-' + name)
                assert item is not None, (theme, name, tree.property('rows'), tree.property('height'))
                background = item.property('background').property('color')
                assert contrast(item.property('contentItem').property('color'), background) >= 4.5
                arrow = item.property('indicator').childItems()[0]
                assert contrast(arrow.property('color'), background) >= 3
            # Disabled controls remain legible, with a distinct subdued text role.
            search.setProperty('enabled', False)
            assert contrast(search.property('color'), search.property('background').property('color')) >= 4.5
            search.setProperty('enabled', True)
        assert not errors
    finally:
        shiboken6.delete(engine)


def test_qml_compact_controls_play_target_and_error_banner(app, tmp_path, monkeypatch):
    import shiboken6
    from PySide6.QtCore import QPointF, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QSignalSpy, QTest

    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            self.calls = []
        def submit(self, *args):
            self.calls.append(args)

    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(w) for w in items))
    bridge = Bridge(engine, backend_factory=PassiveBackend, home_path=tmp_path)
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', 'pl')
    engine.load(QUrl.fromLocalFile(str(Path('dj_digger/gui/qml/Main.qml').resolve())))
    try:
        window = engine.rootObjects()[0]
        def settle():
            painted = QSignalSpy(window.frameSwapped)
            window.update()
            assert painted.wait(3000)
        def click(item):
            settle()
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, pos=point)
        def find(item, name):
            if item.objectName() == name:
                return item
            return next((found for child in item.childItems() if (found := find(child, name))), None)
        bridge.receive('ready', {})
        bridge.receive('sidebar', {'items': [{'source': 'fixture', 'title': 'Test playlist'}]})
        bridge.receive('view', dict(title='Local', source='fixture', local=True, generation=1,
                                    rows=[row('a', 'Alpha'), row('b', 'Beta')]))
        bridge.receive('audio', dict(key='a', title='Alpha', playing=True, duration=180, position=30))
        bridge.table.select(1)
        play = window.findChild(QQuickItem, 'playPause')
        assert not window.property('pauseTarget')
        click(play)
        assert bridge.backend.calls[-1][0] == 'play'
        assert bridge.backend.calls[-1][1]['keys'] == ['b']
        bridge.table.select(0)
        assert window.property('pauseTarget')
        click(play)
        assert bridge.backend.calls[-1][1]['keys'] == ['a']
        bridge.table.clearSelection()
        click(play)
        assert bridge.backend.calls[-1] == ('transport', {'operation': 'toggle', 'value': 0.0})
        bridge.table.select(1)
        for theme in ('light', 'dark'):
            window.setProperty('themeChoice', theme)
            settle()
            playlist = find(window.contentItem(), 'playlist-fixture')
            assert playlist.property('background').property('color') == window.property('accent')
            labels = playlist.property('contentItem').childItems()
            assert all(label.property('color') == window.property('selectionText') for label in labels)
        window.setWidth(760)
        window.setHeight(520)
        bridge.receive('error', {'text': 'Unable to load track. ' * 30})
        bridge.receive('message', {'text': 'Background scan completed'})
        settle()
        banner = window.findChild(QQuickItem, 'errorBanner')
        assert banner.isVisible()
        for name in ('volumeSlider', 'trackActions', 'errorBanner'):
            item = window.findChild(QQuickItem, name)
            for control in ([item] if name != 'trackActions' else item.childItems()):
                if not control.isVisible():
                    continue
                corner = control.mapToScene(QPointF(control.width(), control.height()))
                assert corner.x() <= window.width() + 1, (name, corner)
                assert corner.y() <= window.height() + 1, (name, corner)
        click(window.findChild(QQuickItem, 'errorDetails'))
        assert window.property('dialogOpen')
        QTest.keyClick(window, Qt.Key_Escape)
        settle()
        assert banner.isVisible()
        click(window.findChild(QQuickItem, 'dismissError'))
        assert not banner.isVisible()
        assert 'Unable to load track.' in str(window.property('messages').toVariant())
        assert not warnings
    finally:
        shiboken6.delete(engine)

"""Offline contracts for the optional Qt desktop and its worker boundary."""
import itertools
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, Qt, QThread
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


def test_table_longest_visible_text_and_header_names(app):
    model = TrackModel()
    model.replace([row('1', 'Short', 99), row('2', 'A much longer title', 120.5), row('3', 'Mid', 100)])
    assert model.longestText(2) == 'A much longer title'
    assert model.longestText(4) == '120.5'
    assert model.longestText(0) == 'New'
    # A played track says nothing until it is decided on; long mixes count hours.
    played = [row('1', 'Short', 99), dict(row('2', 'Mix', 120), status='played', duration=8408000)]
    model.replace(played)
    assert model.data(model.index(1, 0)) == '' and model.data(model.index(1, 8)) == '2:20:08'
    assert model.data(model.index(0, 8)) == '1:00'
    model.filter('short', '', False)
    assert model.longestText(2) == 'Short'
    assert model.longestText(42) == '' and model.longestText(-1) == ''
    assert model.headerName(9) == 'Stores' and model.headerName(10) == ''
    assert model.headerName(2) == 'Track name'
    assert TrackModel().longestText(2) == ''


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


def test_a_folder_row_is_renamed_on_disk_and_its_artist_set_in_place(backend, tmp_path):
    worker, events = backend
    folder = tmp_path / 'music'
    folder.mkdir()
    (folder / 'Song - Artist.wav').write_bytes(b'fixture')
    worker.submit('folder', {'path': str(folder)})
    view = wait_event(events, 'view')
    [first] = view['rows']
    assert (first['title'], first['artist'], first['status'], first['local']) == ('Song - Artist', 'Song', 'new', True)
    wait_event(events, 'rows')  # The folder's metadata pass, which must not undo the edits below.
    target = dict(keys=[first['key']], generation=view['generation'])
    worker.submit('rename', dict(target, field='title', value='Artist - Song'))
    [renamed] = wait_event(events, 'rows')['rows']
    assert (renamed['key'], renamed['title'], renamed['artist']) == (first['key'], 'Artist - Song', 'Artist')
    assert sorted(p.name for p in folder.iterdir()) == ['Artist - Song.wav']
    worker.submit('rename', dict(target, field='artist', value='Someone Else'))
    assert wait_event(events, 'rows')['rows'][0]['artist'] == 'Someone Else'
    worker.submit('rename', dict(target, field='artist', value=''))
    assert wait_event(events, 'rows')['rows'][0]['artist'] == 'Artist'
    worker.submit('rename', dict(target, field='title', value='../outside'))
    while (event := events.get(timeout=10))[0] != 'error':
        pass
    assert 'file name' in event[1]['text']
    assert sorted(p.name for p in folder.iterdir()) == ['Artist - Song.wav']


def test_added_folder_is_opened_and_saved_without_duplicates(backend, tmp_path):
    worker, events = backend
    folder = tmp_path / 'DJ Sets'
    folder.mkdir()
    for _ in range(2):
        worker.submit('add_folder', {'path': str(folder)})
        assert wait_event(events, 'sidebar')['pinned'] == [str(folder)]
        assert wait_event(events, 'view')['title'] == str(folder)
        wait_event(events, 'folder')
    assert AppConfig(tmp_path / 'config.json').pinned_directories == [str(folder)]
    hidden = tmp_path / '.hidden'
    hidden.mkdir()
    worker.submit('add_folder', {'path': str(hidden)})
    kind, value = events.get(timeout=10)
    assert kind == 'error' and 'visible folder' in value['text']
    assert worker.services.config.pinned_directories == [str(folder)]


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


def test_packaged_window_icon_loads(app):
    from PySide6.QtGui import QIcon
    icon = QIcon(str(Path('dj_digger/gui/qml/icons/app.png').resolve()))
    assert not icon.isNull() and icon.availableSizes()[0].width() == 256


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


def test_mark_and_undo_update_statuses_without_reloading_the_crate(backend):
    worker, events = backend
    worker.rows = online_rows(3)
    worker.services.library.load = lambda source: pytest.fail('a mark must not reload the crate')
    worker.submit('mark', {'keys': ['1', '3'], 'generation': 0, 'status': 'got'})
    marked = wait_event(events, 'rows')['rows']
    assert [row['status'] for row in marked] == ['got', 'new', 'got']
    worker.submit('undo', {})
    assert [row['status'] for row in wait_event(events, 'rows')['rows']] == ['new'] * 3


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


def test_settings_show_placeholder_email_as_empty(backend, tmp_path):
    from dj_digger.config import DEFAULT_EMAIL
    worker, events = backend
    for _ in range(2):
        worker.submit('settings', {})
        question = wait_event(events, 'question')
        answer = {f['name']: f['value'] for f in question['fields']}
        # A placeholder shown here would fail validation on every save.
        assert answer['user_email'] == ''
        worker.answer(question['id'], dict(answer, download_directory=str(tmp_path)))
        wait_event(events, 'sidebar')
        # The same spelling of "no email" the TUI saves.
        assert json.loads((tmp_path / 'config.json').read_text())['user_email'] == DEFAULT_EMAIL


def test_add_playlist_lists_the_import_at_once_and_fills_it_as_tracks_arrive(backend):
    from dj_digger.models import Crate, Track

    worker, events = backend
    received = []

    class Collection:
        def collect(self, target, options, generation, export_format, export_path, cancel, progress, on_tracks=None):
            received.append(target)
            progress('Fetching tracks', 1, 2)
            on_tracks(Crate(source=target, tracks=[Track('One', 'https://soundcloud.com/a/one', id=1)], title='Set'))
            progress('Fetching tracks', 2, 2)
            return type('Result', (), {'record': None})()

    worker.services._collection = Collection()
    worker.submit('dig', {})
    question = wait_event(events, 'question')
    assert question['fields'][0]['label'] == 'Paste a link to your SoundCloud playlist'
    worker.answer(question['id'], {'target': 'my-saved-page.html'})
    question = wait_event(events, 'question')
    assert question['error'] == 'Paste a link from soundcloud.com'
    url = 'https://soundcloud.com/a/sets/b'
    worker.answer(question['id'], {'target': f'  {url}  '})
    seen = []
    while not seen or seen[-1][0] != 'sidebar' or seen[-1][1]['items'][:1] == [dict(title='Set', source=url, pending=True)] \
            or any(item.get('pending') for item in seen[-1][1]['items']):
        kind, value = events.get(timeout=10)
        assert kind != 'error', value
        seen.append((kind, value))
    assert received == [url]
    sidebars = [value['items'] for kind, value in seen if kind == 'sidebar']
    assert sidebars[0][0] == dict(title=url, source=url, pending=True)
    assert dict(title='Set', source=url, pending=True) in sidebars[1]
    views = [value for kind, value in seen if kind == 'view']
    assert [len(view['rows']) for view in views[:2]] == [0, 1] and views[1]['source'] == url
    # Nothing was saved, so the half-filled view is withdrawn; the progress line clears.
    assert views[-1]['title'] == ''
    progress = [value for kind, value in seen if kind == 'importProgress']
    assert progress[0] == dict(stage='Fetching tracks', done=1, total=2) and progress[-1] == {}


def test_store_accounts_sign_in_to_bandcamp_only(backend):
    worker, events = backend
    received = []

    class Cart:
        async def setup_logins(self, stores, cancel):
            received.append(tuple(stores))
        async def close(self):
            pass

    worker.services._cart = Cart()
    worker.submit('store_login', {})
    assert wait_event(events, 'message')['text'] == 'Bandcamp session is ready'
    assert received == [('bandcamp',)]


def store_login_with(worker, setup_logins):
    worker.services._cart = type('Cart', (), {'setup_logins': setup_logins, 'close': lambda self: asyncio_noop()})()
    worker.submit('store_login', {})


async def asyncio_noop():
    pass


def next_of(events, *kinds):
    while (event := events.get(timeout=10))[0] not in kinds:
        pass
    return event


def test_cancelled_store_login_is_not_an_error(backend):
    from dj_digger.cart_models import CartCancelled
    worker, events = backend
    async def setup_logins(self, stores, cancel):
        raise CartCancelled('cart operation was cancelled')
    store_login_with(worker, setup_logins)
    assert next_of(events, 'message', 'error') == ('message', {'text': 'Cancelled'})


@pytest.mark.parametrize('choice', ['download', 'decline', 'cancel during download'])
def test_missing_chromium_is_offered_as_a_download(backend, monkeypatch, choice):
    from dj_digger.automation_errors import AutomationError, ChromiumMissing
    worker, events = backend
    calls, installs = [], []
    async def setup_logins(self, stores, cancel):
        calls.append(stores)
        if len(calls) == 1:
            raise ChromiumMissing('Chromium is required')
    def install(cancel):
        installs.append(cancel)
        if choice == 'cancel during download':
            cancel.set()
            raise AutomationError('cart operation was cancelled')
    monkeypatch.setattr('dj_digger.browser_session.install_chromium', install)
    store_login_with(worker, setup_logins)
    question = next_of(events, 'question')[1]
    assert question['title'] == 'Download Chromium'
    worker.answer(question['id'], None if choice == 'decline' else {})
    kind, value = next_of(events, 'message', 'error')
    if choice == 'download':
        assert value['text'] == 'Bandcamp session is ready' and len(calls) == 2 and len(installs) == 1
    else:
        assert (kind, value['text']) == ('message', 'Cancelled') and len(calls) == 1
        assert len(installs) == (choice != 'decline')


def test_store_login_skips_stores_without_a_login_page(monkeypatch):
    import asyncio

    from dj_digger.services.purchases import CartBrowserSession
    session = CartBrowserSession()
    async def launch():
        raise AssertionError('no browser for a store without a login page')
    monkeypatch.setattr(session, '_playwright_handle', launch)
    asyncio.run(session.setup_logins(('beatport',), asyncio.Event()))


def test_replace_export_needs_no_destination_folder(backend, tmp_path, monkeypatch):
    from dj_digger import export

    monkeypatch.setattr(export, 'destination_drive', lambda folder: (tmp_path, 'ntfs'))
    worker, events = backend
    folder = tmp_path / 'music'
    folder.mkdir()
    (folder / 'track.wav').write_bytes(b'audio')
    worker.submit('folder', {'path': str(folder)})
    generation = wait_event(events, 'view')['generation']
    worker.submit('export', {'keys': [], 'generation': generation})
    question = wait_event(events, 'question')
    answer = {f['name']: f['value'] for f in question['fields']}
    # The dialog asks for groups of decks that play the same files, those holding the
    # original CDJ set ticked the first time; the format follows from them.
    from dj_digger.decks import DECK_GROUPS, DEFAULT_DECKS
    decks = next(f for f in question['fields'] if f['name'] == 'decks')
    assert decks['kind'] == 'checks'
    assert [label for _, label in decks['options']] == [', '.join(group) for group in DECK_GROUPS]
    assert {name for index in answer['decks'] for name in DECK_GROUPS[int(index)]} >= set(DEFAULT_DECKS)
    assert not {'format', 'bits', 'rate'} & set(answer)
    newest = str(next(i for i, group in enumerate(DECK_GROUPS) if 'CDJ-3000' in group))
    worker.answer(question['id'], dict(answer, folder=' '))
    retry = wait_event(events, 'question')
    assert retry['error'] == 'This field is required'
    # The retry keeps the ticked groups rather than the deck names they stand for.
    assert next(f for f in retry['fields'] if f['name'] == 'decks')['value'] == answer['decks']
    none = dict(answer, folder='', mode='replace', decks=[])
    worker.answer(retry['id'], none)
    retry = wait_event(events, 'question')
    assert retry['error'] == 'Choose at least one deck'
    worker.answer(retry['id'], dict(none, decks=[newest]))
    review = wait_event(events, 'question')
    assert review['title'] == 'Review export'
    summary = review['fields'][0]['value'].splitlines()
    assert summary.pop(0).startswith('Warning: The destination drive uses NTFS')
    assert summary[0].startswith('FLAC, up to 24-bit / 96 kHz')
    newest_decks = ['CDJ-3000', 'CDJ-3000X', 'OPUS-QUAD', 'XDJ-AZ']
    assert [line.split(':')[0] for line in summary[1:5]] == newest_decks
    assert worker.services.config.export_decks == newest_decks
    worker.answer(review['id'], None)
    assert wait_event(events, 'message')['text'] == 'Cancelled'
    assert not [path for path in tmp_path.rglob('dj-digger-*') if path.is_dir()]


def test_playing_sends_the_kick_levels_ahead(backend):
    import asyncio
    from types import SimpleNamespace
    worker, events = backend

    class Playing:
        playing, position, duration = True, 10.0, 300.0

        def __init__(self):
            self.loaded = SimpleNamespace(track=SimpleNamespace(key='k', label='K'))

        def heard(self):
            return 9.93

        def kicks(self):
            return 9.7, .01, [0.0, .5, 1.0]

        def take_event(self):
            self.loaded = None  # One tick, then the loop ends.

    worker.services._player = Playing()
    try:
        asyncio.run_coroutine_threadsafe(worker.tick(), worker.loop).result(timeout=5)
        audio = wait_event(events, 'audio')
        assert audio['heard'] == 9.93 and abs(audio['at'] - time.time()) < 5
        assert wait_event(events, 'kicks') == dict(key='k', start=9.7, step=.01, levels=[0.0, .5, 1.0])
    finally:
        worker.services._player = None


def test_empty_replace_selection_reports_instead_of_crashing(backend, tmp_path):
    worker, events = backend
    folder = tmp_path / 'empty'
    folder.mkdir()
    worker.submit('folder', {'path': str(folder)})
    generation = wait_event(events, 'view')['generation']
    worker.submit('export', {'keys': [], 'generation': generation})
    question = wait_event(events, 'question')
    worker.answer(question['id'], dict({f['name']: f['value'] for f in question['fields']}, folder='', mode='replace'))
    while (event := events.get(timeout=10))[0] != 'error':
        pass
    assert 'No audio files selected' in event[1]['text']


def test_title_only_scan_match_is_a_local_preview_only(backend, tmp_path, monkeypatch):
    from dj_digger.models import Track
    from dj_digger.rows import Row
    worker, events = backend
    loose = tmp_path / 'maybe.wav'
    loose.write_bytes(b'audio')
    worker.rows = [Row(0, Track('Maybe', 'https://soundcloud.com/a/maybe', id=7), [])]
    library = worker.services.library
    monkeypatch.setattr(library, 'scanner', lambda directories: type('Scanner', (), {'scan': lambda self, cancel=None: None})())
    monkeypatch.setattr(library, 'match_tracks', lambda tracks, scanner: {'7': str(loose)})
    worker.submit('scan', {})
    row = wait_event(events, 'rows')['rows'][0]
    assert row['path'] == str(loose) and row['status'] != 'got'
    assert worker.rows[0].track.local_path is None
    prepared = []
    def prepare_local(track):
        prepared.append(track.local_path)
        raise ValueError('stop before audio')
    monkeypatch.setattr('dj_digger.local_audio.prepare_local', prepare_local)
    worker.submit('play', {'keys': ['7'], 'generation': worker.generation})
    while events.get(timeout=10)[0] != 'error':
        pass
    assert prepared == [str(loose)]


def test_a_track_without_an_id_says_there_is_nothing_to_stream(backend):
    from dj_digger.models import Track
    from dj_digger.rows import Row
    worker, events = backend
    worker.rows = [Row(0, Track('Nameless', 'https://soundcloud.com/a/nameless'), [])]
    worker.submit('play', {'keys': ['https://soundcloud.com/a/nameless'], 'generation': worker.generation})
    while (event := events.get(timeout=10))[0] != 'error':
        pass
    assert event[1]['text'] == 'No track id, so there is nothing to stream'


def test_delete_refuses_a_selected_link_before_asking(backend, tmp_path):
    folder = tmp_path / 'links'
    folder.mkdir()
    target = tmp_path / 'target.wav'
    target.write_bytes(b'fixture')
    try:
        (folder / 'link.wav').symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    worker, events = backend
    worker.submit('folder', {'path': str(folder)})
    view = wait_event(events, 'view')
    worker.submit('delete_files', {'keys': [view['rows'][0]['key']], 'generation': view['generation']})
    while (event := events.get(timeout=10))[0] not in ('error', 'question'):
        pass
    assert event[0] == 'error' and 'symbolic link' in event[1]['text']
    assert target.exists()


def test_refresh_keeps_a_locally_removed_row_and_shows_new_arrivals(backend, monkeypatch):
    from dj_digger.models import Crate, Track
    worker, events = backend
    source = 'https://soundcloud.com/a/sets/b'
    tracks = [Track(f'Track {n}', f'https://soundcloud.com/a/{n}', id=n) for n in (1, 2, 3)]
    served = [tracks[:2]]
    monkeypatch.setattr(worker.services.collection, 'read',
                        lambda target, options, **kwargs: Crate(source, list(served[0]), 'Playlist'))
    worker.submit('dig', {'target': source})
    view = wait_event(events, 'view')
    assert [row['key'] for row in view['rows']] == ['1', '2']
    worker.submit('remove', {'keys': ['1'], 'generation': view['generation']})
    worker.answer(wait_event(events, 'question')['id'], {})
    assert [row['key'] for row in wait_event(events, 'view')['rows']] == ['2']
    served[0] = tracks
    worker.submit('refresh', {})
    # Arrivals sort above older tracks; the removed row stays out.
    assert [row['key'] for row in wait_event(events, 'view')['rows']] == ['3', '2']


def test_export_notice_names_the_first_failure():
    from dj_digger.gui.backend import export_notice
    complete = {'status': 'complete', 'missing': [], 'results': [{'source': '/m/a.wav', 'status': 'complete'}]}
    assert export_notice(complete) == ('message', {'text': 'Export: {0}; missing files: {1}', 'args': ['complete', 0]})
    assert export_notice({'status': 'cancelled', 'missing': ['/m/a.wav'], 'results': []})[0] == 'message'
    partial = {'status': 'partial', 'missing': ['/m/a.wav', '/m/b.wav'], 'results': [
        {'source': '/m/a.wav', 'status': 'failed', 'error': 'Source changed since export was reviewed'},
        {'source': '/m/b.wav', 'status': 'failed', 'error': 'Destination already exists'}]}
    kind, payload = export_notice(partial)
    assert kind == 'error'
    assert payload['args'] == ['partial', 2, 'a.wav', 'Source changed since export was reviewed (+1)']


def test_finished_operation_label_is_cleared_but_results_stay(app, tmp_path, monkeypatch):
    from PySide6.QtQml import QQmlApplicationEngine

    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            pass
        def submit(self, *args):
            pass

    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    bridge = Bridge(QQmlApplicationEngine(), backend_factory=PassiveBackend, home_path=tmp_path)
    bridge.receive('busy', {'value': True, 'text': 'Collecting tracks'})
    assert bridge.message == 'Collecting tracks'
    bridge.receive('busy', {'value': False, 'text': ''})
    assert bridge.message == ''
    bridge.receive('busy', {'value': True, 'text': 'Importing playlists'})
    bridge.receive('message', {'text': 'Signed in'})
    bridge.receive('busy', {'value': False, 'text': ''})
    assert bridge.message == 'Signed in'
    bridge.receive('busy', {'value': True, 'text': 'Exporting audio'})
    bridge.receive('busy', {'value': False, 'text': ''})
    bridge.receive('error', {'text': 'Disk full'})
    assert bridge.message == 'Disk full'


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


def keyed(key, title, key_signature, bpm=120):
    from dj_digger.analysis import key_names
    classic, camelot = key_names(key_signature) or ('', '')
    return {**row(key, title, bpm), 'keySignature': key_signature, 'classicKey': classic, 'camelot': camelot}


def test_keys_follow_the_chosen_notation_and_sort_around_the_wheel(app):
    model = TrackModel()
    model.replace([keyed('a', 'A', 'A minor', 120.35), keyed('b', 'B', '12B'), keyed('c', 'C', 'Hm'), keyed('d', 'D', 'Db')])
    assert [model.data(model.index(i, 5)) for i in range(4)] == ['8A', '12B', 'Hm', '3B']
    assert model.data(model.index(0, 4)) == '120.3' and model.data(model.index(1, 4)) == '120'
    assert model.data(model.index(3, 5), Qt.UserRole + 5) == '3B'
    changed = []
    model.dataChanged.connect(lambda first, last, roles: changed.append((first.column(), last.column())))
    model.setKeyNotation('classic')
    assert changed == [(5, 5)]
    assert [model.data(model.index(i, 5)) for i in range(4)] == ['Am', 'E', 'Hm', 'C#']
    model.setKeyNotation('bogus')
    assert model.key_notation == 'classic'
    model.sortBy(5)
    assert [r['key'] for r in model.visible] == ['d', 'a', 'b', 'c']


def test_only_real_status_changes_flash_and_bulk_changes_do_not(app):
    from dj_digger.gui.model import FLASH_LIMIT
    model = TrackModel()
    flashes = []
    model.statusFlashed.connect(flashes.append)
    rows = [row('a', 'Alpha'), row('b', 'Beta')]
    model.replace(rows)
    model.update_progress({'a': .5})
    model.select(0)
    model.update_rows([dict(r) for r in rows])
    assert flashes == []
    changed = [dict(r) for r in rows]
    changed[1]['status'] = 'got'
    model.sortBy(2)  # The reset path flashes too.
    model.update_rows(changed)
    assert flashes == [{'b': 'got'}]
    many = [row(str(i), str(i)) for i in range(FLASH_LIMIT + 1)]
    model.replace(many)
    model.update_rows([{**r, 'status': 'skip'} for r in many])
    assert len(flashes) == 1


def test_presentation_settings_keep_only_known_keys(app, tmp_path):
    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            pass
        def submit(self, *args):
            pass

    bridge = Bridge(None, backend_factory=PassiveBackend, home_path=tmp_path)
    bridge.saveSettings({'keyNotation': 'classic', 'animations': False, 'pulseOffset': 140, 'theme': 'dark', 'colors': 'green-yellow', 'token': 'secret'})
    assert bridge._settings == {'keyNotation': 'classic', 'animations': False, 'theme': 'dark', 'colors': 'green-yellow'}


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
    now_playing = wait_event(events, 'nowPlaying')
    assert (now_playing['key'], now_playing['name'], now_playing['camelot']) == (track.key, 'Local waveform', '')
    waveform = wait_event(events, 'waveform')
    assert waveform['key'] == track.key
    assert len(waveform['samples']) == 1024
    assert max(waveform['samples']) > 0
    assert not wait_event(events, 'audio')['playing']


def test_seeks_collapse_to_the_newest_and_nudges_add_up(backend, monkeypatch):
    import asyncio

    from dj_digger.models import Track
    from dj_digger.player import Loaded
    from dj_digger.services.playback import Stream

    worker, events = backend
    player = worker.services.player
    player._loaded = Loaded(Track('Seekable', '', local_path='synthetic.wav'), Stream('synthetic.wav', duration=100))
    applied = []

    def slow_seek(seconds):
        time.sleep(.05)
        applied.append(seconds)
        player._offset = seconds

    monkeypatch.setattr(player, 'seek', slow_seek)
    try:
        for value in range(1, 21):
            worker.submit('transport', {'operation': 'seek', 'value': value})
        worker.submit('transport', {'operation': 'nudge', 'value': 10})
        worker.submit('transport', {'operation': 'nudge', 'value': -3})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (applied and applied[-1] == 27):
            time.sleep(.02)
        assert applied[-1] == 27 and len(applied) < 10
        assert applied[0] == 1  # the first seek was already running when the rest arrived
        # Every applied seek publishes a snapshot immediately rather than waiting for the ticker.
        assert wait_event(events, 'audio')['position'] in applied
        assert asyncio.run_coroutine_threadsafe(worker.action_transport({'operation': 'nudge', 'value': 5}), worker.loop).result(2) is None
        # The seek to 27 may still be finishing, in which case the nudge queues behind it.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and applied[-1] != 32:
            time.sleep(.02)
        assert applied[-1] == 32
    finally:
        player._loaded = None


def test_stopped_local_waveform_is_cancelled_and_cannot_restore_audio(backend, tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from dj_digger import local_audio
    from dj_digger.models import Track
    from dj_digger.player import Loaded
    from dj_digger.services.playback import Stream

    worker, events = backend
    path = tmp_path / 'synthetic.wav'
    path.write_bytes(b'audio')
    loaded = Loaded(Track('Old local', '', local_path=str(path)), Stream(str(path), duration=10))
    worker.services.player._loaded = loaded
    entered, release = threading.Event(), threading.Event()
    cancel = worker.waveform_cancel
    def delayed(path, stop):
        assert stop is cancel
        entered.set()
        assert release.wait(5)
        return [123]  # Even a late worker ignoring cancellation must be discarded.
    monkeypatch.setattr(local_audio, 'waveform', delayed)
    future = asyncio.run_coroutine_threadsafe(worker.load_waveform(loaded, None, cancel), worker.loop)
    try:
        assert entered.wait(5)
        asyncio.run_coroutine_threadsafe(worker.action_transport({'operation': 'stop'}), worker.loop).result(timeout=2)
        assert cancel.is_set()
        assert wait_event(events, 'audio') == {}
        worker.services.player._loaded = SimpleNamespace(track=loaded.track)
    finally:
        release.set()
    future.result(timeout=5)
    assert events.empty()
    worker.services.player._loaded = None


def test_qml_folder_roots_leaves_and_one_sided_waveform(app, tmp_path, monkeypatch):
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
    (home / 'Downloads').mkdir(parents=True)
    (home / 'Music' / 'House' / 'Deep').mkdir(parents=True)
    (home / 'Music' / 'House' / 'Deep' / 'set.mp3').write_bytes(b'')
    (home / 'Music' / 'No music' / 'Notes').mkdir(parents=True)
    (home / 'Music' / 'No music' / 'Notes' / 'readme.txt').write_text('')
    (home / 'Music' / 'Only partial').mkdir()
    (home / 'Music' / 'Only partial' / ('track.' + '0' * 32 + '.partial.mp3')).write_bytes(b'')
    (home / 'Music' / '.hidden').mkdir()
    (home / 'Music' / 'Only hidden' / '.private').mkdir(parents=True)
    (home / 'Music' / 'Only audio').mkdir()
    (home / 'Music' / 'Only audio' / 'track.wav').write_bytes(b'audio')
    # Music of its own, so a click on the root opens it whether or not its check has finished.
    (home / 'Music' / 'loose.mp3').write_bytes(b'')
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
        # Geometry and colours are asserted as they settle, without transitions in between.
        window.setProperty('animations', False)
        bridge.receive('ready', {})
        # Before anything plays the record already stands on the pair's colours, with a white
        # label and no code, and the waveform drifts as slow noise with no cursor.
        def named(name):
            return window.findChild(QQuickItem, name)
        window.setWidth(1200)  # The artwork shows from a 640 px player panel.
        painted = QSignalSpy(window.frameSwapped)
        window.update()
        assert painted.wait(3000)
        assert named('cover').isVisible() and named('cover').property('idle') is True
        assert named('blankSleeve').property('opacity') == 0 and named('whiteLabel').property('opacity') == 1
        assert named('trackCode').property('opacity') == 0
        assert named('waveform').isVisible() and not named('cursor').isVisible()
        # Moving bars are rectangles; the canvas waits for a settled waveform.
        assert named('noiseBars').isVisible() and not named('unplayedBars').isVisible()
        bridge.receive('audio', dict(key='w', title='Local fixture.wav', playing=True, position=1, duration=4))
        assert named('cover').property('idle') is False and named('cursor').isVisible()
        # Until its waveform arrives the track shows placeholder bars and a blank record.
        loading, blank = window.findChild(QQuickItem, 'waveform'), window.findChild(QQuickItem, 'cover')
        assert not loading.property('ready') and blank.property('reveal') == 0
        bridge.receive('waveform', dict(key='w', samples=[1000] * 512 + [200] * 512))
        assert loading.property('ready') and blank.property('reveal') == 1
        assert named('unplayedBars').isVisible() and not named('noiseBars').isVisible()
        # Artwork of another track is ignored; the loaded track's goes on the label as it is.
        from PySide6.QtCore import QBuffer, QByteArray
        from PySide6.QtGui import QColor, QImage

        from dj_digger.gui.artwork import label_image
        picture, encoded = QImage(16, 16, QImage.Format_RGB32), QByteArray()
        picture.fill(QColor('red'))
        buffer = QBuffer(encoded)
        buffer.open(QBuffer.WriteOnly)
        picture.save(buffer, 'PNG')
        bridge.receive('artwork', dict(key='other', image='data:image/png;base64,'))
        assert bridge.artwork == ''
        bridge.receive('artwork', dict(key='w', image=label_image(bytes(encoded))))
        label = window.findChild(QQuickItem, 'labelArtwork')
        deadline = time.monotonic() + 5
        while label.property('opacity') < 1 and time.monotonic() < deadline:
            QTest.qWait(10)
        assert label.property('source').toString().startswith('data:image/png') and label.property('opacity') == 1
        assert len(bridge.waveform) == 1024
        tree = window.findChild(QQuickItem, 'directoryTree-music')
        canvas = window.findChild(QQuickItem, 'waveform')
        def find_item(item, name):
            if item.objectName() == name:
                return item
            return next((found for child in item.childItems() if (found := find_item(child, name))), None)
        scene = window.contentItem()
        tree = find_item(scene, 'directoryTree-music')
        assert bridge.directoryRoots == [dict(path=str(home / 'Downloads'), kind='downloads'),
                                         dict(path=str(home / 'Music'), kind='music')]
        assert find_item(scene, 'directory-Sets') is None
        assert find_item(scene, 'addFolder').property('text') == 'Add folder…'
        assert find_item(scene, 'addPlaylist').property('text') == 'Add playlist'
        painted = QSignalSpy(window.frameSwapped)
        window.update()
        assert painted.wait(3000)
        music = find_item(scene, 'root-music')
        point = music.mapToScene(QPointF(100, 16)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        assert any(call[0] == 'folder' and Path(call[1]['path']) == home / 'Music' and call[1]['offset'] == 0
                   for call in bridge.backend.calls)
        expand = find_item(scene, 'expand-music')
        deadline = time.monotonic() + 5
        while not expand.isEnabled() and time.monotonic() < deadline:
            QTest.qWait(10)
        assert expand.isEnabled()
        assert not find_item(scene, 'expand-downloads').isEnabled()
        point = expand.mapToScene(QPointF(expand.width()/2, expand.height()/2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        deadline = time.monotonic() + 5
        while (find_item(tree, 'directory-House') is None
               or not find_item(tree, 'directory-House').property('hasChildren')) and time.monotonic() < deadline:
            QTest.qWait(10)
        # Only folders with music in them or below them are listed.
        assert tree.property('rows') == 2
        for name in ('.hidden', 'Only hidden', 'No music', 'Only partial'):
            assert find_item(tree, 'directory-' + name) is None
        leaf = find_item(tree, 'directory-Only audio')
        assert leaf is not None and not leaf.property('hasChildren')
        assert not leaf.property('indicator').isVisible()
        # A folder arriving with music in it shows up; an empty one never does.
        (home / 'Music' / 'Only audio' / 'Empty').mkdir()
        staged = tmp_path / 'New folder'
        staged.mkdir()
        (staged / 'new.flac').write_bytes(b'')
        child = staged.rename(home / 'Music' / 'Only audio' / 'New folder')
        # Asked of the model: filtering can rebuild the tree's delegates while this waits.
        only_audio = str(home / 'Music' / 'Only audio')
        deadline = time.monotonic() + 5
        while not bridge.directoryHasChildren(only_audio) and time.monotonic() < deadline:
            QTest.qWait(10)
        assert bridge.directoryHasChildren(only_audio)
        (home / 'Music' / 'Only audio' / 'Empty').rmdir()
        (child / 'new.flac').unlink()
        child.rmdir()
        deadline = time.monotonic() + 5
        while bridge.directoryHasChildren(only_audio) and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not bridge.directoryHasChildren(only_audio)
        painted = QSignalSpy(window.frameSwapped)
        window.update()
        assert painted.wait(3000)  # Use the settled delegate geometry for the next click.
        house = find_item(tree, 'directory-House')
        indicator = house.property('indicator')
        point = indicator.mapToScene(QPointF(indicator.width()/2, indicator.height()/2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        deadline = time.monotonic() + 5
        while find_item(tree, 'directory-Deep') is None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert find_item(tree, 'directory-Deep') is not None
        bridge.addFolder(QUrl.fromLocalFile(str(home / 'Sets')).toString())
        action, values = bridge.backend.calls[-1]
        assert action == 'add_folder'
        assert Path(values['path']) == home / 'Sets'
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
        # Scrubbing shows the pointer's time and sends one seek on release; the target stays on
        # screen until the backend reports a matching position.
        assert abs(canvas.property('fraction') - .25) < .01
        seeks = lambda: [c[1]['value'] for c in bridge.backend.calls if c[0] == 'transport' and c[1]['operation'] == 'seek']  # noqa: E731
        QTest.mousePress(window, Qt.LeftButton, pos=canvas.mapToScene(QPointF(canvas.width() * .5, 5)).toPoint())
        QTest.mouseMove(window, canvas.mapToScene(QPointF(canvas.width() * .6, 5)).toPoint())
        QTest.mouseMove(window, canvas.mapToScene(QPointF(canvas.width() * .75, 5)).toPoint())
        assert abs(canvas.property('fraction') - .75) < .01 and not seeks()
        QTest.mouseRelease(window, Qt.LeftButton, pos=canvas.mapToScene(QPointF(canvas.width() * .75, 5)).toPoint())
        assert len(seeks()) == 1 and abs(seeks()[0] - 3) < .05
        assert abs(canvas.property('fraction') - .75) < .01
        bridge.receive('audio', dict(key='w', title='Local fixture.wav', playing=True, position=1, duration=4))
        assert abs(canvas.property('fraction') - .75) < .01
        bridge.receive('audio', dict(key='w', title='Local fixture.wav', playing=True, position=3, duration=4))
        assert abs(canvas.property('fraction') - .75) < .01
        bridge.receive('audio', dict(key='w', title='Local fixture.wav', playing=True, position=3.2, duration=4))
        assert abs(canvas.property('fraction') - .8) < .01
        assert len(bridge.waveform) == 1024
        # The light follows the kick levels the player computed ahead of the speaker.
        from PySide6.QtQml import QQmlEngine, QQmlExpression
        def qml(expression):
            result, _ = QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()
            return result
        played, kick = find_item(canvas, 'playedBars'), find_item(canvas, 'kickBars')
        # Only the played side pulses; the unplayed side has no peak layer.
        assert find_item(canvas, 'unplayedKickBars') is None
        assert find_item(canvas, 'unplayedBars').property('opacity') == 1
        cover = find_item(scene, 'cover')
        window.setProperty('animations', True)
        # The snapshot says what reached the speaker and when; the clock runs from there.
        qml('audioClock = -1; syncClock(1.0, 5000, 1.5, 5010)')
        assert qml('audioClock') == pytest.approx(1.01) and qml('playhead') == pytest.approx(1.01)
        # Levels every 10 ms from 1.0 s: a kick peaking at 1.09 s. The light samples
        # them 40 ms ahead of the heard time, for the frames still on their way to the eye.
        assert window.property('displayLead') == pytest.approx(.04)
        bridge.receive('kicks', dict(key='w', start=1.0, step=.01, levels=[0] * 8 + [.5, 1, .6, .2] + [0] * 8))
        qml('lastTick = 5010; pulseTick(5010)')
        assert window.property('flash') == 0
        # It jumps up with the kick, sampled between levels, and is never late;
        # a faint level shows nothing.
        qml('pulseTick(5035)')
        assert window.property('flash') == pytest.approx(.25) and window.property('glow') == pytest.approx(.1 / .85)
        qml('pulseTick(5039)')
        assert window.property('flash') == pytest.approx(.45)
        qml('pulseTick(5050)')
        assert window.property('flash') == pytest.approx(1) and window.property('glow') == pytest.approx(1)
        assert played.property('opacity') == 1 and kick.property('opacity') == pytest.approx(1)
        assert cover.property('opacity') == 1
        assert find_item(cover, 'coverGlow').property('opacity') == pytest.approx(1)
        # The neon covers the whole played side: full-width bars under the played clip, which
        # ends at the cursor; no shader glow under the software renderer.
        assert kick.property('width') == canvas.width() and kick.property('x') == 0
        assert kick.parentItem() is played.parentItem()
        assert played.parentItem().width() == pytest.approx(canvas.property('fraction') * canvas.width())
        assert find_item(canvas, 'kickGlow').property('active') is False
        # Then it dies away like a lamp, by e every 75 ms, slower than the levels drop,
        # and is under the gate within 150 ms.
        qml('pulseTick(5125)')
        assert window.property('flash') == pytest.approx(math.exp(-1)) and window.property('glow') == pytest.approx((math.exp(-1) - .15) / .85)
        qml('pulseTick(5200)')
        assert window.property('flash') == pytest.approx(math.exp(-2)) and window.property('glow') == 0
        # Levels of another track are ignored.
        bridge.receive('kicks', dict(key='wrong', start=1.4, step=.01, levels=[1] * 30))
        qml('pulseTick(6050)')
        assert window.property('flash') == 0
        # The clock never runs past the handed position (1.5 s here).
        bridge.receive('kicks', dict(key='w', start=1.4, step=.01, levels=[1] * 30))
        qml('pulseTick(6060)')
        assert qml('playhead') == pytest.approx(1.5) and window.property('flash') == pytest.approx(1)
        # A late snapshot re-anchors the clock without cutting the light; another track clears it.
        qml('syncClock(1.4, 6070, 1.55, 6070)')
        assert qml('audioClock') == pytest.approx(1.4) and window.property('flash') == pytest.approx(1)
        qml('clockKey = "other"; syncClock(1.4, 6080, 1.55, 6080)')
        assert window.property('flash') == 0
        bridge.receive('kicks', dict(key='w', start=0, step=.01, levels=[]))
        QTest.qWait(250)
        assert kick.property('opacity') == 0  # Nothing lingers in a break.
        bridge.receive('audio', dict(key='w', title='Local fixture.wav', playing=False, position=3.2, duration=4))
        assert canvas.property('opacity') == 1 and cover.property('opacity') == 1
        # While playing with motion on, the playhead follows the interpolated heard clock, at subpixel x.
        bridge.receive('audio', dict(key='w', title='Local fixture.wav', playing=True, position=3.2, duration=4))
        qml('audioClock = 2; clockAt = 1000; lastPosition = 3')
        qml('pulseTick(1250)')
        assert window.property('playhead') == pytest.approx(2.25)
        assert canvas.property('fraction') == pytest.approx(2.25 / 4)
        cursor = find_item(canvas, 'cursor')
        assert cursor.property('x') == pytest.approx(canvas.width() * 2.25 / 4)
        # Pausing hands the playhead back to the snapshot position.
        bridge.receive('audio', dict(key='w', title='Local fixture.wav', playing=False, position=3.2, duration=4))
        assert window.property('playhead') == -1 and canvas.property('fraction') == pytest.approx(.8)
        # The peak colours stay under the WCAG 2.3.1 general and red flash thresholds
        # at the layers' full crossfade, in both themes and for every artwork tone.
        def flash_luminance(color):
            r, g, b = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in (color.redF(), color.greenF(), color.blueF())]
            return .2126 * r + .7152 * g + .0722 * b, r / (r + g + b) >= .8, (r - g - b) * 320
        pairs = qml('Object.keys(colorPairs).join(",")').split(',')
        assert pairs[0] == 'magenta-blue' and len(pairs) >= 4
        for pair in pairs:
            window.setProperty('colorPair', pair)
            for theme in ('dark', 'light'):
                window.setProperty('themeChoice', theme)
                for tone in ('waveRestFoot', 'waveRestTop', 'accent2'):
                    base, peak = qml(tone), qml(f'peakColor({tone})')
                    assert peak.hslHueF() == pytest.approx(base.hslHueF(), abs=.01)
                    assert peak.hslSaturationF() >= base.hslSaturationF() - .01 and peak.lightnessF() > base.lightnessF()
                    (y0, red_base, red0), (y1, red_peak, red1) = flash_luminance(base), flash_luminance(peak)
                    assert abs(y1 - y0) < .1, (pair, theme, tone)
                    assert not (red_base or red_peak) or abs(red1 - red0) < 20, (pair, theme, tone)
        assert flash_luminance(qml('accent2'))[1]  # The red bound is exercised.
        # A kick lights the waveform up: on the dark theme the neon is at least twice
        # as bright as the muted rest (a lighter rest read as lit, and the kick as
        # going out), on both themes it is more vivid, and no colour is a saturated
        # red. The resting bars keep 3:1 against the surfaces behind them.
        for pair, theme in itertools.product(pairs, ('dark', 'light')):
            window.setProperty('colorPair', pair)
            window.setProperty('themeChoice', theme)
            # The backdrop is always the pair's two resting colours; only their layout is per track.
            backdrop = find_item(cover, 'coverBackdrop')
            assert (backdrop.property('first'), backdrop.property('second')) == (window.property('waveRestFoot'), window.property('waveRestTop'))
            for side in ('Foot', 'Top'):
                rest, neon = window.property(f'waveRest{side}'), window.property(f'waveNeon{side}')
                (y0, red0, _), (y1, red1, _) = flash_luminance(rest), flash_luminance(neon)
                assert not red0 and not red1, (pair, theme, side)
                assert neon.hsvSaturationF() > rest.hsvSaturationF(), (pair, theme, side)
                if theme == 'dark':
                    assert y1 >= 2 * y0, (pair, side)
                    # Deep and neon, not pastel.
                    assert rest.hsvSaturationF() >= .7 and neon.hsvSaturationF() >= .8, (pair, side)
                for surface in ('panel', 'bg'):
                    y2 = flash_luminance(window.property(surface))[0]
                    assert (max(y0, y2) + .05) / (min(y0, y2) + .05) >= 3, (pair, theme, side, surface)
        window.setProperty('colorPair', 'magenta-blue')
        window.setProperty('animations', False)
        bridge.receive('audio', dict(key='other', title='Other', playing=True, position=0, duration=4))
        assert bridge.waveform == []
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
            while any(find_item(tree, 'directory-' + name) is None for name in ('House', 'Deep')) and time.monotonic() < deadline:
                QTest.qWait(10)
            for name in ('House', 'Deep'):
                item = find_item(tree, 'directory-' + name)
                assert item is not None, (theme, name, tree.property('rows'), tree.property('height'))
                background = item.property('background').property('color')
                icon, label = item.property('contentItem').childItems()
                assert label.property('text') == name
                assert contrast(label.property('color'), background) >= 4.5
                assert contrast(icon.property('color'), background) >= 3
                arrow = item.property('indicator').childItems()[0]
                assert contrast(arrow.property('color'), background) >= 3
            # Disabled controls remain legible, with a distinct subdued text role.
            search.setProperty('enabled', False)
            assert contrast(search.property('color'), search.property('background').property('color')) >= 4.5
            search.setProperty('enabled', True)
        # A folder whose music is all in subfolders opens and closes in the tree instead of as an empty list.
        house = find_item(tree, 'directory-House')
        assert bridge.folderNeedsExpand(str(home / 'Music' / 'House'))
        assert not bridge.folderNeedsExpand(str(home / 'Music' / 'Only audio'))
        calls, rows = len(bridge.backend.calls), tree.property('rows')
        point = house.mapToScene(QPointF(house.width() - 20, house.height() / 2)).toPoint()
        def rows_become(count):
            deadline = time.monotonic() + 5
            while tree.property('rows') != count and time.monotonic() < deadline:
                QTest.qWait(10)  # TreeView applies a collapse on its next polish.
            return tree.property('rows') == count
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        assert rows_become(rows - 1) and len(bridge.backend.calls) == calls
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        assert rows_become(rows) and len(bridge.backend.calls) == calls
        # Folder rows highlight the loaded folder only; loading a playlist clears them.
        own = find_item(tree, 'directory-Only audio')
        point = own.mapToScene(QPointF(own.width() - 20, own.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, pos=point)
        folder_call = bridge.backend.calls[-1]
        assert folder_call[0] == 'folder' and Path(folder_call[1]['path']) == home / 'Music' / 'Only audio'
        bridge.receive('folder', {'path': str(home / 'Music' / 'Only audio'), 'offset': 0, 'total': 0})
        bridge.receive('view', dict(title=str(home / 'Music' / 'Only audio'), source='', local=True, generation=2, rows=[]))
        assert own.property('highlighted') is True
        assert house.property('highlighted') is False
        bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=3, rows=[]))
        assert own.property('highlighted') is False
        # A right click offers to hide a folder; once the backend saved it, the tree leaves it out.
        QTest.mouseClick(window, Qt.RightButton, pos=house.mapToScene(QPointF(house.width() - 20, house.height() / 2)).toPoint())
        assert qml('folderMenu.visible') is True and Path(qml('folderMenu.path')) == home / 'Music' / 'House'
        qml('folderMenu.itemAt(2).triggered(); folderMenu.close()')
        assert bridge.backend.calls[-1][0] == 'hide_folder' and Path(bridge.backend.calls[-1][1]['path']) == home / 'Music' / 'House'
        bridge.receive('sidebar', {'items': [], 'pinned': [], 'hidden': [str(home / 'Music' / 'House')]})
        deadline = time.monotonic() + 5
        while tree.property('rows') != 1 and time.monotonic() < deadline:
            QTest.qWait(10)
        # Only "Only audio" is left: House and the Deep folder inside it are gone.
        assert tree.property('rows') == 1 and find_item(tree, 'directory-Only audio').isVisible()
        bridge.receive('sidebar', {'items': [], 'pinned': [], 'hidden': []})
        assert bridge.samePath(str(home / 'Music' / './House'), str(home / 'Music' / 'House'))
        assert not bridge.samePath('', str(home / 'Music'))
        assert not errors
        bridge.receive('sidebar', {'items': [], 'pinned': [str(home / 'Sets'), str(home / 'Music'),
                                                         str(home / 'Music' / '.hidden')]})
        assert [item['path'] for item in bridge.directoryRoots] == [str(home / name) for name in ('Downloads', 'Music', 'Sets')]
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
        # Geometry and colours are asserted as they settle, without transitions in between.
        window.setProperty('animations', False)
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
        # With a track loaded, Play and Space only pause or resume it, whatever is selected;
        # Enter starts the selected track.
        assert window.property('pauseTarget')
        click(play)
        assert bridge.backend.calls[-1] == ('transport', {'operation': 'toggle', 'value': 0.0})
        window.findChild(QQuickItem, 'trackTable').forceActiveFocus()
        QTest.keyClick(window, Qt.Key_Space)
        assert bridge.backend.calls[-1] == ('transport', {'operation': 'toggle', 'value': 0.0})
        QTest.keyClick(window, Qt.Key_Return)
        assert bridge.backend.calls[-1][0] == 'play' and bridge.backend.calls[-1][1]['keys'] == ['b']
        # Mute is a speaker icon that is struck through while muted; its name stays accessible.
        from PySide6.QtQml import QQmlEngine, QQmlExpression
        mute = window.findChild(QQuickItem, 'muteButton')
        def mute_icon():
            return QQmlExpression(QQmlEngine.contextForObject(mute), mute, 'icon.source.toString()').evaluate()[0]
        assert mute_icon().endswith('icons/volume.svg')
        click(mute)
        assert bridge.backend.calls[-1][0] == 'transport' and bridge.backend.calls[-1][1]['operation'] == 'mute'
        assert mute_icon().endswith('icons/volume-muted.svg')
        assert mute.property('text') == 'Wyłącz wyciszenie'
        click(mute)
        assert mute_icon().endswith('icons/volume.svg')
        # With nothing loaded, Play starts the selected track.
        bridge.receive('audio', {})
        assert not window.property('pauseTarget')
        click(play)
        assert bridge.backend.calls[-1][0] == 'play' and bridge.backend.calls[-1][1]['keys'] == ['b']
        bridge.receive('audio', dict(key='a', title='Alpha', playing=True, duration=180, position=30))
        bridge.table.clearSelection()
        # The action buttons are present as soon as a view is loaded and enable with a selection.
        actions = window.findChild(QQuickItem, 'trackActions')
        assert actions.isVisible()
        open_links = next(c for c in actions.childItems() if c.property('text') == 'Otwórz linki')
        assert not open_links.isEnabled()
        bridge.table.select(1)
        settle()
        assert open_links.isEnabled()
        # "More actions" omits the toolbar entries; local-only and playlist-only entries follow the view.
        def qml(expression):
            return QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]
        def menu_entries():
            return [qml(f'contextMenu.itemAt({i}).text') for i in range(int(qml('contextMenu.count')))
                    if qml(f'contextMenu.itemAt({i}).height') > 0 and qml(f'contextMenu.itemAt({i}).text')]
        # A right click and "More actions" show one menu, without anything the toolbar already offers.
        qml('contextMenu.open()')
        settle()
        assert menu_entries() == ['Odtwórz', 'Resetuj status', 'Kopiuj artystę i tytuł', 'Edytuj BPM / tonację…',
                                  'Eksportuj audio…', 'Przygotuj koszyk / playlistę Beatport…', 'Usuń pliki…']
        qml('contextMenu.close()')
        bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=2, rows=[row('a', 'Alpha'), row('b', 'Beta')]))
        bridge.table.select(1)
        qml('contextMenu.open()')
        settle()
        assert menu_entries() == ['Odtwórz', 'Resetuj status', 'Kopiuj artystę i tytuł', 'Przygotuj koszyk / playlistę Beatport…',
                                  'Usuń z playlisty…']
        qml('contextMenu.close()')
        bridge.receive('view', dict(title='Local', source='fixture', local=True, generation=3, rows=[row('a', 'Alpha'), row('b', 'Beta')]))
        bridge.table.select(1)
        settle()
        # The menu bar holds only what has no button elsewhere; every entry shows its shortcut in one column.
        titles = [qml(f'menuBar.menuAt({i}).title') for i in range(int(qml('menuBar.count')))]
        assert titles == ['Biblioteka', 'Utwory', 'Widok', 'Ustawienia', 'Pomoc']
        entries = [qml(f'menuBar.menuAt({m}).actionAt({i}) ? menuBar.menuAt({m}).actionAt({i}).text : ""')
                   for m in range(len(titles)) for i in range(int(qml(f'menuBar.menuAt({m}).count')))]
        for duplicate in ('Otwórz linki', 'Pobierz', 'Oznacz jako posiadane', 'Pomiń', 'Odtwórz / pauza', 'Szukaj', 'Ukryj obsłużone'):
            assert duplicate not in entries
        assert qml('menuBar.menuAt(1).itemAt(0).text') == 'Zaznacz wszystko'
        assert qml('menuBar.menuAt(1).itemAt(0).shortcutText') == 'Ctrl+A'
        assert qml('menuBar.menuAt(3).itemAt(0).shortcutText') == 'Shift+S'
        assert qml('menuBar.menuAt(2).itemAt(0).checkable') is True
        assert qml('menuBar.menuAt(1).itemAt(0).leftPadding') == qml('menuBar.menuAt(2).itemAt(0).leftPadding')
        assert qml('contextMenu.itemAt(0).shortcutText') == 'Enter'
        # Keys of actions that left the menus still work and are still listed, with the toolbar naming them too.
        assert open_links.property('keys') == 'O'
        bridge.backend.calls.clear()
        table = window.findChild(QQuickItem, 'trackTable')
        table.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_K)
        assert bridge.backend.calls[-1][0] == 'mark' and bridge.backend.calls[-1][1]['status'] == 'skip'
        listed = qml('shortcutList()')
        assert '  K\tPomiń' in listed and '  Space\tOdtwórz / pauza' in listed and '  Shift+S\tPreferencje…' in listed
        # In a folder view a double click edits the name in place; typing never fires a shortcut,
        # Enter saves and Esc cancels.
        settle()
        name = table.mapToScene(QPointF(qml('table.columnWidth(0) + table.columnWidth(1)') + 30, 17)).toPoint()
        QTest.mouseDClick(window, Qt.LeftButton, pos=name)
        settle()
        assert find(window.contentItem(), 'cellEditor') is not None
        bridge.backend.calls.clear()
        for char in 'Artist - Song':
            QTest.keyClick(window, char)
        QTest.keyClick(window, Qt.Key_Return)
        settle()
        assert bridge.backend.calls == [('rename', dict(keys=['a'], field='title', value='Artist - Song', generation=3))]
        assert find(window.contentItem(), 'cellEditor') is None
        artist = table.mapToScene(QPointF(qml('table.columnWidth(0)') + 30, 17)).toPoint()
        QTest.mouseDClick(window, Qt.LeftButton, pos=artist)
        settle()
        for char in 'Nobody':
            QTest.keyClick(window, char)
        QTest.keyClick(window, Qt.Key_Escape)
        settle()
        # Esc only cancels the edit; it does not go on to clear the selection.
        assert bridge.table.keys() == ['a']
        assert bridge.backend.calls == [('rename', dict(keys=['a'], field='title', value='Artist - Song', generation=3))]
        assert find(window.contentItem(), 'cellEditor') is None
        # Columns: a dragged or fitted width wins over the saved one, the divider fits on
        # double-click without sorting, and the header menu toggles visibility.
        settle()
        qml('table.setColumnWidth(1, 222); table.forceLayout()')
        assert qml('table.columnWidth(1)') == 222
        window.setWidth(1400)
        settle()
        qml('fitColumn(9)')
        assert qml('table.columnWidth(9)') == qml('contentWidth(9)') > 40
        short_title_width = qml('contentWidth(2)')
        bridge.table.update_rows([row('a', 'A much longer track title that needs a wider column')])
        assert qml('contentWidth(2)') > short_title_width
        settle()
        edge = qml('header.mapToItem(null, table.columnWidth(0) + table.columnWidth(1) - 3, 16)').toPoint()
        QTest.mouseDClick(window, Qt.LeftButton, pos=edge)
        settle()
        assert qml('table.columnWidth(1)') == qml('contentWidth(1)') != 222
        assert bridge.table.sort_column == -1
        middle = qml('header.mapToItem(null, table.columnWidth(0) + 40, 16)').toPoint()
        QTest.mouseClick(window, Qt.RightButton, pos=middle)
        settle()
        assert qml('columnMenu.visible') is True and qml('columnMenu.column') == 1
        stores = window.findChild(QObject, 'column-9')
        assert stores.property('text') == 'Sklepy' and stores.property('checked') is True
        assert window.findChild(QObject, 'column-2').property('enabled') is False
        # Genre, year and label show in folder views too, filled from the files' tags.
        assert window.findChild(QObject, 'column-3').property('enabled') is True
        qml('columnMenu.close()')
        qml('toggleColumn(9)')
        # Unloaded (zero-width) columns report -1 from TableView.
        assert qml('table.columnWidth(9)') <= 0 and stores.property('checked') is False
        assert window.property('hiddenColumns').toVariant() == [9]
        qml('toggleColumn(9); resetColumnWidths()')
        assert qml('table.columnWidth(9)') == 140 and qml('table.columnWidth(1)') == 150
        # A narrower width saved by an older version never cuts statuses or times short.
        qml('table.setColumnWidth(0, 60); table.setColumnWidth(8, 40); table.forceLayout()')
        assert qml('table.columnWidth(0)') == 104 and qml('table.columnWidth(8)') == 70
        qml('resetColumnWidths()')
        assert window.property('hiddenColumns').toVariant() == []
        # Columns can be reordered; the order is tracked for persistence and can be reset.
        qml('table.moveColumn(0, 2)')
        settle()
        assert window.property('columnOrder').toVariant() == [1, 2, 0, 3, 4, 5, 6, 7, 8, 9]
        cells = qml('(function(){ let out = []; for (let c of table.contentItem.children) if (c.column !== undefined && c.row === 0 && c.visible) out.push([Math.round(c.x), c.column]); out.sort((a, b) => a[0] - b[0]); return JSON.stringify(out.slice(0, 3).map(c => c[1])) })()')
        assert json.loads(cells) == [1, 2, 0]
        qml('resetColumnOrder()')
        settle()
        assert window.property('columnOrder').toVariant() == list(range(10))
        # The status bar spans the whole window and the dialog buttons are localized and compact.
        status = window.findChild(QQuickItem, 'statusBar')
        assert status.width() == window.width()
        bridge.receive('question', dict(id='q1', title='Dodaj playlistę', body='', ok='Dodaj',
                                        fields=[dict(name='url', label='Adres', kind='text', value='')]))
        settle()
        assert qml('dialog.standardButton(Dialog.Cancel).text') == 'Anuluj'
        assert qml('dialog.standardButton(Dialog.Ok).text') == 'Dodaj'
        assert qml('dialog.standardButton(Dialog.Cancel).width') < qml('dialog.width') / 3
        qml('dialog.close()')
        settle()
        # Several ticks under one label sit two to a row and answer with the ticked values.
        bridge.receive('question', dict(id='q2', title='Export', body='', ok='Export', fields=[dict(
            name='decks', label='Decks', kind='checks', value=['1'],
            options=[['0', 'CDJ-350, CDJ-850 / 850-K'], ['1', 'CDJ-900, CDJ-2000'], ['2', 'XDJ-AERO']])]))
        settle()
        boxes = [find(window.contentItem(), f'check-decks-{i}') for i in range(3)]
        assert [box.property('checked') for box in boxes] == [False, True, False]
        assert boxes[0].y() == boxes[1].y() < boxes[2].y() and boxes[0].x() < boxes[1].x()
        QTest.mouseClick(window, Qt.LeftButton, pos=boxes[2].mapToScene(QPointF(8, boxes[2].height() / 2)).toPoint())
        settle()
        assert qml('JSON.stringify(dialog.values.decks)') == '["1","2"]'
        qml('dialog.close()')
        settle()
        for theme in ('light', 'dark'):
            window.setProperty('themeChoice', theme)
            settle()
            playlist = find(window.contentItem(), 'playlist-fixture')
            assert playlist.property('background').property('color') == window.property('selection')
            labels = [item for item in playlist.property('contentItem').childItems() if item.isVisible()]
            assert all(label.property('color') == window.property('selectionText') for label in labels)
        # Hover lights the row under the pointer, also once the table has scrolled.
        bridge.receive('view', dict(title='Local', source='fixture', local=True, generation=2,
                                    rows=[row(str(i), f'Track {i}') for i in range(60)]))
        settle()
        qml('table.contentY = 34 * 4')
        settle()
        table = window.findChild(QQuickItem, 'trackTable')
        for visible_row in (1, 3):
            point = table.mapToScene(QPointF(200, visible_row * 34 + 17)).toPoint()
            QTest.mouseMove(window, point)
            settle()
            assert qml('table.hoverRow') == 4 + visible_row
        QTest.mouseMove(window, QPointF(2, 2).toPoint())
        settle()
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
        # With motion on, the played layer waits for the bars to settle from the noise and is then
        # painted again; it once kept the first frame of the blend, noise over the waveform.
        window.setProperty('animations', True)
        bridge.receive('audio', dict(key='blend', title='Blend', playing=True, position=10, duration=100))
        bridge.receive('waveform', dict(key='blend', samples=[900] * 1024))
        waveform, played = window.findChild(QQuickItem, 'waveform'), window.findChild(QQuickItem, 'playedBars')
        assert waveform.property('moving') and not played.parentItem().isVisible()
        from PySide6.QtCore import SIGNAL
        repainted = QSignalSpy(waveform, SIGNAL('repaint()'))
        deadline = time.monotonic() + 5
        while waveform.property('moving') and time.monotonic() < deadline:
            QTest.qWait(20)
        assert not waveform.property('moving') and played.parentItem().isVisible() and repainted.count() >= 1
        window.setProperty('animations', False)
        assert not warnings
    finally:
        shiboken6.delete(engine)


def test_reviewed_cart_validates_editable_prices():
    from decimal import Decimal

    from dj_digger.cart_models import CartItem, CartPlan
    from dj_digger.gui.backend import reviewed_cart
    item = CartItem('1', 'A - B', 'bandcamp', 's', 'p', 'id', 'B', Decimal('1'), 'EUR',
                    minimum_price=Decimal('1'), price_step=Decimal('0.5'), price_editable=True)
    plan = CartPlan((item, item))
    reviewed = reviewed_cart(plan, {'select_0': True, 'price_0': '2.5', 'select_1': False})
    assert [i.price for i in reviewed.items] == [Decimal('2.5')]
    for price, error in (('0.5', 'Invalid price'), ('1.2', 'Invalid price step'), ('abc', 'Invalid price')):
        with pytest.raises(ValueError, match=f'^{error}$'):
            reviewed_cart(plan, {'select_0': True, 'price_0': price, 'select_1': False})

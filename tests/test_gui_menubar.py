"""The desktop menu bar: account state, Help entries, selection commands and the QML menus."""
import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
pytest.importorskip('PySide6')

from tests import test_gui
from tests.test_gui import FakeLink, next_of, row, wait_event

app, backend = test_gui.app, test_gui.backend  # shared fixtures


class Relay:
    def __init__(self, browser, protocol=1):
        self.hello = dict(browser=browser, version='0.3.1', protocol=protocol)
        self.compatible = protocol == 1
        self.last_active = 0
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def started(backend):
    """The backend after start, with the account state it sends on starting already taken."""
    worker, events = backend
    assert next_of(events, 'account')[1]['signedIn'] in (True, False)
    return worker, events


def account(worker, events):
    worker.submit('account', {})
    return next_of(events, 'account')[1]


def test_account_names_the_saved_login_and_the_connected_browser_never_the_token(started, monkeypatch):
    from dj_digger import auth
    worker, events = started
    monkeypatch.delenv('SOUNDCLOUD_OAUTH_TOKEN', raising=False)
    assert account(worker, events) == dict(signedIn=False, name='', browser='')
    auth.save_token('secret-token', 'digger', 7)
    link = FakeLink()
    link._connections = [Relay('firefox', protocol=0), Relay('chrome')]
    worker.services.browser_link = link
    state = account(worker, events)
    assert state == dict(signedIn=True, name='digger', browser='Chrome') and 'secret-token' not in str(state)
    # A token from the environment is signed in, but under no saved name.
    monkeypatch.setenv('SOUNDCLOUD_OAUTH_TOKEN', 'other')
    assert account(worker, events)['name'] == ''


def test_sign_out_and_disconnect_refresh_the_menu_state(started, monkeypatch):
    from dj_digger import auth
    from dj_digger.webext import register
    worker, events = started
    monkeypatch.delenv('SOUNDCLOUD_OAUTH_TOKEN', raising=False)
    monkeypatch.setattr(register, 'unregister', lambda: ['manifest.json'])
    auth.save_token('secret-token', 'digger', 7)
    relay = Relay('chrome')
    link = FakeLink()
    link._connections = [relay]
    worker.services.browser_link = link
    worker.submit('logout', {})
    worker.answer(wait_event(events, 'question')['id'], {})
    assert next_of(events, 'account')[1]['signedIn'] is False
    worker.submit('disconnect_browsers', {})
    worker.answer(wait_event(events, 'question')['id'], {})
    state = next_of(events, 'account')[1]
    assert relay.closed, 'a relay still running would keep the menu saying Connected'
    assert state['browser'] == 'Chrome', 'the fake link does not forget a closed relay'


def test_about_shows_version_licence_and_data_folder_and_manual_update_checks(backend, monkeypatch):
    from dj_digger import __version__
    from dj_digger.gui import updates
    from dj_digger.paths import data_dir
    worker, events = backend
    worker.submit('about', {})
    question = wait_event(events, 'question')
    fields = {f['name']: (f['kind'], f['value']) for f in question['fields']}
    assert question['ok'] == 'Close'
    assert fields == {'version': ('note', __version__), 'licence': ('note', 'Apache License 2.0'), 'data': ('path', str(data_dir()))}
    worker.answer(question['id'], {})
    monkeypatch.setattr(updates, 'latest', lambda installed: None)
    worker.submit('updates', {})
    assert wait_event(events, 'message')['text'] == 'You have the latest desktop version'


def test_reveal_opens_the_data_and_log_folders(app, monkeypatch, tmp_path):
    from PySide6.QtGui import QDesktopServices

    from dj_digger import logging_setup
    from dj_digger.gui.bridge import Bridge
    from dj_digger.paths import data_dir

    class PassiveBackend:
        def __init__(self, emit):
            pass

        def submit(self, *args):
            pass

    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    monkeypatch.setattr(logging_setup, 'current_log_path', lambda: tmp_path / 'logs' / 'dj-digger.log')
    bridge = Bridge(None, backend_factory=PassiveBackend, home_path=tmp_path)
    bridge.revealFolder('data')
    bridge.revealFolder('logs')
    assert opened == [str(data_dir()), str(tmp_path / 'logs')]
    bridge.saveSettings({'recent': [{'kind': 'folder', 'target': '/music'}], 'token': 'x'})
    assert bridge._settings == {'recent': [{'kind': 'folder', 'target': '/music'}]}


def test_qml_menu_bar(app, tmp_path, monkeypatch):
    import shiboken6
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    from PySide6.QtTest import QSignalSpy

    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            self.calls = []

        def submit(self, *args):
            self.calls.append(args)

        def answer(self, *args):
            self.calls.append(('answer', *args))

    import gc

    from PySide6.QtCore import QTranslator

    # Earlier desktop tests leave their Polish catalog installed; these assertions read English.
    for translator in [o for o in gc.get_objects() if issubclass(type(o), QTranslator)]:
        if shiboken6.isValid(translator):
            app.removeTranslator(translator)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    (tmp_path / 'home').mkdir()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda found: warnings.extend(str(w) for w in found))
    bridge = Bridge(engine, backend_factory=PassiveBackend, home_path=tmp_path / 'home')
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', 'en')
    engine.load(QUrl.fromLocalFile(str(Path('dj_digger/gui/qml/Main.qml').resolve())))
    window = engine.rootObjects()[0]

    def qml(expression):
        return QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]

    def texts(menu):
        return [qml(f'{menu}.itemAt({i}).text') for i in range(int(qml(f'{menu}.count')))]

    def find(item, name):
        if item.objectName() == name:
            return item
        return next((match for child in item.childItems() if (match := find(child, name))), None)

    def settle():
        painted = QSignalSpy(window.frameSwapped)
        window.update()
        assert painted.wait(3000)

    try:
        bridge.receive('ready', {})
        assert [qml(f'menuBar.menuAt({i}).title') for i in range(int(qml('menuBar.count')))] == \
            ['Library', 'Tracks', 'Playback', 'View', 'Settings', 'Help']
        # Playback lists the player's own Actions, so their keys stay the same.
        assert qml('menuBar.menuAt(2).itemAt(0).action === playAction') is True
        assert [t for t in texts('menuBar.menuAt(2)') if t] == ['Play / pause', 'Stop', 'Previous track', 'Next track',
                                                               'Seek backward 10 s', 'Seek forward 10 s', 'Seek backward 30 s',
                                                               'Seek forward 30 s', 'Volume down', 'Volume up', 'Mute']
        library = texts('menuBar.menuAt(0)')
        assert [name for name in library if name] == ['Import playlist from link…', 'Add folder…', 'Import profile playlists…',
                                                       'Import saved summary…', 'Open recent', 'Refresh', 'Scan local library',
                                                       'Resume interrupted export…', 'Quit']
        assert qml('menuBar.menuAt(0).itemAt(2).tip').startswith('Add every playlist')
        bridge.receive('busy', {'value': True, 'text': ''})
        assert qml('menuBar.menuAt(0).itemAt(2).enabled') is False
        bridge.receive('busy', {'value': False, 'text': ''})
        assert texts('menuBar.menuAt(4)') == ['Preferences…']
        assert [name for name in texts('menuBar.menuAt(3)') if name] == ['Show sidebar', 'Full screen', 'Columns', 'Compact rows', 'Reset layout']
        qml('preferencesAction.trigger()')
        operation, preferences = bridge.backend.calls[-1]
        assert operation == 'settings' and preferences['previewThirty'] is False
        appearance = {f['name']: f for f in preferences['appearance']}
        assert set(appearance) == {'language', 'theme', 'colors', 'keyNotation', 'vinyl', 'animations'}
        assert appearance['colors']['options'][0] == ['magenta-blue', 'Magenta and blue']
        from dj_digger.gui.backend import field, section
        bridge.receive('question', dict(id='prefs', title='Preferences', ok='Save', fields=[
            section('section_appearance', 'Appearance', collapsed=True), *appearance.values(),
            section('section_browser', 'Accounts and browser', collapsed=True),
            field('connections', '', kind='connections'),
        ]))
        assert qml('dialog.visible && dialog.collapsed.section_appearance && dialog.collapsed.section_browser')
        qml('dialog.collapsed = {}')
        bridge.receive('account', dict(signedIn=True, name='digger', browser='Chrome'))
        settle()
        account = find(window.contentItem(), 'soundCloudConnection')
        assert account is not None and account.property('text') == 'Signed in as digger — Sign out…'
        bridge.receive('busy', {'value': True, 'text': ''})
        assert not account.isEnabled()
        bridge.receive('busy', {'value': False, 'text': ''})
        qml('dialog.connectionAction("logout")')
        assert bridge.backend.calls[-1][:2] == ('answer', 'prefs')
        assert bridge.backend.calls[-1][2]['_connectionAction'] == 'logout'
        bridge.receive('presentation', dict(previewThirty=True, animations=False, colors='green-yellow'))
        assert qml('previewStart') == '30' and not window.property('animations') and qml('colorPair') == 'green-yellow'
        bridge.receive('question', dict(id='playlist', title='New Playlist', ok='Create', fields=[
            field('soundcloud', 'Also create on SoundCloud', False, 'bool'),
            dict(field('sharing', 'SoundCloud visibility', 'private', 'choice',
                       [['private', 'Private'], ['public', 'Public']]), requires='soundcloud'),
        ]))
        settle()
        sharing = find(window.contentItem(), 'field-sharing')
        assert sharing is not None and not sharing.isVisible()
        qml('dialog.setValue("soundcloud", true)')
        assert sharing.isVisible()
        qml('dialog.reject()')
        # Checked follows the state whichever side changes it.
        sidebar = 'menuBar.menuAt(3).itemAt(0)'
        qml('sidebarAction.trigger()')
        assert window.property('sidebarVisible') is False and qml(f'{sidebar}.checked') is False
        window.setProperty('sidebarVisible', True)
        assert qml(f'{sidebar}.checked') is True
        qml('sidebarAction.trigger(); sidebarAction.trigger()')
        assert window.property('sidebarVisible') is True and qml('sidebarAction.checked') is True
        # Shortcuts list menu entries with every key, including those only the menus name.
        listed = qml('shortcutList()')
        for line in ('  F1, ?\tKeyboard shortcuts', '  Shift+S, Ctrl+,\tPreferences…', '  Space\tPlay / pause',
                     '  Ctrl+I\tInvert selection', '  F11\tFull screen', '  K\tSkip'):
            assert line in listed
        assert listed.count('\tPlay / pause') == 1
        bridge.table.replace([row('a', 'Night mix'), row('b', 'Night dub'), row('c', 'Day mix')])
        bridge.table.filter('Night', {})
        bridge.table.select(0)
        tracks = 'menuBar.menuAt(1)'
        assert {'Select all', 'Invert selection', 'Open saved links for all visible tracks (2)',
                'Download all visible (2)', 'Add all visible to store carts (2)…'} <= set(texts(tracks))
        export = f'{tracks}.menuAt({tracks}.count - 1)'
        assert texts(export) == ['Selected tracks (1)…', 'All visible tracks (2)…']
        bridge.table.filter('no match', {})
        assert 'Download all visible (0)' in texts(tracks)
        assert texts(export) == ['Selected tracks (0)…', 'All visible tracks (0)…']
        # Recently opened playlists and folders, newest first; playlists gone from the sidebar drop out.
        bridge.receive('sidebar', {'items': [{'source': 'sc:1', 'title': 'Peak time'}]})
        bridge.receive('view', dict(title='Peak time', source='sc:1', local=False, generation=1, rows=[]))
        bridge.receive('folder', dict(path='/music/House', offset=0, total=0, directories=[]))
        bridge.receive('view', dict(title='/music/House', local=True, generation=2, rows=[]))
        assert [(e['kind'], e['title']) for e in window.property('recentEntries').toVariant()] == [('folder', 'House'), ('playlist', 'Peak time')]
        assert texts('recentMenu')[:2] == ['House', 'Peak time']
        bridge.receive('sidebar', {'items': []})
        assert [e['kind'] for e in window.property('recentEntries').toVariant()] == ['folder']
        assert not [w for w in warnings if 'Main.qml' in w], warnings
    finally:
        shiboken6.delete(engine)


def test_update_needs_confirmation_and_idle_desktop(backend, monkeypatch):
    from dj_digger.gui import updates
    worker, events = backend
    monkeypatch.setattr(updates, 'latest', lambda installed: ('2.0.0', []))
    monkeypatch.setattr(updates, 'target', lambda version: ('uv', ['uv-fixture']))
    monkeypatch.setattr(updates, 'check_pypi', lambda version: None)
    worker.submit('updates')
    worker.answer(wait_event(events, 'question')['id'], None)
    wait_event(events, 'dismiss')
    assert worker.pending_update is None
    handle = worker.services.operations.start('Exporting')
    worker.submit('updates')
    worker.answer(wait_event(events, 'question')['id'], {})
    assert 'Finish active operations' in next_of(events, 'error')[1]['text']
    worker.services.operations.finish(handle)
    worker.submit('updates')
    worker.answer(wait_event(events, 'question')['id'], {})
    wait_event(events, 'closeForUpdate')
    assert worker.pending_update == ('uv', ['uv-fixture'])
    assert updates.update_result('2.0.0') == ('message', {'text': 'Updated to version {0}', 'args': ['2.0.0']})


def test_daily_checks_respect_opt_out_and_timestamp(backend, monkeypatch):
    import asyncio

    from dj_digger.gui import updates

    worker, events = backend
    worker.schedule_updates = True
    config = worker.services.config
    calls = []
    monkeypatch.setattr(updates, 'latest', lambda version: calls.append(version))
    def scheduled():
        asyncio.run_coroutine_threadsafe(worker.action_scheduled_updates({}), worker.loop).result(5)
    config.check_updates = False
    scheduled()
    assert not calls
    config.check_updates = True
    scheduled()
    scheduled()
    assert len(calls) == 1 and config.last_update_check > 0
    config.last_update_check -= 86401
    scheduled()
    assert len(calls) == 2

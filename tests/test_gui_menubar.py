"""The desktop menu bar: account state, Help entries, selection commands and the QML menus."""
import os
import threading
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


def test_about_shows_version_licence_and_data_folder_and_updates_open_the_releases_page(backend, monkeypatch):
    from dj_digger import __version__, browser
    from dj_digger.gui.backend import RELEASES_URL
    from dj_digger.paths import data_dir
    worker, events = backend
    worker.submit('about', {})
    question = wait_event(events, 'question')
    fields = {f['name']: (f['kind'], f['value']) for f in question['fields']}
    assert question['ok'] == 'Close'
    assert fields == {'version': ('note', __version__), 'licence': ('note', 'Apache License 2.0'), 'data': ('path', str(data_dir()))}
    worker.answer(question['id'], {})
    opened, done = [], threading.Event()
    monkeypatch.setattr(browser, 'open_url', lambda url, choice: opened.append(url) or done.set() or True)
    worker.submit('updates', {})
    assert done.wait(10) and opened == [RELEASES_URL] and RELEASES_URL.startswith('https://github.com/')


def test_invert_selection_selects_the_other_visible_rows(app):
    from dj_digger.gui.model import TrackModel
    model = TrackModel()
    model.replace([row('a', 'Alpha'), row('b', 'Beta'), row('c', 'Gamma')])
    model.select(0)
    model.invertSelection()
    assert model.keys() == ['b', 'c']
    model.filter('Beta', {})
    model.invertSelection()
    assert model.keys() == [] and model.selected == set(), 'hidden rows drop out of the selection'
    model.filter('', {})
    model.invertSelection()
    assert model.keys() == ['a', 'b', 'c']


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

    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            self.calls = []

        def submit(self, *args):
            self.calls.append(args)

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

    try:
        bridge.receive('ready', {})
        assert [qml(f'menuBar.menuAt({i}).title') for i in range(int(qml('menuBar.count')))] == \
            ['Library', 'Tracks', 'Playback', 'View', 'Settings', 'Help']
        # Playback lists the player's own Actions, so their keys stay the same.
        assert qml('menuBar.menuAt(2).itemAt(0).action === playAction') is True
        assert [t for t in texts('menuBar.menuAt(2)') if t] == ['Play / pause', 'Stop', 'Previous track', 'Next track',
                                                               'Seek backward 10 s', 'Seek forward 10 s', 'Seek backward 30 s',
                                                               'Seek forward 30 s', 'Jump to drop', 'Start previews at the drop',
                                                               'Start previews at 30 %', 'Volume down', 'Volume up', 'Mute']
        # The preview starts are one or the other: checking one unchecks the other, unchecking leaves none.
        qml('previewDropAction.trigger()')
        assert qml('previewStart') == 'drop' and qml('previewDropAction.checked') and not qml('previewThirtyAction.checked')
        qml('previewThirtyAction.trigger()')
        assert qml('previewStart') == '30' and not qml('previewDropAction.checked')
        qml('previewThirtyAction.trigger()')
        assert qml('previewStart') == '' and not qml('previewThirtyAction.checked')
        library = texts('menuBar.menuAt(0)')
        assert library.index('Import profile playlists…') < library.index('Pin folder') < library.index('Resume interrupted export…')
        assert qml('menuBar.menuAt(0).itemAt(3).tip').startswith('Add every playlist')
        # Busy: imports, restore and sign-out wait.
        bridge.receive('busy', {'value': True, 'text': ''})
        assert qml('menuBar.menuAt(0).itemAt(3).enabled') is False and qml('menuBar.menuAt(4).itemAt(2).enabled') is False
        bridge.receive('busy', {'value': False, 'text': ''})
        # One account entry and one browser entry, each saying what it will do.
        settings = 'menuBar.menuAt(4)'
        assert texts(settings)[2:] == ['Sign in to SoundCloud…', 'Connect browser…']
        bridge.receive('account', dict(signedIn=True, name='digger', browser='Chrome'))
        assert texts(settings)[2:] == ['Signed in as digger — Sign out…', 'Connected: Chrome — Disconnect…']
        qml(f'{settings}.itemAt(2).action.trigger(); {settings}.itemAt(3).action.trigger()')
        assert [c[0] for c in bridge.backend.calls[-2:]] == ['logout', 'disconnect_browsers']
        # Checked follows the state whichever side changes it.
        sidebar = 'menuBar.menuAt(3).itemAt(0)'
        qml('sidebarAction.trigger()')
        assert window.property('sidebarVisible') is False and qml(f'{sidebar}.checked') is False
        window.setProperty('sidebarVisible', True)
        assert qml(f'{sidebar}.checked') is True
        qml('sidebarAction.trigger(); sidebarAction.trigger()')
        assert window.property('sidebarVisible') is True and qml('sidebarAction.checked') is True
        qml('animationsAction.trigger()')
        assert window.property('animations') is False and qml('animationsAction.checked') is False
        # Colour entries carry a swatch of the pair's two neon colours.
        colours = 'menuBar.menuAt(3).menuAt(7)'
        assert qml(f'{colours}.title') == 'Colours' and qml(f'{colours}.itemAt(0).swatch.length') == 2
        # Shortcuts list menu entries with every key, including those only the menus name.
        listed = qml('shortcutList()')
        for line in ('  F1, ?\tKeyboard shortcuts', '  Shift+S, Ctrl+,\tPreferences…', '  Space\tPlay / pause',
                     '  Ctrl+I\tInvert selection', '  F11\tFull screen', '  K\tSkip'):
            assert line in listed
        assert listed.count('\tPlay / pause') == 1
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

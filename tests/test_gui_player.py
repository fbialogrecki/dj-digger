"""The player panel: spectral waveform, drop estimate, seeks, cues, previews and mute."""
import asyncio
import math
import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
pytest.importorskip('PySide6')

from test_gui import app, backend, wait_event  # noqa: E402,F401  (fixtures)

from dj_digger import waveform  # noqa: E402


def tone(hz, seconds=2, rate=4000):
    t = np.arange(int(seconds * rate)) / rate
    mono = (np.sin(2 * math.pi * hz * t) * 12000).astype('<i2')
    return np.repeat(mono, 2).tobytes()


def test_bands_tell_lows_from_highs():
    for hz, loudest in ((90, 0), (400, 1), (1500, 2)):
        bands = np.zeros((4, 3))
        waveform.fold_bands(bands, tone(hz), 0, 2000)
        assert all(int(np.argmax(row)) == loudest for row in bands), hz
    # Each band is measured against its own loudest bin, so quiet highs still show.
    assert waveform.band_levels([[100, 1, 0], [25, 4, 0]]) == [[255, 128, 0], [128, 255, 0]]
    shares = waveform.column_bands([[255, 0, 0], [0, 255, 0], [0, 0, 0], [0, 0, 0]], 2)
    assert shares == [[.5, .5, 0], [.333, .333, .333]]
    assert waveform.column_bands([], 3) == []


def test_drop_is_the_first_big_rise_after_a_quiet_stretch():
    intro, drop = [300] * 400, [1000] * 624
    found = waveform.drop(intro + drop)
    assert abs(found - 400 / 1024) < .01
    # Bands carry the lows the estimate follows.
    bands = [[60, 200, 200]] * 400 + [[255, 200, 200]] * 624
    assert abs(waveform.drop([500] * 1024, bands) - 400 / 1024) < .01
    # A loud intro, a breakdown and a drop: the drop, not the start.
    shaped = [1000] * 200 + [200] * 200 + [1000] * 624
    assert abs(waveform.drop(shaped) - 400 / 1024) < .01
    # No quiet stretch before a loud one, or no audio: no drop.
    assert waveform.drop([800] * 1024) == -1
    assert waveform.drop([0] * 1024) == -1
    assert waveform.drop([]) == -1


def test_seeks_are_counted_and_mute_comes_from_the_player(backend, monkeypatch):  # noqa: F811
    worker, events = backend
    player = worker.services.player
    monkeypatch.setattr(player, 'seek', lambda seconds: None)
    monkeypatch.setattr(player, 'nudge', lambda seconds: None)
    counted = []
    monkeypatch.setattr(worker, 'publish_audio', lambda: counted.append(worker.seeks))
    run = lambda coroutine: asyncio.run_coroutine_threadsafe(coroutine, worker.loop).result(timeout=5)  # noqa: E731
    run(worker.apply_seek('nudge', 5))
    run(worker.apply_seek('seek', 30))
    assert counted == [1, 2]
    run(worker.action_transport({'operation': 'mute', 'value': 0}))
    assert wait_event(events, 'volume') == {'volume': .8, 'muted': True}
    # Setting a level unmutes, and the window hears so.
    run(worker.action_transport({'operation': 'volume', 'value': .5}))
    assert wait_event(events, 'volume') == {'volume': .5, 'muted': False}


@pytest.fixture
def window(app, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

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
    engine.rootContext().setContextProperty('systemLanguage', 'en')
    engine.load(QUrl.fromLocalFile(str(Path('dj_digger/gui/qml/Main.qml').resolve())))
    root = engine.rootObjects()[0]
    root.setProperty('animations', False)
    bridge.receive('ready', {})
    try:
        yield root, bridge
        assert not warnings, warnings
    finally:
        import shiboken6
        shiboken6.delete(engine)


def qml(root, expression):
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    return QQmlExpression(QQmlEngine.contextForObject(root), root, expression).evaluate()[0]


def settle(root):
    from PySide6.QtTest import QSignalSpy
    painted = QSignalSpy(root.frameSwapped)
    root.update()
    assert painted.wait(3000)


def seeks(bridge):
    return [c[1]['value'] for c in bridge.backend.calls if c[0] == 'transport' and c[1]['operation'] == 'seek']


def audio(key, position=0, seeks=0, playing=True):
    return dict(key=key, title=key, playing=playing, position=position, duration=100, seeks=seeks)


def test_keys_cues_and_the_drop_seek(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    root, bridge = window
    bridge.receive('audio', audio('a', 12))
    bridge.receive('waveform', dict(key='a', samples=[300] * 400 + [1000] * 624, bands=[], drop=.4))
    assert root.property('dropTime') == pytest.approx(40)
    settle(root)
    QTest.keyClick(root, Qt.Key_5)
    QTest.keyClick(root, Qt.Key_0)
    qml(root, 'jumpToDrop()')
    assert seeks(bridge) == [50, 0, 40]
    # Hot cues: Ctrl+n sets one at the playhead, Alt+n jumps to it; only this track's show.
    QTest.keyClick(root, Qt.Key_2, Qt.ControlModifier)
    assert qml(root, 'trackCues[1]') == pytest.approx(12)
    assert find(root.contentItem(), 'cue2').property('visible')
    assert not find(root.contentItem(), 'cue1').property('visible')
    QTest.keyClick(root, Qt.Key_2, Qt.AltModifier)
    QTest.keyClick(root, Qt.Key_3, Qt.AltModifier)  # No cue 3: nothing happens.
    assert seeks(bridge) == [50, 0, 40, 12]
    bridge.receive('audio', audio('b'))
    assert qml(root, 'trackCues[1]') is None
    # The Help list names them: the Playback menu's Actions and the waveform keys.
    help = qml(root, 'shortcutList()')
    for text in ('}\tSeek forward 30 s', 'J\tJump to drop', '1–9, 0\t', 'Ctrl+1–4\t', 'Alt+1–4\t'):
        assert text in help, text


def find(item, name):
    if item.objectName() == name:
        return item
    return next((found for child in item.childItems() if (found := find(child, name))), None)


def test_previews_start_at_30_percent_or_the_drop_once(window):
    root, bridge = window
    root.setProperty('previewStart', '30')
    bridge.receive('audio', audio('a'))
    assert seeks(bridge) == [30]
    bridge.receive('audio', audio('a', 30, seeks=1))
    assert seeks(bridge) == [30], 'once per track'
    root.setProperty('previewStart', 'drop')
    bridge.receive('audio', audio('b', 0, seeks=1))
    assert seeks(bridge) == [30], 'the drop waits for the waveform'
    bridge.receive('waveform', dict(key='b', samples=[1] * 1024, bands=[], drop=.42))
    assert seeks(bridge) == [30, 42]
    # A track without a drop starts at 30 %; one the listener already moved in stays put.
    bridge.receive('audio', audio('c', 0, seeks=2))
    bridge.receive('waveform', dict(key='c', samples=[1] * 1024, bands=[], drop=-1))
    bridge.receive('audio', audio('d', 0, seeks=3))
    bridge.receive('audio', audio('d', 20, seeks=4))
    bridge.receive('waveform', dict(key='d', samples=[1] * 1024, bands=[], drop=.5))
    assert seeks(bridge) == [30, 42, 30]


def test_a_late_snapshot_keeps_the_light_and_a_served_seek_clears_it(window):
    root, bridge = window
    root.setProperty('animations', True)
    bridge.receive('audio', audio('a', 1.5))
    qml(root, 'audioClock = -1; syncClock(1.0, 5000, 1.5, 5000)')
    root.setProperty('flash', 1)
    # An audio hiccup moves the position a second without a seek: re-anchored, still lit.
    qml(root, 'syncClock(2.6, 5100, 2.7, 5100)')
    assert root.property('flash') == 1 and qml(root, 'audioClock') == pytest.approx(2.6)
    bridge.receive('audio', audio('a', 2.8, seeks=1))
    qml(root, 'syncClock(9.0, 5200, 9.1, 5200)')
    assert root.property('flash') == 0


def test_spectral_colours_are_readable_and_not_a_saturated_red(window):
    root, _ = window

    def linear(c):
        return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4

    def luminance(color):
        r, g, b = (linear(c) for c in (color.redF(), color.greenF(), color.blueF()))
        return .2126 * r + .7152 * g + .0722 * b, r / (r + g + b)

    for theme in ('dark', 'light'):
        root.setProperty('themeChoice', theme)
        panel = luminance(qml(root, 'panel'))[0]
        for index in range(3):
            value, red = luminance(qml(root, f'Qt.color(spectralColors[{index}])'))
            assert red < .8
            assert (max(value, panel) + .05) / (min(value, panel) + .05) >= 3
    assert qml(root, 'spectralColor([1, 0, 0])') == qml(root, 'Qt.color(spectralColors[0])')


def test_new_view_settings_are_kept(tmp_path, monkeypatch, app):  # noqa: F811
    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            pass

        def submit(self, *args):
            pass

    bridge = Bridge(None, backend_factory=PassiveBackend, home_path=tmp_path)
    values = {'remainingTime': True, 'previewStart': 'drop', 'spectral': False, 'vinyl': False}
    bridge.saveSettings(dict(values, other=1))
    assert bridge._settings == values


def test_the_browser_panel_mirrors_the_player_and_drives_it(backend, monkeypatch):  # noqa: F811
    """webext player.state on every change (deduplicated) and player.command onto the window's own steps."""
    from dj_digger.gui import backend as module
    from dj_digger.models import Track
    from dj_digger.player import Loaded
    from dj_digger.services.playback import Stream

    assert [module.cover_hash(s) for s in ('', '123456', 'local:abc', 'https://soundcloud.com/a/b-ü')] == [
        7, 926833366, 1713381292, 1141811965]  # Main.qml's cover.hash, computed by node
    worker, events = backend
    monkeypatch.setattr(module, 'BROWSER_DEBOUNCE', 0)
    published = []
    worker.services.browser_link = type('Link', (), {'publish_player': lambda self, state: published.append(state)})()
    run = lambda coroutine: asyncio.run_coroutine_threadsafe(coroutine, worker.loop).result(timeout=5)  # noqa: E731

    def latest(count):
        for _ in range(250):
            if len(published) >= count:
                return {k: v for k, v in published[-1].items() if k != 'at'}
            run(asyncio.sleep(.02))
        raise AssertionError(published)

    steps = []
    monkeypatch.setattr(worker, 'action_step', lambda values: asyncio.sleep(0, steps.append(values)))
    monkeypatch.setattr(worker, 'action_transport', lambda values: asyncio.sleep(0, steps.append(values)))
    run(worker.action_remote({'op': 'next'}))  # nothing loaded: nothing to step
    run(worker.action_remote({'op': 'seek', 'fraction': .5}))
    pair = ['#b8286a', '#3a5fd6', '#ff2fa8', '#1e9bff']
    idle = dict(busy=False, label='', done=0, total=0)
    run(worker.action_look({'colors': pair + ['red'], 'dark': True}))
    assert latest(1) == dict(title='', artist='', playing=False, position=0.0, duration=0.0, seed=7, artwork='', colors=pair,
                             dark=True, jobs=idle, needs=0)
    track = Track('Song', '', artist='Artist', local_path='synthetic.wav', local_id='abc')
    player = worker.services.player
    player._loaded = Loaded(track, Stream('synthetic.wav', duration=100))
    try:
        worker.loop.call_soon_threadsafe(worker.publish_now_playing)
        assert latest(2) == dict(title='Song', artist='Artist', playing=False, position=0.0, duration=100.0, seed=1713381292,
                                 artwork='', colors=pair, dark=True, jobs=idle, needs=0)
        assert abs(published[-1]['at'] - time.time()) < 5  # when the position was read: the panel's clock runs from there
        worker.browser_artwork = (track.key, 'data:image/jpeg;base64,AA==')
        worker.loop.call_soon_threadsafe(worker.publish_browser)
        worker.loop.call_soon_threadsafe(worker.publish_browser)  # unchanged: not sent again
        assert latest(3)['artwork'] == 'data:image/jpeg;base64,AA=='
        run(asyncio.sleep(.1))
        assert len(published) == 3
        # While playing the position alone goes at most every BROWSER_POSITION_EVERY seconds; a seek goes at once.
        player._playing, player._offset = True, 30.0
        worker.loop.call_soon_threadsafe(worker.publish_browser)
        assert latest(4)['playing'] and latest(4)['position'] == 30.0
        player._offset = 31.0
        worker.loop.call_soon_threadsafe(worker.publish_browser)
        run(asyncio.sleep(.1))
        assert len(published) == 4
        worker.browser_sent -= module.BROWSER_POSITION_EVERY
        worker.loop.call_soon_threadsafe(worker.publish_browser)
        assert latest(5)['position'] == 31.0
        player._offset, worker.seeks = 70.0, worker.seeks + 1
        worker.loop.call_soon_threadsafe(worker.publish_browser)
        assert latest(6)['position'] == 70.0
        player._playing = False
        for op in ('prev', 'toggle', 'next', 'stop'):
            run(worker.action_remote({'op': op}))
        run(worker.action_remote({'op': 'seek', 'fraction': .25}))
        assert steps == [{'direction': -1}, {'operation': 'toggle'}, {'direction': 1}, {'operation': 'seek', 'value': 25.0}]
    finally:
        player._loaded, player._playing, player._offset = None, False, 0.0


def test_the_browser_panel_follows_the_job_and_cancels_it(backend, monkeypatch):  # noqa: F811
    """jobs mirrors the status bar ('busy', 'importProgress'), also from a worker thread; cancel is its Cancel."""
    from dj_digger.gui import backend as module

    worker, events = backend
    monkeypatch.setattr(module, 'BROWSER_DEBOUNCE', 0)
    published = []
    worker.services.browser_link = type('Link', (), {'publish_player': lambda self, state: published.append(state)})()
    run = lambda coroutine: asyncio.run_coroutine_threadsafe(coroutine, worker.loop).result(timeout=5)  # noqa: E731

    def jobs():
        run(asyncio.sleep(.1))
        return published[-1]['jobs']

    async def job(handle):
        threading.Thread(target=worker.send, args=('importProgress', dict(stage='Importing playlists', done=2, total=5))).start()
        await asyncio.sleep(.2)
        assert jobs_now() == dict(busy=True, label='Importing playlists', done=2, total=5)
        await worker.dispatch('remote', {'op': 'cancel'})
        assert handle.cancel.is_set()
        worker.send('importProgress', {})
        await asyncio.sleep(.1)
        assert jobs_now() == dict(busy=True, label='Collecting tracks', done=0, total=0)

    jobs_now = lambda: published[-1]['jobs']  # noqa: E731
    run(worker.job('Collecting tracks', job))
    assert jobs() == dict(busy=False, label='', done=0, total=0)


def test_the_browser_panel_answers_only_the_gates_question_on_screen(backend, monkeypatch):  # noqa: F811
    """needs counts the gates of the open "Gates need you" question; gates answers that one and no other."""
    from dj_digger.gui import backend as module

    worker, events = backend
    monkeypatch.setattr(module, 'BROWSER_DEBOUNCE', 0)
    published = []
    worker.services.browser_link = type('Link', (), {'publish_player': lambda self, state: published.append(state)})()
    run = lambda coroutine: asyncio.run_coroutine_threadsafe(coroutine, worker.loop).result(timeout=5)  # noqa: E731
    asked = []

    async def scenario():
        other = asyncio.ensure_future(worker.ask('Settings'))  # an older question is on screen first
        await asyncio.sleep(0)
        gates = asyncio.ensure_future(worker.ask('Gates need you', opened=lambda ident: (
            setattr(worker, 'gates_waiting', (ident, 3)), worker.publish_browser(), asked.append(ident))))
        await asyncio.sleep(.1)
        assert published[-1]['needs'] == 3
        await worker.action_remote({'op': 'gates', 'choice': 'skip'})
        await asyncio.sleep(.05)
        assert not gates.done() and not other.done()  # not the one on screen: ignored
        worker.answer(next(iter(worker.questions)), {})
        await other
        await worker.action_remote({'op': 'gates', 'choice': 'skip'})
        assert await gates == {'next': 'skip'}

    run(scenario())
    assert asked


def test_import_target_takes_only_soundcloud_sets_tracks_and_profiles():
    from dj_digger.gui.backend import import_target

    assert import_target('https://www.soundcloud.com/some-dj/a_track-2/?in=some-dj/sets/x') == ('track', 'https://soundcloud.com/some-dj/a_track-2')
    assert import_target('https://soundcloud.com/some-dj/sets/deep_cuts-2') == ('set', 'https://soundcloud.com/some-dj/sets/deep_cuts-2')
    assert import_target('https://m.soundcloud.com/some-dj/sets/x/?in=y#t') == ('set', 'https://soundcloud.com/some-dj/sets/x')
    for url in ('https://soundcloud.com/some-dj', 'https://www.soundcloud.com/some-dj/sets', 'https://soundcloud.com/some-dj/tracks?x=1'):
        assert import_target(url) == ('profile', 'https://soundcloud.com/some-dj'), url
    for url in ('http://soundcloud.com/some-dj', 'https://evil.com/some-dj', 'https://soundcloud.com.evil.com/a',
                'https://api.soundcloud.com/a', 'https://user@soundcloud.com/a', 'https://soundcloud.com:444/a',
                'https://soundcloud.com/a/likes', 'https://soundcloud.com/a/track/s-secret', 'https://soundcloud.com/a/sets/b/s-secret', 'https://soundcloud.com/',
                'https://soundcloud.com/a%2Fb', 'https://soundcloud.com//a', 'javascript:alert(1)', 'https://[::1', '',
                'https://soundcloud.com/' + 'a' * 500):
        assert import_target(url) is None, url


def test_the_browser_import_starts_the_windows_own_import(backend, monkeypatch):  # noqa: F811
    worker, events = backend
    run = lambda coroutine: asyncio.run_coroutine_threadsafe(coroutine, worker.loop).result(timeout=5)  # noqa: E731
    started = []
    monkeypatch.setattr(worker, 'action_dig', lambda values: asyncio.sleep(0, started.append(('dig', values))))
    monkeypatch.setattr(worker, 'action_profile', lambda values: asyncio.sleep(0, started.append(('profile', values))))
    for url in ('https://soundcloud.com/a/sets/b?x=1', 'https://www.soundcloud.com/a/reposts', 'https://soundcloud.com/a/t',
                'https://soundcloud.com/a/likes', 'http://soundcloud.com/a/t'):
        run(worker.action_remote({'op': 'import', 'url': url}))
    assert started == [('dig', {'target': 'https://soundcloud.com/a/sets/b', 'add': True}),
                       ('profile', {'url': 'https://soundcloud.com/a'}),
                       ('dig', {'target': 'https://soundcloud.com/a/t', 'add': True})]  # a track: a one-track playlist


def test_the_window_tells_the_browser_panel_its_colours(window):
    root, bridge = window
    looks = [c[1] for c in bridge.backend.calls if c[0] == 'look']
    dark = qml(root, 'root.dark')
    assert looks and looks[-1] == {'colors': ['#b8286a', '#3a5fd6', '#ff2fa8', '#1e9bff'] if dark else
                                   ['#ff2187', '#4d77ff', '#ff0094', '#42b1ff'], 'dark': dark}
    root.setProperty('colorPair', 'green-yellow')
    assert [c[1] for c in bridge.backend.calls if c[0] == 'look'][-1]['colors'][0] == ('#1a8f45' if dark else '#069d3e')
    root.setProperty('themeChoice', 'light' if dark else 'dark')
    assert [c[1] for c in bridge.backend.calls if c[0] == 'look'][-1] == {
        'colors': ['#069d3e', '#a18606', '#00c43e', '#c1a900'] if dark else ['#1a8f45', '#a88d0a', '#2bff6e', '#ffe000'],
        'dark': not dark}

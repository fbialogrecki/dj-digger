"""The track context menu: entries per view, counts, reasons, and the ways it opens."""
from pathlib import Path

import pytest
from test_gui import app, row  # noqa: F401  (skips without PySide6; app is a fixture)

from dj_digger.gui.model import TrackModel


def test_invert_selection_flips_only_visible_rows(app):  # noqa: F811
    model = TrackModel()
    model.replace([row('a', 'Alpha'), row('b', 'Beta'), row('c', 'Gamma')])
    model.select(0)
    model.invertSelection()
    assert model.keys() == ['b', 'c']
    model.filter('Beta', {})
    model.invertSelection()
    assert model.keys() == []
    # Like Select all, it acts on what the filters show; hidden rows leave the selection.
    model.filter('', {})
    assert model.keys() == []


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
    win = engine.rootObjects()[0]
    win.setProperty('animations', False)
    bridge.receive('ready', {})
    try:
        yield win, bridge
        assert not warnings
    finally:
        import shiboken6
        shiboken6.delete(engine)


def qml(win, expression):
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    return QQmlExpression(QQmlEngine.contextForObject(win), win, expression).evaluate()[0]


def settle(win):
    from PySide6.QtTest import QSignalSpy
    painted = QSignalSpy(win.frameSwapped)
    win.update()
    assert painted.wait(3000)


def shown(win):
    """objectNames of the entries the open menu shows (hidden ones collapse to no height)."""
    count = int(qml(win, 'contextMenu.count'))
    return [qml(win, f'contextMenu.itemAt({i}).objectName') for i in range(count)
            if qml(win, f'contextMenu.itemAt({i}).height') > 0 and qml(win, f'contextMenu.itemAt({i}).objectName')]


def entry(win, name):
    return f'(function() {{ for (let i = 0; i < contextMenu.count; i++) if (contextMenu.itemAt(i).objectName === "{name}") return contextMenu.itemAt(i) }})()'


def test_menu_entries_follow_the_view_and_name_counts(window):
    from PySide6.QtGui import QGuiApplication
    win, bridge = window
    win.setWidth(1200)
    bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=1,
                                rows=[dict(row('a', 'Alpha'), url='https://soundcloud.com/x/a'),
                                      dict(row('b', 'Beta'), url='javascript:alert(1)'), row('c', 'Gamma')]))
    bridge.table.selectAll()
    qml(win, 'contextMenu.open()')
    settle(win)
    assert shown(win) == ['play', 'open', 'reset', 'copy', 'copyLink', 'cart', 'remove']
    # Translations may already be installed by another test, so labels are checked by what they carry.
    assert '3' in qml(win, entry(win, 'remove') + '.text')
    assert qml(win, entry(win, 'remove') + '.destructive') is True
    assert qml(win, entry(win, 'download') + '.shortcutText') == 'D'
    qml(win, 'contextMenu.close()')
    win.setWidth(800)
    qml(win, 'sidebar.SplitView.preferredWidth = 280')
    settle(win)
    assert qml(win, 'toolbar.compactActions') and qml(win, 'moreActions.visible')
    qml(win, 'contextMenu.open()')
    settle(win)
    assert {'download', 'owned', 'skip'} <= set(shown(win))
    assert {'selectAll', 'invert'}.isdisjoint(shown(win))
    qml(win, 'contextMenu.close()')
    win.setWidth(1200)
    # Only web addresses are copied.
    bridge.copyLinks()
    assert QGuiApplication.clipboard().text() == 'https://soundcloud.com/x/a'

    bridge.receive('view', dict(title='Local', source='fixture', local=True, generation=2,
                                rows=[row('a', 'Alpha'), row('b', 'Beta')]))
    bridge.table.selectAll()
    qml(win, 'contextMenu.open()')
    settle(win)
    assert shown(win) == ['play', 'reset', 'copy', 'reveal', 'edit', 'export', 'deleteFiles']
    assert '2' in qml(win, entry(win, 'deleteFiles') + '.text')
    # Disabled entries say why.
    assert qml(win, entry(win, 'edit') + '.enabled') is False
    for name in ('edit', 'reveal'):
        item = entry(win, name)
        assert qml(win, f'{item}.reason') and qml(win, f'{item}.contentItem.children[0].text') == qml(win, f'{item}.text + " (" + {item}.reason + ")"')
    qml(win, 'contextMenu.close()')


def test_menu_opens_from_keyboard_and_empty_area_and_saves_an_edit(window):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QTest
    win, bridge = window
    bridge.receive('view', dict(title='Local', source='fixture', local=True, generation=1,
                                rows=[row('a', 'Alpha'), row('b', 'Beta')]))
    bridge.table.select(1)
    table = win.findChild(QQuickItem, 'trackTable')
    table.forceActiveFocus()
    settle(win)
    QTest.keyClick(win, Qt.Key_Menu)
    assert qml(win, 'contextMenu.visible') is True
    qml(win, 'contextMenu.close()')
    settle(win)
    QTest.keyClick(win, Qt.Key_F10, Qt.ShiftModifier)
    assert qml(win, 'contextMenu.visible') is True
    qml(win, 'contextMenu.close()')
    settle(win)
    # Below the last row a right click keeps the selection and opens the menu.
    below = table.mapToScene(QPointF(40, table.height() - 10)).toPoint()
    QTest.mouseClick(win, Qt.RightButton, pos=below)
    assert qml(win, 'contextMenu.visible') is True and bridge.table.keys() == ['b']
    qml(win, 'contextMenu.close()')
    settle(win)
    # A right click while a name is being edited saves the edit before the menu opens.
    qml(win, 'table.startEditing("a", titleColumn)')
    settle(win)
    QTest.keyClick(win, 'Z')
    other = table.mapToScene(QPointF(40, 34 + 17)).toPoint()
    QTest.mouseClick(win, Qt.RightButton, pos=other)
    settle(win)
    assert ('rename', dict(keys=['a'], field='title', value='Z', generation=1)) in bridge.backend.calls
    assert qml(win, 'contextMenu.visible') is True
    assert qml(win, 'table.editKey') == ''
    qml(win, 'contextMenu.close()')
    settle(win)
    assert table.hasActiveFocus()


def test_send_to_and_find_in_follow_the_current_selection(window):
    win, bridge = window
    bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=3,
                                rows=[dict(row('a', 'Alpha'), url='https://soundcloud.com/x/a', publishable=True),
                                      dict(row('b', 'Beta'), media='local-file')]))
    bridge.receive('sidebar', dict(items=[dict(source='local-playlist:1', title='Local', count=0),
                                          dict(source='remote', title='Mine', count=0, owned=True),
                                          dict(source='foreign', title='Other', count=0)]))
    bridge.table.select(0)
    qml(win, 'contextMenu.open()')
    settle(win)
    assert qml(win, 'sendToMenu.count') == 3
    assert qml(win, 'sendToMenu.itemAt(0).text') == 'Create playlist from selection…'
    assert qml(win, 'sendToMenu.itemAt(2).enabled')
    assert qml(win, entry(win, 'openSoundCloud') + '.visible')
    assert qml(win, 'searchProviderMenu.count') == 5
    qml(win, 'searchProviderMenu.itemAt(3).triggered()')
    assert ('track_link', dict(keys=['a'], generation=3, provider='YouTube Music')) in bridge.backend.calls
    bridge.table.selectAll()
    assert not qml(win, 'searchProviderMenu.enabled')
    assert not qml(win, 'sendToMenu.itemAt(2).enabled')
    assert qml(win, 'sendToMenu.itemAt(2).reason') == 'Only SoundCloud tracks can be sent to a SoundCloud playlist'
    assert not qml(win, entry(win, 'openSoundCloud') + '.visible')
    qml(win, 'sendToMenu.itemAt(1).triggered()')
    assert ('add_to_playlist', dict(source='local-playlist:1', keys=['a', 'b'], generation=3)) in bridge.backend.calls


def test_nothing_animates_or_ticks_at_idle(window):
    """No track, nothing busy, window active: no frame loop, animation or timer is left running (idle CPU)."""
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    win, _bridge = window
    win.setProperty('animations', True)
    win.show()
    win.requestActivate()
    settle(win)
    QTest.qWait(1000)  # start-up one-shots (the play button's pop, 50 ms settles) end

    running = sorted(o.objectName() or o.metaObject().className() for o in win.findChildren(QObject)
                     if any(kind in o.metaObject().className() for kind in ('Animation', 'Timer'))
                     and o.property('running') and not o.property('paused'))
    assert qml(win, 'motion') is True and running == []

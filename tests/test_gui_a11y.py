"""Cross-cutting desktop polish: focus ring, toasts, error banner, start state and themes."""
import json
from pathlib import Path

import pytest

pytest.importorskip('PySide6')
import test_gui
from test_gui import row

# The desktop's Qt application fixture, shared with the main GUI tests.
app = test_gui.app


def test_focus_ring_toasts_banner_start_state_and_high_contrast(app, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QSignalSpy, QTest

    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            self.calls = []
        def submit(self, *args):
            self.calls.append(args)

    import gc

    import shiboken6
    from PySide6.QtCore import QTranslator

    # Earlier desktop tests leave their Polish catalog installed; these assertions read English.
    for translator in [o for o in gc.get_objects() if issubclass(type(o), QTranslator)]:
        if shiboken6.isValid(translator):
            app.removeTranslator(translator)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    # A saved high-contrast theme survives a restart.
    (tmp_path / 'config' / 'dj-digger').mkdir(parents=True)
    (tmp_path / 'config' / 'dj-digger' / 'gui.json').write_text(json.dumps({'theme': 'contrast'}), encoding='utf-8')
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(w) for w in items))
    bridge = Bridge(engine, backend_factory=PassiveBackend, home_path=tmp_path)
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', 'en')
    engine.load(QUrl.fromLocalFile(str(Path('dj_digger/gui/qml/Main.qml').resolve())))
    try:
        window = engine.rootObjects()[0]
        window.setProperty('animations', False)
        def qml(expression):
            return QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]
        def settle():
            QTest.qWait(50)
            painted = QSignalSpy(window.frameSwapped)
            window.update()
            assert painted.wait(3000)
        def item(name):
            return window.findChild(QQuickItem, name)
        assert qml('themeChoice') == 'contrast' and qml('dark') and qml('String(bg)') == '#000000'
        window.setProperty('themeChoice', 'dark')
        bridge.receive('ready', {})
        settle()
        # Nothing open: the start state names the first steps as buttons.
        assert item('emptyState').isVisible() and item('emptyAddPlaylist').isVisible()
        assert item('emptyAddPlaylist').property('text') == 'Add playlist…'
        bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=1,
                                    rows=[row('a', 'Alpha'), row('b', 'Beta')]))
        settle()
        assert not item('emptyState').isVisible()
        bridge.receive('view', dict(title='Empty', source='sc:2', local=False, generation=2, rows=[]))
        settle()
        assert item('emptyState').isVisible() and not item('emptyAddPlaylist').isVisible()
        bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=3,
                                    rows=[row('a', 'Alpha'), row('b', 'Beta')]))
        settle()
        # The ring follows keyboard focus only; a click (any other reason) leaves it off.
        ring = item('focusRing')
        qml('moreActions.forceActiveFocus(Qt.TabFocusReason)')
        settle()
        assert ring.isVisible() and ring.parentItem().property('text') == 'More actions'
        assert ring.width() == ring.parentItem().width() and qml('focusRing.border.width') == 2
        qml('table.forceActiveFocus(); moreActions.forceActiveFocus()')
        settle()
        assert not ring.isVisible()
        # Tab from the search field lands on the next control with the ring on it.
        qml('search.forceActiveFocus(Qt.TabFocusReason)')
        QTest.keyClick(window, Qt.Key_Tab)
        settle()
        assert ring.isVisible() and ring.parentItem() is not None
        # Information is confirmed in toasts, at most three; errors go to the banner instead.
        for text in ('one', 'two', 'three', 'four'):
            bridge.receive('message', {'text': text})
        settle()
        assert [qml(f'toastModel.get({i}).text') for i in range(qml('toastModel.count'))] == ['two', 'three', 'four']
        assert qml('toasts.visible')
        qml('toastModel.clear()')
        bridge.receive('error', {'text': 'Something broke. ' * 40})
        settle()
        assert qml('toastModel.count') == 0 and item('errorBanner').isVisible()
        # Cut to three lines, the banner's button says there is more; the status bar points at it.
        assert item('errorDetails').property('text') == 'Show full error'
        assert item('statusMessage').property('text') == 'Error — see the message above'
        qml('errorMessage = ""')
        settle()
        assert item('statusMessage').property('text') == ''
        # Copying confirms itself only when something was copied.
        qml('root.copied(desktop.copy())')
        assert qml('toastModel.count') == 0
        bridge.model.select(0, False, False)
        qml('root.copied(desktop.copy())')
        assert qml('toastModel.count') == 1 and qml('toastModel.get(0).text') == 'Copied to the clipboard'
        assert QGuiApplication.clipboard().text().endswith('Alpha')
        # The toast goes by itself.
        QTest.qWait(3300)
        assert qml('toastModel.count') == 0
        # Disabled text reads clearly dimmer than secondary text in every theme.
        for theme in ('dark', 'light', 'contrast'):
            window.setProperty('themeChoice', theme)
            assert qml('Math.abs(disabledFg.hslLightness - muted.hslLightness)') > .15, theme
        assert not [w for w in warnings if 'Main.qml' in w], warnings
    finally:
        shiboken6.delete(engine)

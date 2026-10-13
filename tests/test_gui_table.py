"""Track table: selection, keyboard row, sorting, columns and the QML interactions on top of them."""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
pytest.importorskip('PySide6')
from PySide6.QtCore import QPointF, Qt

from dj_digger.gui.model import COLUMNS, TrackModel
from tests.test_gui import row


@pytest.fixture(scope='module')
def app():
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle
    QQuickStyle.setStyle('Basic')
    return QGuiApplication.instance() or QGuiApplication([])


def named(key, title, artist=''):
    return dict(row(key, title), artist=artist)


def test_shift_selects_the_range_and_ctrl_shift_adds_it(app):
    model = TrackModel()
    model.replace([row(str(i), f'T{i}') for i in range(8)])
    model.select(1)
    model.select(3, False, True)
    assert model.keys() == ['1', '2', '3']
    # A shorter range replaces the longer one instead of piling up.
    model.select(2, False, True)
    assert model.keys() == ['1', '2']
    model.select(6, True, False)
    assert model.keys() == ['1', '2', '6']
    # Ctrl+Shift keeps what was selected and adds anchor..row (the anchor is now 6).
    model.select(7, True, True)
    assert model.keys() == ['1', '2', '6', '7']
    assert model.cursorRow == 7


def test_range_without_a_visible_anchor_starts_at_the_keyboard_row(app):
    model = TrackModel()
    model.replace([row(str(i), f'T{i}') for i in range(6)])
    model.select(4)
    model.selectAll()
    model.select(2, False, True)
    assert model.keys() == ['2', '3', '4']
    # The anchor's track filtered out: the range starts where the keyboard row is.
    model.select(5)
    model.filter('T1 ', '', False)
    model.filter('', '', False)
    model.anchor = 'gone'
    model.select(3, False, True)
    assert model.keys() == ['3', '4', '5']
    # Nothing to start from at all: the row itself.
    fresh = TrackModel()
    fresh.replace([row('a', 'A'), row('b', 'B')])
    assert fresh.cursorRow == -1
    fresh.select(1, False, True)
    assert fresh.keys() == ['b'] and fresh.anchor == 'b'


def test_keyboard_row_follows_its_track_through_sort_and_filter(app):
    model = TrackModel()
    model.replace([row('c', 'Charlie'), row('a', 'Alpha'), row('b', 'Beta')])
    model.select(0)
    assert model.cursorRow == 0
    model.sortBy(2)
    assert model.cursorRow == 2 and model.visible[2]['key'] == 'c'
    model.filter('alpha', '', False)
    assert model.cursorRow == -1
    model.filter('', '', False)
    assert model.cursorRow == 2
    model.selectAll()
    assert model.cursorRow == 2 and model.anchor == 'c'


def test_sort_cycles_back_to_source_order_with_stable_ties(app):
    model = TrackModel()
    rows = [named('1', 'Zulu', 'B'), named('2', 'Alpha', 'B'), named('3', 'Mike', 'A'), dict(named('4', 'Echo', 'A'), bpm=90)]
    model.replace(rows)
    bpm = COLUMNS.index('bpm')
    model.sortBy(bpm)
    # Equal BPMs fall back to artist, then title, ascending in both directions.
    assert [r['key'] for r in model.visible] == ['4', '3', '2', '1']
    model.sortBy(bpm)
    assert [r['key'] for r in model.visible] == ['3', '2', '1', '4'] and model.sortReverse
    model.sortBy(bpm)
    assert [r['key'] for r in model.visible] == ['1', '2', '3', '4'] and model.sortColumn == -1
    model.setSort(2, True)
    assert [r['key'] for r in model.visible] == ['1', '3', '4', '2']
    model.setSort(99, True)
    assert model.sortColumn == -1 and not model.sortReverse


def test_selection_repaints_only_the_rows_that_changed(app):
    model = TrackModel()
    model.replace([row(str(i), f'T{i}') for i in range(10)])
    model.select(2)
    spans = []
    model.dataChanged.connect(lambda first, last, roles: spans.append((first.row(), last.row())))
    model.select(5, False, True)
    assert spans == [(3, 5)]
    spans.clear()
    model.select(8, True, False)
    assert spans == [(8, 8)]
    spans.clear()
    model.select(0)
    assert spans == [(0, 0), (2, 5), (8, 8)]


def test_played_status_and_local_format_and_comment(app):
    from dj_digger.services.local_library import media_track
    model = TrackModel()
    model.replace([dict(row('a', 'A'), status='played', format='MP3 320 kbps', comment='Peak time')])
    assert model.data(model.index(0, 0)) == 'Played'
    assert model.longestText(COLUMNS.index('format')) == 'MP3 320 kbps'
    assert model.headerName(COLUMNS.index('comment')) == 'Comment'

    class Db:
        def media_values(self, media_id):
            return {}
    metadata = {'bit_rate': 320000, 'duration': 60, 'tags': {'comment': 'Peak\ntime  edit'}}
    track = media_track(Db(), dict(id='m', path='/music/a.mp3', signature='s', metadata_json=json.dumps(metadata)))
    assert track.audio_format == 'MP3 320 kbps' and track.comment == 'Peak time edit'
    unprobed = media_track(Db(), dict(id='m', path='/music/b.flac', signature='s', metadata_json='{}'))
    assert unprobed.audio_format == 'FLAC' and unprobed.comment == ''


def test_table_settings_are_saved(app, tmp_path):
    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            pass
        def submit(self, *args):
            pass
    bridge = Bridge(None, backend_factory=PassiveBackend, home_path=tmp_path)
    bridge.saveSettings({'sortColumn': 4, 'sortReverse': True, 'compactRows': True, 'other': 1})
    assert bridge._settings == {'sortColumn': 4, 'sortReverse': True, 'compactRows': True}


def test_qml_table_keyboard_drag_density_and_now_playing(app, tmp_path, monkeypatch):
    import shiboken6
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtTest import QSignalSpy, QTest

    from dj_digger.gui.bridge import Bridge

    class PassiveBackend:
        def __init__(self, emit):
            self.calls = []
        def submit(self, *args):
            self.calls.append(args)

    # Settings from a version with ten columns: Format and Comment arrive hidden, the sort comes back.
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    (tmp_path / 'config' / 'dj-digger').mkdir(parents=True)
    (tmp_path / 'config' / 'dj-digger' / 'gui.json').write_text(json.dumps({
        'columnWidths': [110, 150, 280, 85, 64, 64, 58, 95, 70, 140], 'hiddenColumns': [3],
        'columnOrder': list(range(10)), 'sortColumn': 2, 'sortReverse': True}), encoding='utf-8')
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

        def settle():
            painted = QSignalSpy(window.frameSwapped)
            window.update()
            assert painted.wait(3000)

        def qml(expression):
            return QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]
        assert window.property('hiddenColumns').toVariant() == [3, 10, 11]
        assert window.property('columnOrder').toVariant() == list(range(12))
        assert bridge.table.sort_column == 2 and bridge.table.reverse
        qml('desktop.model.setSort(-1, false)')
        bridge.receive('ready', {})
        bridge.receive('view', dict(title='Local', source='fixture', local=True, generation=1,
                                    rows=[row(str(i), f'Track {i}') for i in range(60)]))
        settle()
        table = window.findChild(QQuickItem, 'trackTable')
        table.forceActiveFocus()
        # Down with no keyboard row starts at the first row.
        assert qml('table.keyboardRow') == -1
        QTest.keyClick(window, Qt.Key_Down)
        assert bridge.table.keys() == ['0'] and qml('table.keyboardRow') == 0
        QTest.keyClick(window, Qt.Key_Down, Qt.ShiftModifier)
        QTest.keyClick(window, Qt.Key_Down, Qt.ShiftModifier)
        assert bridge.table.keys() == ['0', '1', '2']
        QTest.keyClick(window, Qt.Key_Up, Qt.ShiftModifier)
        assert bridge.table.keys() == ['0', '1']
        # Press on one row and drag to another: the rows between are selected.
        x = qml('table.columnWidth(0) + table.columnWidth(1) + 40')
        start = table.mapToScene(QPointF(x, 34 * 4 + 17)).toPoint()
        end = table.mapToScene(QPointF(x, 34 * 7 + 17)).toPoint()
        QTest.mousePress(window, Qt.LeftButton, pos=start)
        assert bridge.table.keys() == ['4']
        QTest.mouseMove(window, table.mapToScene(QPointF(x, 34 * 5 + 17)).toPoint())
        QTest.mouseMove(window, end)
        QTest.mouseRelease(window, Qt.LeftButton, pos=end)
        assert bridge.table.keys() == ['4', '5', '6', '7'] and qml('table.keyboardRow') == 7
        # Page Down moves by the rows that fit at the current density.
        page = qml('table.pageRows')
        QTest.keyClick(window, Qt.Key_PageDown)
        assert qml('table.keyboardRow') == 7 + page
        window.setProperty('compactRows', True)
        settle()
        assert qml('table.rowHeight') == 26 and qml('table.pageRows') > page
        # The loaded track's row carries the bar; the play state sits in a fixed slot before the name.
        bridge.receive('audio', dict(key='0', title='Track 0', playing=True, duration=180, position=3))
        qml('table.contentY = 0')
        settle()

        def visible_items(name):
            found, stack = [], [window.contentItem()]
            while stack:
                item = stack.pop()
                if item.objectName() == name and item.isVisible():
                    found.append(item)
                stack.extend(item.childItems())
            return found
        assert len(visible_items('nowPlayingBar')) == 1
        assert len(visible_items('playState')) == 1
        assert not warnings, warnings
    finally:
        shiboken6.delete(engine)

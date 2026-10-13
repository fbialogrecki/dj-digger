"""The track toolbar: store pills, status and BPM/key/genre filters, and bulk confirmations."""
from pathlib import Path

import pytest

pytest.importorskip('PySide6')
import test_gui
from PySide6.QtCore import Qt
from test_gui import online_rows, row, wait_event

from dj_digger.gui.model import TrackModel

# The desktop's Qt application and worker fixtures, shared with the main GUI tests.
app, backend = test_gui.app, test_gui.backend


def track(key, stores, status='new', **values):
    return {**row(key, key), 'stores': stores, 'status': status, **values}


def keys(model):
    return [r['key'] for r in model.visible]


def test_store_filter_takes_several_store_names(app):
    model = TrackModel()
    model.replace([track('a', 'bandcamp'), track('b', 'beatport'), track('c', 'gate', storeNames='Hypeddit'),
                   track('d', 'bandcamp, beatport'), track('e', 'no-link')])
    assert {s['name']: s['count'] for s in model.stores} == {'bandcamp': 2, 'beatport': 2, 'Hypeddit': 1, 'no-link': 1}
    model.filter('', {'stores': ['beatport', 'Hypeddit']})
    assert keys(model) == ['b', 'c', 'd']
    # Names, not substrings or positions: a store missing from the view matches nothing.
    model.filter('', {'stores': ['beat']})
    assert keys(model) == []
    model.filter('', {})
    assert keys(model) == ['a', 'b', 'c', 'd', 'e']


def test_status_bpm_key_and_genre_filters_combine(app):
    model = TrackModel()
    model.replace([track('a', '', 'new', bpm=124, camelot='8A', genre='Deep House'),
                   track('b', '', 'played', bpm=128, camelot='9A', genre='Techno'),
                   track('c', '', 'got', bpm=126, camelot='8A', genre='House'),
                   track('d', '', 'skip', bpm='', camelot='', genre='house')])
    model.filter('', {'statuses': ['new', 'played', 'opened']})
    assert keys(model) == ['a', 'b']
    model.filter('', {'bpmMin': 125})
    assert keys(model) == ['b', 'c']
    # A track without a BPM never passes a BPM bound.
    model.filter('', {'bpmMax': 126})
    assert keys(model) == ['a', 'c']
    model.filter('', {'keys': ['8A', '7A']})
    assert keys(model) == ['a', 'c']
    model.filter('', {'genre': ' HOUSE '})
    assert keys(model) == ['a', 'c', 'd']
    model.filter('', {'genre': 'house', 'statuses': ['got'], 'bpmMin': 120, 'bpmMax': 130})
    assert keys(model) == ['c']


def test_a_status_change_reapplies_the_status_filter(app):
    model = TrackModel()
    model.replace([track('a', ''), track('b', '')])
    model.filter('', {'statuses': ['new']})
    model.update_rows([track('a', '', 'got'), track('b', '')])
    assert keys(model) == ['b']


def test_open_honours_store_names_including_a_gate_site(backend):
    from dj_digger.models import LinkRecord
    worker, events = backend
    rows = online_rows(1)
    rows[0].records.append(LinkRecord('gate', rows[0].track, 'https://hypd.it/x', ''))
    worker.rows = rows
    opened = []
    worker.services.opening.open_one = lambda url, *args: opened.append(url)
    worker.submit('open', {'keys': ['1'], 'generation': 0, 'stores': ['Hypeddit', 'beatport']})
    wait_event(events, 'rows')
    assert opened == ['https://beatport.com/track/0']
    opened.clear()
    worker.submit('open', {'keys': ['1'], 'generation': 0, 'stores': ['Hypeddit']})
    wait_event(events, 'rows')
    assert opened == ['https://hypd.it/x']


def test_more_than_ten_downloads_ask_first(backend):
    worker, events = backend
    worker.rows = online_rows(11)
    worker.services.config.first_run = False
    worker.submit('download', {'keys': [str(i) for i in range(1, 12)], 'generation': 0})
    question = wait_event(events, 'question')
    assert question['title'] == 'Download' and question['fields'][0]['args'] == [11]
    worker.answer(question['id'], None)
    assert wait_event(events, 'message')['text'] == 'Cancelled'


def test_filter_rows_keeps_the_chosen_statuses():
    from dj_digger.playlist import filter_rows

    class Item:
        def __init__(self, key, haystack):
            self.key, self.haystack = key, haystack
    rows = [Item('a', 'alpha'), Item('b', 'beta')]
    status = {'a': 'got', 'b': 'new'}
    assert filter_rows(rows, '', ['new'], lambda r: status[r.key]) == [rows[1]]
    assert filter_rows(rows, 'alp', (), lambda r: status[r.key]) == [rows[0]]


def test_toolbar_pills_status_filter_and_view_change(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF, QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
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
    engine.rootContext().setContextProperty('systemLanguage', 'en')
    engine.load(QUrl.fromLocalFile(str(Path('dj_digger/gui/qml/Main.qml').resolve())))
    try:
        window = engine.rootObjects()[0]
        window.setProperty('animations', False)
        window.setWidth(1400)
        def qml(expression):
            return QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]
        def settle():
            QTest.qWait(200)  # past the filter timer
            painted = QSignalSpy(window.frameSwapped)
            window.update()
            assert painted.wait(3000)
        def find(item, name):
            if item.objectName() == name:
                return item
            return next((found for child in item.childItems() if (found := find(child, name))), None)
        def pill(name):
            found = find(window.contentItem(), 'storePill-' + name)
            return found if found is not None and found.isVisible() else None
        def click(item):
            point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, pos=point)
            settle()
        bridge.receive('ready', {})
        rows = [track('a', 'bandcamp'), track('b', 'beatport'), track('c', 'bandcamp, beatport', 'got')]
        bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=1, rows=rows))
        settle()
        assert pill('all').property('checked')
        assert [pill(n).property('text') for n in ('bandcamp', 'beatport')] == ['bandcamp 2', 'beatport 2']
        click(pill('beatport'))
        assert bridge.table.store_filter == {'beatport'} and keys(bridge.table) == ['b', 'c']
        click(pill('bandcamp'))
        assert bridge.table.store_filter == {'beatport', 'bandcamp'}
        assert window.findChild(QQuickItem, 'filterCount').property('text') == '3 / 3'
        click(pill('all'))
        assert bridge.table.store_filter == set()
        # H toggles the status filter between all and unhandled; the combo box follows.
        window.findChild(QQuickItem, 'trackTable').forceActiveFocus()
        QTest.keyClick(window, Qt.Key_H)
        settle()
        assert keys(bridge.table) == ['a', 'b'] and qml('statusBox.currentIndex') == 1
        # A store that leaves the view leaves the filter; another view clears search, keeps the status.
        qml('toggleStore("beatport")')
        qml('search.text = "a"')
        settle()
        bridge.receive('view', dict(title='Playlist', source='sc:1', local=False, generation=2, rows=[track('a', 'bandcamp')]))
        settle()
        assert bridge.table.store_filter == set()
        assert qml('search.text') == 'a'
        bridge.receive('view', dict(title='Other', source='sc:2', local=False, generation=3, rows=rows))
        settle()
        assert qml('search.text') == '' and bridge.table.statuses == {'new', 'played', 'opened'}
        # Nothing left to show: the empty state offers to clear the filters.
        qml('search.text = "zzz"')
        settle()
        clear = window.findChild(QQuickItem, 'clearFilters')
        assert clear.isVisible()
        click(clear)
        assert keys(bridge.table) == ['a', 'b', 'c'] and qml('statusFilter') == ''
        assert not [w for w in warnings if 'Main.qml' in w], warnings
    finally:
        import shiboken6
        shiboken6.delete(engine)

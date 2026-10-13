"""The desktop sidebar: playlist and folder menus, counts, filter, sections and drag and drop."""
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from tests.test_gui import app, backend, wait_event  # noqa: F401  (fixtures)


def test_headers_count_active_tracks_and_a_renamed_title_survives_refresh(tmp_path):
    from dj_digger.db import Database

    db = Database(tmp_path / 'library.db')
    db.save_crate(dict(source='crate', title='SoundCloud', tracks=[dict(id=1), dict(id=2), dict(id=3)], partial=False))
    db.set_removed_tracks('crate', db.crate_generation('crate'), ['2'], True)
    [header] = db.list_crate_headers()
    assert header['count'] == 2
    assert db.rename_crate('crate', 'Mine') and not db.rename_crate('missing', 'x')
    updated = db.remember_collection(dict(source='crate', title='SoundCloud again', tracks=[dict(id=1)], partial=False),
                                     db.snapshot_generations())
    assert updated['title'] == 'Mine' and db.list_crate_headers()[0]['title'] == 'Mine'


def test_local_playlists_take_dropped_tracks_rename_and_duplicate(backend, tmp_path):  # noqa: F811
    worker, events = backend
    folder = tmp_path / 'music'
    folder.mkdir()
    for name in ('One - A.wav', 'Two - B.wav'):
        (folder / name).write_bytes(b'fixture')
    worker.submit('folder', {'path': str(folder)})
    view = wait_event(events, 'view')
    wait_event(events, 'rows')
    first, second = (row['key'] for row in view['rows'])
    target = dict(generation=view['generation'])

    def answer(values):
        question = wait_event(events, 'question')
        worker.answer(question['id'], values)
        return question

    worker.submit('save_playlist', dict(target, keys=[first]))
    answer({'title': 'Mine'})
    [mine] = wait_event(events, 'sidebar')['items']
    assert (mine['title'], mine['count']) == ('Mine', 1)
    worker.submit('add_to_playlist', dict(target, source=mine['source'], keys=[second]))
    assert wait_event(events, 'sidebar')['items'][0]['count'] == 2
    worker.submit('rename_playlist', {'source': mine['source']})
    assert answer({'title': 'Ours'})['fields'][0]['value'] == 'Mine'
    assert [item['title'] for item in wait_event(events, 'sidebar')['items']] == ['Ours']
    worker.submit('duplicate_playlist', {'source': mine['source']})
    answer({'title': 'Copy'})
    items = wait_event(events, 'sidebar')['items']
    assert sorted((item['title'], item['count']) for item in items) == [('Copy', 2), ('Ours', 2)]
    assert len({item['source'] for item in items}) == 2
    worker.submit('add_to_playlist', dict(target, source='https://soundcloud.com/a/sets/b', keys=[first]))
    kind, value = events.get(timeout=10)
    while kind != 'error':
        kind, value = events.get(timeout=10)
    assert 'local playlist' in value['text']


def test_folder_menu_pins_unpins_and_unhides_the_folder_it_names(backend, tmp_path):  # noqa: F811
    worker, events = backend
    folder = tmp_path / 'Sets'
    folder.mkdir()
    worker.submit('pin', {'path': str(folder)})
    assert wait_event(events, 'sidebar')['pinned'] == [str(folder)]
    worker.submit('unpin', {'path': str(folder) + os.sep})
    assert wait_event(events, 'sidebar')['pinned'] == []
    worker.submit('hide_folder', {'path': str(folder)})
    assert wait_event(events, 'sidebar')['hidden'] == [str(folder)]
    worker.submit('unhide_folder', {'path': str(folder)})
    assert wait_event(events, 'sidebar')['hidden'] == []


def test_music_folders_rescan_and_match_names_below(app, tmp_path):  # noqa: F811
    from PySide6.QtTest import QTest

    from dj_digger.gui.directories import NESTED, OWN, DirectoryModel, MusicFolders

    (tmp_path / 'House' / 'Deep').mkdir(parents=True)
    (tmp_path / 'House' / 'Deep' / 'set.mp3').write_bytes(b'')
    source = DirectoryModel()
    folders = MusicFolders(source)
    try:
        source.setRootPath(str(tmp_path))
        folders.set_roots([str(tmp_path / 'House')])
        deadline = time.monotonic() + 5
        while folders.music(str(tmp_path / 'House')) != NESTED and time.monotonic() < deadline:
            QTest.qWait(10)
        assert folders.music(str(tmp_path / 'House')) == NESTED
        (tmp_path / 'House' / 'loose.wav').write_bytes(b'')
        folders.rescan(str(tmp_path / 'House'))
        deadline = time.monotonic() + 5
        while folders.music(str(tmp_path / 'House')) != OWN and time.monotonic() < deadline:
            QTest.qWait(10)
        assert folders.music(str(tmp_path / 'House')) == OWN
        def house():  # Proxy indexes do not outlive a refilter.
            return folders.mapFromSource(source.index(str(tmp_path / 'House')))
        source.fetchMore(source.index(str(tmp_path / 'House')))
        deadline = time.monotonic() + 5
        while not folders.rowCount(house()) and time.monotonic() < deadline:
            QTest.qWait(10)
        assert folders.matches(house(), 'DEEP') and folders.matches(house(), 'hou') and folders.matches(house(), '')
        assert not folders.matches(house(), 'techno')
    finally:
        folders.stop()


def test_qml_sidebar_sections_filter_keyboard_menus_and_track_drop(app, tmp_path, monkeypatch):  # noqa: F811
    import shiboken6
    from PySide6.QtCore import QPointF, QUrl
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression
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
    (home / 'Music').mkdir()
    (home / 'Sets').mkdir()
    engine = QQmlApplicationEngine()
    bridge = Bridge(engine, backend_factory=PassiveBackend, home_path=home)
    engine.rootContext().setContextProperty('desktop', bridge)
    engine.rootContext().setContextProperty('systemLanguage', 'en')
    engine.load(QUrl.fromLocalFile(str(Path('dj_digger/gui/qml/Main.qml').resolve())))
    errors = []
    engine.warnings.connect(lambda warnings: errors.extend(str(w) for w in warnings))
    try:
        window = engine.rootObjects()[0]
        window.setProperty('animations', False)
        bridge.receive('ready', {})
        local, remote = 'local-playlist:1', 'https://soundcloud.com/a/sets/x'
        bridge.receive('sidebar', {'items': [dict(title='Mine', source=local, count=3), dict(title='Set', source=remote, count=12)],
                                   'pinned': [str(home / 'Sets')], 'hidden': [str(home / 'Music' / 'Secret')]})

        def qml(expression):
            return QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]

        def find_item(item, name):
            if item.objectName() == name:
                return item
            return next((found for child in item.childItems() if (found := find_item(child, name))), None)

        def settle():
            painted = QSignalSpy(window.frameSwapped)
            window.update()
            assert painted.wait(3000)

        def center(item):
            return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()

        scene = window.contentItem()
        settle()
        mine = find_item(scene, 'playlist-' + local)
        assert find_item(mine, 'playlistCount').property('text') == '3'
        # The filter narrows playlists and folders by name.
        qml('sidebarFilter.text = "SET"')
        assert qml('playlistList.count') == 1
        assert find_item(scene, 'root-' + str(home / 'Sets')).parentItem().isVisible()
        assert not find_item(scene, 'root-downloads').parentItem().isVisible()
        qml('sidebarFilter.text = ""')
        # A header folds its section away and back; the state is what gui.json saves.
        header = find_item(scene, 'playlistsHeader')
        QTest.mouseClick(window, Qt.LeftButton, pos=center(header))
        assert not find_item(scene, 'playlistList').isVisible() and qml('root.sidebarCollapsed.join()') == 'playlists'
        QTest.mouseClick(window, Qt.LeftButton, pos=center(header))
        assert find_item(scene, 'playlistList').isVisible() and qml('root.sidebarCollapsed.join()') == ''
        settle()
        # Arrows and Enter open a playlist as a click does.
        qml('playlistList.forceActiveFocus()')
        QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Up)
        QTest.keyClick(window, Qt.Key_Return)
        assert bridge.backend.calls[-1] == ('load', {'source': local})
        # A long press opens the menu without opening the playlist.
        calls = len(bridge.backend.calls)
        mine = find_item(scene, 'playlist-' + local)
        QTest.mousePress(window, Qt.LeftButton, pos=center(mine))
        QTest.qWait(1200)
        QTest.mouseRelease(window, Qt.LeftButton, pos=center(mine))
        assert qml('playlistMenu.visible') and qml('playlistMenu.source') == local
        assert len(bridge.backend.calls) == calls
        qml('playlistMenu.close()')
        # The menus act on the row they were opened on.
        qml(f'root.playlistMenuAt("{remote}")')
        assert qml('playlistMenu.itemAt(1).visible') and not qml('playlistMenu.itemAt(3).visible')
        qml('playlistMenu.itemAt(1).triggered(); playlistMenu.close()')
        assert bridge.backend.calls[-1] == ('refresh', {'source': remote})
        qml(f'root.playlistMenuAt("{local}")')
        qml('playlistMenu.itemAt(2).triggered(); playlistMenu.close()')
        assert bridge.backend.calls[-1] == ('rename_playlist', {'source': local})
        sets = (home / 'Sets').as_posix()
        qml(f'root.folderMenuAt("{sets}")')
        assert qml('folderMenu.pinned') and qml('folderMenu.itemAt(8).text') == 'Unpin folder'
        qml('folderMenu.itemAt(8).triggered(); folderMenu.close()')
        assert bridge.backend.calls[-1][0] == 'unpin' and Path(bridge.backend.calls[-1][1]['path']) == home / 'Sets'
        qml(f'root.folderMenuAt("{sets}"); folderMenu.itemAt(1).triggered(); folderMenu.close()')
        assert bridge.backend.calls[-1][0] == 'analyze_folder' and Path(bridge.backend.calls[-1][1]['path']) == home / 'Sets'
        # Hidden folders come back from the Folders menu.
        assert qml('hiddenFoldersMenu.itemAt(1).text') == str(home / 'Music' / 'Secret')
        qml('hiddenFoldersMenu.itemAt(1).triggered()')
        assert bridge.backend.calls[-1] == ('unhide_folder', {'path': str(home / 'Music' / 'Secret')})
        # Selected local tracks dragged onto a local playlist join it.
        rows = [dict(key=f'local:{i}', title=f'Track {i}', artist='', genre='', bpm='', keySignature='', classicKey='', camelot='',
                     year='', label='', duration=60000, status='new', stores='', storeNames='', local=True, path='', search='')
                for i in range(3)]
        bridge.receive('view', dict(title=str(home / 'Music'), source='', local=True, generation=5, rows=rows))
        settle()
        table = find_item(scene, 'trackTable')
        start = table.mapToScene(QPointF(300, 50)).toPoint()
        target = center(find_item(scene, 'playlist-' + local))
        # A drag starts from a selected row; from an unselected one it selects rows instead.
        QTest.mouseClick(window, Qt.LeftButton, pos=start)
        QTest.qWait(700)  # Not a double click, which would play.
        QTest.mousePress(window, Qt.LeftButton, pos=start)
        for step in range(1, 11):
            QTest.mouseMove(window, start + (target - start) * step / 10)
            QTest.qWait(10)
        QTest.mouseRelease(window, Qt.LeftButton, pos=target)
        assert bridge.backend.calls[-1] == ('add_to_playlist', dict(source=local, keys=['local:1'], generation=5))
        # A folder dropped on the Folders section from a file manager is added.
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(home / 'Sets'))])
        point = center(find_item(scene, 'foldersHeader'))
        for event in (QDragEnterEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier),
                      QDragMoveEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier),
                      QDropEvent(QPointF(point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)):
            QGuiApplication.sendEvent(window, event)
        assert bridge.backend.calls[-1][0] == 'add_folder' and Path(bridge.backend.calls[-1][1]['path']) == home / 'Sets'
        assert not errors
    finally:
        shiboken6.delete(engine)

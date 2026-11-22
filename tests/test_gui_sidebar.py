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

from tests.test_gui import app, backend, next_of, wait_event  # noqa: F401  (fixtures)


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


def test_a_local_playlist_exports_exactly_its_files_from_the_sidebar(backend, tmp_path):  # noqa: F811
    worker, events = backend
    folder = tmp_path / 'music'
    folder.mkdir()
    for name in ('One - A.wav', 'Two - B.wav', 'Three - C.wav'):
        (folder / name).write_bytes(b'fixture')
    worker.submit('folder', {'path': str(folder)})
    view = wait_event(events, 'view')
    keys = {Path(row['path']).name: row['key'] for row in view['rows']}
    worker.submit('save_playlist', dict(generation=view['generation'], keys=[keys['One - A.wav'], keys['Three - C.wav']]))
    question = wait_event(events, 'question')
    worker.answer(question['id'], {'title': 'Set'})
    [mine] = wait_event(events, 'sidebar')['items']
    # Not opened, nothing selected: the playlist's own files are what gets exported.
    worker.submit('export', {'source': mine['source']})
    form = wait_event(events, 'question')
    worker.answer(form['id'], dict({f['name']: f['value'] for f in form['fields']}, folder=str(tmp_path / 'out'), mode='copy'))
    review = wait_event(events, 'question')
    assert review['title'] == 'Review export'
    plan = review['fields'][0]['value']
    assert 'One - A.wav →' in plan and 'Three - C.wav →' in plan and 'Two - B.wav' not in plan
    worker.answer(review['id'], None)
    assert wait_event(events, 'message')['text'] == 'Cancelled'
    worker.submit('export', {'source': 'https://soundcloud.com/a/sets/b'})
    while (event := events.get(timeout=10))[0] != 'error':
        pass
    assert event[1]['text'] == 'Only local playlists can be exported'


def test_soundcloud_and_local_tracks_share_an_ordered_playlist(backend, tmp_path):  # noqa: F811
    from dj_digger.crate_models import CrateRecord
    from dj_digger.models import Track

    worker, events = backend
    state = worker.services.state
    snapshot = 'sc-snapshot:test'
    remotes = [Track(title, f'https://soundcloud.com/owner/{title.lower()}', id=i)
               for i, title in enumerate(('First', 'Second'), start=1)]
    state.db.save_crate(CrateRecord(snapshot, 'Snapshot', remotes, preserve_order=True).to_json())
    state.set('2', 'skip')
    worker.submit('refresh', {'source': snapshot})
    view = wait_event(events, 'view')
    assert [r['key'] for r in view['rows']] == ['1', '2']
    worker.submit('save_playlist', dict(generation=view['generation'], keys=['1', '2'], order=['2', '1']))
    question = wait_event(events, 'question')
    assert [(f['name'], f['value']) for f in question['fields']] == [
        ('title', ''), ('count', 'Selected tracks: {0}'), ('soundcloud', False), ('sharing', 'private')]
    worker.answer(question['id'], {'title': 'Set'})
    mine = next(i for i in wait_event(events, 'sidebar')['items'] if i['title'] == 'Set')

    folder = tmp_path / 'music'
    folder.mkdir()
    (folder / 'File.wav').write_bytes(b'audio')
    worker.submit('folder', {'path': str(folder)})
    folder_view = wait_event(events, 'view')
    [local] = folder_view['rows']
    worker.submit('add_to_playlist', dict(source=mine['source'], keys=[local['key']], generation=folder_view['generation']))
    wait_event(events, 'sidebar')
    worker.submit('load', {'source': mine['source']})
    mixed = wait_event(events, 'view')
    assert mixed['localPlaylist'] and mixed['mixedPlaylist'] and not mixed['local']
    assert [(r['key'], r['media']) for r in mixed['rows']] == [('2', False), ('1', False), (local['key'], True)]
    worker.submit('delete_files', dict(keys=[local['key']], generation=mixed['generation']))
    assert next_of(events, 'error')[1]['text'] == 'Open the folder to delete files'
    worker.submit('remove', dict(keys=['2', local['key']], generation=mixed['generation']))
    worker.answer(wait_event(events, 'question')['id'], {})
    remaining = next(i for i in wait_event(events, 'sidebar')['items'] if i['source'] == mine['source'])
    assert (remaining['count'], remaining['mediaCount']) == (1, 0)
    assert [r['key'] for r in wait_event(events, 'view')['rows']] == ['1']
    worker.submit('delete_playlist', {'source': snapshot})
    worker.answer(wait_event(events, 'question')['id'], {})
    assert [i['source'] for i in wait_event(events, 'sidebar')['items']] == [mine['source']]
    assert state.get('2') == 'skip' and (folder / 'File.wav').read_bytes() == b'audio'


def test_remote_playlist_creation_failure_keeps_the_new_local_playlist(backend):  # noqa: F811
    from dj_digger.crate_models import CrateRecord
    from dj_digger.models import Track

    worker, events = backend
    source = 'sc-snapshot:failure'
    track = Track('Track', 'https://soundcloud.com/owner/track', id=1)
    worker.services.state.db.save_crate(CrateRecord(source, 'Snapshot', [track]).to_json())

    async def fail(title, sharing, tracks):
        assert (title, sharing, [t.key for t in tracks]) == ('Local', 'public', ['1'])
        raise ValueError('Provider refused the request')

    worker.create_soundcloud_playlist = fail
    worker.submit('load', {'source': source})
    view = wait_event(events, 'view')
    worker.submit('save_playlist', dict(generation=view['generation'], keys=['1']))
    question = wait_event(events, 'question')
    worker.answer(question['id'], {'title': 'Local', 'soundcloud': True, 'sharing': 'public'})
    mine = next(i for i in wait_event(events, 'sidebar')['items'] if i['title'] == 'Local')
    assert mine['source'].startswith('local-playlist:') and mine['count'] == 1
    assert next_of(events, 'error')[1]['args'] == ['Local', 'Provider refused the request']


def test_soundcloud_write_stays_in_chosen_browser_when_accounts_differ(backend, monkeypatch):  # noqa: F811
    import asyncio
    from concurrent.futures import Future
    from unittest.mock import Mock

    from dj_digger.models import Track

    worker, events = backend
    owner = 'https://soundcloud.com/owner'
    track = Track('First', owner + '/first', id=1)
    client = worker.services.client
    monkeypatch.setattr(client, '_get', lambda path: {'id': 11, 'permalink_url': owner})
    monkeypatch.setattr(client, 'hydrate_tracks', lambda ids, cancel: [track])
    browsers = [Mock(compatible=True, last_active=index, hello=dict(browser=name, permissions=True, playlistWrite=True))
                for index, name in enumerate(('chrome', 'firefox'))]
    link = Mock()
    link.connections.return_value = browsers
    monkeypatch.setattr(worker.services, 'browser_link', link)
    session = browsers[1].session.return_value
    session.error = None
    reply = Future()
    reply.set_exception(ValueError('Different browser account'))
    session.submit.return_value = (1, reply)
    result = asyncio.run_coroutine_threadsafe(worker.write_soundcloud_tracks([track], title='Set'), worker.loop)
    choose = wait_event(events, 'question')
    assert choose['title'] == 'Choose the SoundCloud browser'
    worker.answer(choose['id'], {'browser': '1'})
    confirm = wait_event(events, 'question')
    assert {f['name']: f['value'] for f in confirm['fields']} == dict(account=owner, browser='firefox', playlist='Set', sharing='Private')
    worker.answer(confirm['id'], {})
    review = wait_event(events, 'question')
    assert review['fields'][0]['args'] == [0, 0]
    worker.answer(review['id'], {})
    with pytest.raises(ValueError, match='Different browser account'):
        result.result(timeout=5)
    browsers[0].session.assert_not_called()
    session.submit.assert_called_once()
    session.request.assert_called_once_with('playlist.review', {})
    session.close.assert_called_once()


def test_only_current_account_playlists_are_remote_destinations(backend, monkeypatch):  # noqa: F811
    from dj_digger import auth
    from dj_digger.crate_models import CrateRecord
    from dj_digger.models import Track

    worker, events = backend
    owned = 'https://soundcloud.com/owner/sets/owned'
    other = 'https://soundcloud.com/other/sets/owned'
    snapshot = 'sc-snapshot:owned'
    track = Track('Track', 'https://soundcloud.com/owner/track', id=1)
    for source, owner in ((owned, 1), (other, 2), (snapshot, 1)):
        record = CrateRecord(source, source, [track], provider_id=5, owner_id=owner)
        worker.services.state.db.save_crate(record.to_json())
    auth.save_token('fixture-account', 'owner', 1)
    worker.submit('account')
    items = wait_event(events, 'sidebar')['items']
    assert {item['source'] for item in items if item['owned']} == {owned}

    async def add(source, tracks):
        assert source == owned and [t.key for t in tracks] == ['1']
        worker.send('message', {'text': 'Called remote adapter'})

    worker.add_to_soundcloud_playlist = add
    worker.submit('load', {'source': snapshot})
    view = wait_event(events, 'view')
    selected = dict(generation=view['generation'], keys=['1'])
    worker.submit('add_to_playlist', dict(selected, source=owned))
    assert wait_event(events, 'message')['text'] == 'Called remote adapter'
    worker.submit('add_to_playlist', dict(selected, source=snapshot))
    assert 'you own' in next_of(events, 'error')[1]['text']

    monkeypatch.setenv('SOUNDCLOUD_OAUTH_TOKEN', 'another-account')
    worker.submit('account')
    assert not any(item['owned'] for item in wait_event(events, 'sidebar')['items'])


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
    from PySide6.QtCore import QPoint, QPointF, QUrl
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
        sidebar = {'items': [dict(title='Mine', source=local, count=3, mediaCount=3),
                             dict(title='Set', source=remote, count=12, mediaCount=0)],
                   'pinned': [str(home / 'Sets')], 'hidden': [str(home / 'Music' / 'Secret')]}
        bridge.receive('sidebar', sidebar)

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
        assert qml('playlistMenu.itemAt(1).visible') and not qml('playlistMenu.itemAt(4).visible')
        # Only a local playlist's files can be exported from here.
        assert qml('playlistMenu.itemAt(5).text') == 'Export audio…' and not qml('playlistMenu.itemAt(5).visible')
        qml('playlistMenu.itemAt(1).triggered(); playlistMenu.close()')
        assert bridge.backend.calls[-1] == ('refresh', {'source': remote})
        qml(f'root.playlistMenuAt("{remote}"); playlistMenu.itemAt(2).triggered(); playlistMenu.close()')
        assert bridge.backend.calls[-1] == ('restore', {'source': remote})
        qml(f'root.playlistMenuAt("{local}")')
        qml('playlistMenu.itemAt(3).triggered(); playlistMenu.close()')
        assert bridge.backend.calls[-1] == ('rename_playlist', {'source': local})
        qml(f'root.playlistMenuAt("{local}")')
        assert qml('playlistMenu.itemAt(5).visible') and qml('playlistMenu.itemAt(5).enabled')
        qml('playlistMenu.itemAt(5).triggered(); playlistMenu.close()')
        assert bridge.backend.calls[-1] == ('export', {'source': local})
        for count in (1, 0):
            sidebar['items'][0]['mediaCount'] = count
            bridge.receive('sidebar', sidebar)
            qml(f'root.playlistMenuAt("{local}")')
            assert qml('playlistMenu.itemAt(5).text') == f'Export local files ({count})…'
            assert qml('playlistMenu.itemAt(5).enabled') == bool(count)
            qml('playlistMenu.close()')
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
        # Overflow is local to each pane and cannot move the divider or either header.
        position = qml('playlistsPane.height')
        pinned = [str(home / f'Folder {i}') for i in range(50)]
        for path in pinned:
            Path(path).mkdir()
        bridge.receive('sidebar', {'items': [dict(title=f'Set {i}', source=f'sc:{i}') for i in range(50)], 'pinned': pinned})
        settle()
        assert qml('playlistsPane.height') == position
        assert qml('playlistList.contentHeight > playlistList.height && foldersScroll.contentHeight > foldersScroll.height')
        qml('playlistList.contentY += 120')
        assert qml('playlistList.contentY') > 0 and qml('foldersScroll.contentItem.contentY') == 0
        qml('foldersScroll.contentItem.contentY += 120')
        assert qml('foldersScroll.contentItem.contentY') > 0 and qml('playlistList.contentY') == 120
        splitter = find_item(scene, 'sidebarSectionHandle')
        point = center(splitter)
        target = point + QPoint(0, 45)
        QTest.mousePress(window, Qt.LeftButton, pos=point)
        QTest.mouseMove(window, target)
        QTest.mouseRelease(window, Qt.LeftButton, pos=target)
        settle()
        assert qml('playlistsPane.height') == position + 45
        ratio = window.property('sidebarSplit')
        assert ratio > .5
        window.setHeight(window.height() + 80)
        settle()
        assert qml('playlistsPane.height') == pytest.approx(qml('sidebarSections.available') * ratio, abs=1)
        assert not errors
    finally:
        shiboken6.delete(engine)

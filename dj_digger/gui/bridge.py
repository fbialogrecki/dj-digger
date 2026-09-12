"""Small value-only boundary between the QML engine and backend thread."""
import json
import logging
import os
import threading
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QLibraryInfo,
    QModelIndex,
    QObject,
    QStandardPaths,
    Qt,
    QTranslator,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication

from ..paths import config_dir
from ..private_json import write_private_json
from .backend import Backend
from .directories import DirectoryModel
from .model import TrackModel


class Bridge(QObject):
    event = Signal(str, object)
    changed = Signal()
    rootsChanged = Signal()
    question = Signal('QVariantMap')
    dismiss = Signal(str)
    notice = Signal(str, str)
    closed = Signal()

    def __init__(self, engine, *, backend_factory=Backend, home_path=None):
        super().__init__()
        self.engine = engine
        self.table = TrackModel(self)
        self._view = dict(title='', generation=0)
        self._sidebar = []
        self._pinned = []
        self._level = 'info'
        self._default_roots = [
            str(Path(home_path) / name) if home_path is not None else
            QStandardPaths.writableLocation(location)
            for name, location in (('Downloads', QStandardPaths.DownloadLocation),
                                   ('Music', QStandardPaths.MusicLocation))
        ]
        self._directories = DirectoryModel(self)
        self._roots = []
        self._refresh_roots()
        self._folder = dict(path='', offset=0, total=0, directories=[])
        self._audio = {}
        self._busy = False
        self._message = ''
        self._ready = False
        self._volume = .8
        self._settings = {}
        self._translator = QTranslator(self)
        self._qt_translator = QTranslator(self)
        self._close_timer = None
        self.event.connect(self.receive)
        self.backend = backend_factory(self.event.emit)
        self.backend.submit('start')

    model = Property(QObject, lambda self: self.table, constant=True)
    view = Property('QVariantMap', lambda self: self._view, notify=changed)
    playlists = Property('QVariantList', lambda self: self._sidebar, notify=changed)
    level = Property(str, lambda self: self._level, notify=changed)
    directoryModel = Property(QObject, lambda self: self._directories, constant=True)
    directoryRoots = Property('QVariantList', lambda self: self._roots, notify=rootsChanged)
    folder = Property('QVariantMap', lambda self: self._folder, notify=changed)
    audio = Property('QVariantMap', lambda self: self._audio, notify=changed)
    busy = Property(bool, lambda self: self._busy, notify=changed)
    ready = Property(bool, lambda self: self._ready, notify=changed)
    volume = Property(float, lambda self: self._volume, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)

    def _refresh_roots(self):
        roots = []
        seen = set()
        for index, path in enumerate([*self._default_roots, *self._pinned]):
            if not path:
                continue
            path = str(Path(path).expanduser().absolute())
            identity = os.path.normcase(os.path.normpath(path))
            if identity in seen or Path(path).name.startswith('.'):
                continue
            model_index = self._directories.setRootPath(path)
            if self._directories.fileInfo(model_index).isHidden():
                continue
            seen.add(identity)
            roots.append(dict(path=path, kind=('downloads', 'music')[index] if index < 2 else 'custom'))
        if roots != self._roots:
            self._roots = roots
            self.rootsChanged.emit()

    @Slot(str, object)
    def receive(self, kind, values):
        if kind == 'view':
            self._view = {k: v for k, v in values.items() if k != 'rows'}
            if values.get('local'):
                self.table.store = ''
            self.table.replace(values['rows'])
        elif kind == 'rows' and values['generation'] == self._view['generation']:
            self.table.update_rows(values['rows'])
        elif kind == 'progress' and values['generation'] == self._view['generation']:
            self.table.update_progress(values['updates'])
        elif kind == 'sidebar':
            self._sidebar = values['items']
            self._pinned = values.get('pinned', [])
            self._refresh_roots()
        elif kind == 'folder':
            self._folder = values
        elif kind == 'audio':
            self._audio = values
        elif kind == 'busy':
            self._busy = values['value']
            if values['text']:
                self._message, self._level = self.tr(values['text']), 'info'
        elif kind in ('error', 'message'):
            self._message, self._level = self.tr(values['text']), kind if kind == 'error' else 'info'
            if 'args' in values:
                self._message = self._message.format(*values['args'])
            self.notice.emit(self._message, self._level)
        elif kind == 'question':
            values['info'] = values.get('ok') == 'Close'
            for name in ('title', 'body', 'ok', 'error'):
                values[name] = self.tr(values.get(name, ''))
            for item in values['fields']:
                item['label'] = self.tr(item['label'])
                item['options'] = [[value, self.tr(label)] for value, label in item.get('options', [])]
            self.question.emit(values)
        elif kind == 'dismiss':
            self.dismiss.emit(values['id'])
        elif kind == 'ready':
            self._ready = True
            self._volume = values.get('volume', self._volume)
        elif kind == 'closed':
            if self._close_timer:
                self._close_timer.cancel()
            self.closed.emit()
        self.changed.emit()

    @Slot(str)
    @Slot(str, bool)
    def action(self, name, everything=False):
        """Act on the selection; ``everything`` explicitly targets every visible row."""
        keys = self.table.keys()
        selected = bool(keys) and not everything
        if everything:
            keys = [row['key'] for row in self.table.visible]
        self.backend.submit(name, dict(keys=keys, order=[r['key'] for r in self.table.visible], selected=selected, search=self.table.search,
                                       hide=self.table.hide, store=self.table.store, generation=self._view['generation']))

    @Slot(str)
    def mark(self, status):
        self.backend.submit('mark', dict(status=status, keys=self.table.keys(), generation=self._view['generation']))

    @Slot(str)
    def load(self, source):
        self.backend.submit('load', {'source': source})

    @Slot(str)
    def deletePlaylist(self, source):
        self.backend.submit('delete_playlist', {'source': source})

    @Slot(str)
    def expandDirectory(self, path):
        index = self._directories.index(path)
        if index.isValid() and self._directories.canFetchMore(index):
            self._directories.fetchMore(index)

    @Slot(str, result=QModelIndex)
    def directoryIndex(self, path):
        return self._directories.index(path)

    @Slot(str, result=bool)
    def directoryHasChildren(self, path):
        index = self._directories.index(path)
        return index.isValid() and self._directories.hasChildren(index)

    @Slot(str)
    def addFolder(self, path):
        self.backend.submit('add_folder', {'path': self.localPath(path)})

    @Slot(QModelIndex)
    def openDirectory(self, index):
        if index.isValid() and index.model() is self._directories:
            self.openFolder(self._directories.filePath(index), 0)

    @Slot('QVariantList', int, result='QVariantList')
    def waveformLevels(self, samples, width):
        from ..waveform import column_levels
        return column_levels(samples, width)

    @Slot(str, result=str)
    def localPath(self, value):
        from PySide6.QtCore import QUrl
        url = QUrl(value)
        return url.toLocalFile() if url.isLocalFile() else value

    @Slot(str, int)
    def openFolder(self, path, offset=0):
        from PySide6.QtCore import QUrl
        url = QUrl(path)
        self.backend.submit('folder', {'path': url.toLocalFile() if url.isLocalFile() else path, 'offset': offset})

    @Slot()
    def copy(self):
        rows = [r for r in self.table.visible if r['key'] in self.table.selected]
        QGuiApplication.clipboard().setText('\n'.join(r['artist'] + ' — ' + r['title'] for r in rows))

    @Slot(int)
    def step(self, direction):
        self.backend.submit('step', {'direction': direction})

    @Slot(str, float)
    def transport(self, operation, value=0):
        self.backend.submit('transport', dict(operation=operation, value=value))

    @Slot(str, 'QVariantMap', bool)
    def answer(self, ident, values, accepted):
        self.backend.answer(ident, values if accepted else None)

    @Slot(str)
    def language(self, language):
        app = QGuiApplication.instance()
        app.removeTranslator(self._translator)
        app.removeTranslator(self._qt_translator)
        if language == 'pl':
            if self._qt_translator.load('qtbase_pl', QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
                app.installTranslator(self._qt_translator)
            path = Path(__file__).parent / 'translations' / 'pl.qm'
            if self._translator.load(str(path)):
                app.installTranslator(self._translator)
        self.engine.retranslate()
        self.table.headerDataChanged.emit(Qt.Horizontal, 0, 9)
        if self.table.visible:
            self.table.dataChanged.emit(self.table.index(0, 0), self.table.index(len(self.table.visible) - 1, 0))
        self.changed.emit()

    @Slot(result='QVariantMap')
    def settings(self):
        try:
            value = json.loads((config_dir() / 'gui.json').read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @Slot('QVariantMap')
    def saveSettings(self, values):
        # Only this fixed presentation shape is persisted; no service preferences.
        allowed = {'width', 'height', 'language', 'theme', 'sidebarWidth', 'sidebarVisible', 'columnWidths'}
        self._settings = {k: v for k, v in values.items() if k in allowed}

    @Slot()
    def close(self):
        if self._close_timer is not None:
            return
        def deadline():
            logging.getLogger(__name__).warning('Desktop shutdown exceeded the existing three-second grace')
            from ..media_processes import terminate_owned
            terminate_owned()
            os._exit(0)
        self._close_timer = threading.Timer(3.0, deadline)
        self._close_timer.daemon = True
        self._close_timer.start()
        # Tiny private settings file is written on the backend executor, not Qt.
        async def finish():
            await self.backend.io(write_private_json, config_dir() / 'gui.json', self._settings)
        def shutdown():
            async def save_and_close():
                try:
                    await finish()
                finally:
                    await self.backend._close()
            self.backend.loop.create_task(save_and_close())
        self.backend.loop.call_soon_threadsafe(shutdown)

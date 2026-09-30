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
    audioChanged = Signal()
    playingChanged = Signal()
    playlistsChanged = Signal()
    waveformChanged = Signal()
    artworkChanged = Signal()
    nowPlayingChanged = Signal()
    beatsChanged = Signal()
    importChanged = Signal()
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
        self._waveform = []
        self._waveform_key = ''
        self._artwork = {}
        self._now_playing = {}
        self._beats = {}
        self._import = {}
        self._busy = False
        self._message = ''
        self._busy_message = ''
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
    playlists = Property('QVariantList', lambda self: self._sidebar, notify=playlistsChanged)
    level = Property(str, lambda self: self._level, notify=changed)
    directoryModel = Property(QObject, lambda self: self._directories, constant=True)
    directoryRoots = Property('QVariantList', lambda self: self._roots, notify=rootsChanged)
    folder = Property('QVariantMap', lambda self: self._folder, notify=changed)
    # Ten position ticks a second must not re-evaluate every view binding, nor carry the waveform.
    audio = Property('QVariantMap', lambda self: self._audio, notify=audioChanged)
    # The loaded track and whether it plays change rarely; table cells bind to these, not to every tick.
    audioKey = Property(str, lambda self: self._audio.get('key', ''), notify=playingChanged)
    playing = Property(bool, lambda self: bool(self._audio.get('playing')), notify=playingChanged)
    waveform = Property('QVariantList', lambda self: self._waveform, notify=waveformChanged)
    waveformKey = Property(str, lambda self: self._waveform_key, notify=waveformChanged)
    # The loaded track's record label as a PNG data URL, '' while loading or without a picture.
    artwork = Property(str, lambda self: self._artwork.get('image', ''), notify=artworkChanged)
    # Artist, name, BPM and key of the loaded track; empty once another track loads.
    nowPlaying = Property('QVariantMap', lambda self: self._now_playing, notify=nowPlayingChanged)
    # Detected hits in queued audio: pulses as [track time, amplitude]
    # and the seconds per beat; empty once another track loads.
    beats = Property('QVariantMap', lambda self: self._beats, notify=beatsChanged)
    # Stage, done and total of a playlist import while it runs; empty otherwise.
    importProgress = Property('QVariantMap', lambda self: self._import, notify=importChanged)
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
        elif kind == 'rows':
            if values['generation'] == self._view['generation']:
                self.table.update_rows(values['rows'])
            return
        elif kind == 'progress':
            if values['generation'] == self._view['generation']:
                self.table.update_progress(values['updates'])
            return
        elif kind == 'sidebar':
            self._sidebar = values['items']
            self._pinned = values.get('pinned', [])
            self._refresh_roots()
            self.playlistsChanged.emit()
        elif kind == 'folder':
            self._folder = values
        elif kind == 'audio':
            before = (self._audio.get('key', ''), bool(self._audio.get('playing')))
            self._audio = values
            if values.get('key', '') != self._waveform_key:
                self._set_waveform('', [])
            if self._artwork and values.get('key', '') != self._artwork['key']:
                self._artwork = {}
                self.artworkChanged.emit()
            if self._now_playing and values.get('key', '') != self._now_playing['key']:
                self._now_playing = {}
                self.nowPlayingChanged.emit()
            if self._beats and values.get('key', '') != self._beats['key']:
                self._beats = {}
                self.beatsChanged.emit()
            if before != (values.get('key', ''), bool(values.get('playing'))):
                self.playingChanged.emit()
            self.audioChanged.emit()
            return
        elif kind == 'waveform':
            self._set_waveform(values['key'], values['samples'])
            return
        elif kind == 'artwork':
            if values.get('key') == self._audio.get('key'):
                self._artwork = values
                self.artworkChanged.emit()
            return
        elif kind == 'importProgress':
            self._import = dict(values, stage=self.tr(values['stage'])) if values else {}
            self.importChanged.emit()
            return
        elif kind == 'beats':
            if values != self._beats:
                self._beats = values
                self.beatsChanged.emit()
            return
        elif kind == 'nowPlaying':
            if values != self._now_playing:
                self._now_playing = values
                self.nowPlayingChanged.emit()
            return
        elif kind == 'busy':
            self._busy = values['value']
            if values['text']:
                self._message, self._level = self.tr(values['text']), 'info'
                self._busy_message = self._message
            elif not self._busy and self._level == 'info' and self._message == self._busy_message:
                # The operation label goes when it ends; a result it reported stays.
                self._message = ''
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

    def _set_waveform(self, key, samples):
        if (key, samples) != (self._waveform_key, self._waveform):
            self._waveform_key, self._waveform = key, samples
            self.waveformChanged.emit()

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

    @Slot(str, str, result=bool)
    def samePath(self, first, second):
        # QFileSystemModel reports forward slashes; the backend echoes native paths.
        normalize = lambda value: os.path.normcase(os.path.normpath(self.localPath(value)))  # noqa: E731
        return bool(first) and bool(second) and normalize(first) == normalize(second)

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
        allowed = {'width', 'height', 'language', 'theme', 'sidebarWidth', 'sidebarVisible', 'columnWidths', 'hiddenColumns', 'columnOrder',
                   'keyNotation', 'animations', 'pulseOffset'}
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

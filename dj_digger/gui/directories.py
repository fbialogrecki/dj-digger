"""Native asynchronous directory listing with accurate leaf indicators."""
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Property, QDir, QModelIndex, QSortFilterProxyModel, QTimer, Signal
from PySide6.QtWidgets import QFileSystemModel

from ..services.local_library import _is_audio_entry

# A folder with no audio among this many entries below it is treated as having none.
SCAN_LIMIT = 20000


class DirectoryModel(QFileSystemModel):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._revision = 0
        self.setReadOnly(True)
        self.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        # Windows dot directories need name filtering in addition to Hidden attributes.
        self.setNameFilters(['[!.]*'])
        self.setNameFilterDisables(False)
        self.directoryLoaded.connect(self._changed)
        self.rowsInserted.connect(self._changed)
        self.rowsRemoved.connect(self._changed)

    def _changed(self, *args):
        self._revision += 1
        self.changed.emit()

    revision = Property(int, lambda self: self._revision, notify=changed)

    def hasChildren(self, parent=QModelIndex()):
        if parent.isValid():
            if self.canFetchMore(parent):
                self.fetchMore(parent)  # QFileSystemModel enumerates in its own thread.
            return self.rowCount(parent) > 0
        return super().hasChildren(parent)


NONE, NESTED, OWN = 'none', 'nested', 'own'


def has_audio(path, stop):
    """OWN when ``path`` holds an audio file itself, NESTED when only a visible folder
    below it does, else NONE; the folder itself is read whole before any below it."""
    pending, seen = deque([path]), 0
    while pending and not stop.is_set():
        current = pending.popleft()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > SCAN_LIMIT or stop.is_set():
                        return NONE
                    if entry.name.startswith('.'):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                    elif _is_audio_entry(entry):
                        return OWN if current == path else NESTED
        except OSError:
            continue
    return NONE


class MusicFolders(QSortFilterProxyModel):
    """The folder tree without folders that hold no music, checked off the UI thread.

    A folder shows once a scan finds audio in it or below it. Roots and the folders
    above them always show, so every root keeps its place in the tree, unless hidden.
    """
    changed = Signal()
    _found = Signal(str, str)

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.setSourceModel(source)
        self._audio = {}
        self._pending = set()
        self._roots = set()
        self._hidden = set()
        self._revision = 0
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(2, thread_name_prefix='music-folders')
        self._found.connect(self._remember)
        # Results arrive one by one; the tree is filtered again once they settle.
        self._settle = QTimer(self, singleShot=True, interval=100, timeout=self._refilter)
        # ponytail: folders without music of their own are checked again every 30 s, so a download
        # landing in one shows up; a folder that loses its music keeps showing until the next start.
        self._recheck = QTimer(self, interval=30000, timeout=self._check_empty)
        self._recheck.start()
        source.changed.connect(self._source_changed)

    revision = Property(int, lambda self: self._revision, notify=changed)

    @staticmethod
    def _identity(path):
        return os.path.normcase(os.path.normpath(path))

    def set_roots(self, paths):
        self._roots = {self._identity(path) for path in paths}
        # Roots always show, but a click needs to know whether they hold music themselves.
        for path in paths:
            if self._identity(path) not in self._audio:
                self._scan(self._identity(path), path)
        self._refilter()

    def set_hidden(self, paths):
        self._hidden = {self._identity(path) for path in paths}
        self._refilter()

    def is_hidden(self, path):
        return self._identity(path) in self._hidden

    def music(self, path):
        """OWN, NESTED or NONE once the folder was checked, '' before."""
        return self._audio.get(self._identity(path), '')

    def filterAcceptsRow(self, row, parent):
        index = self.sourceModel().index(row, 0, parent)
        path = self.sourceModel().filePath(index)
        identity = self._identity(path)
        if identity in self._hidden:
            return False
        if any(root == identity or root.startswith(identity.rstrip(os.sep) + os.sep) for root in self._roots):
            return True
        if identity not in self._audio:
            self._scan(identity, path)
            return False
        return self._audio[identity] != NONE

    def _scan(self, identity, path):
        if identity not in self._pending and not self._stop.is_set():
            self._pending.add(identity)
            self._pool.submit(lambda: self._stop.is_set() or self._found.emit(identity, has_audio(path, self._stop)))

    def _remember(self, identity, found):
        self._pending.discard(identity)
        if self._audio.get(identity) != found:
            self._audio[identity] = found
            self._settle.start()

    def _check_empty(self):
        for identity, found in list(self._audio.items()):
            if found != OWN:
                self._scan(identity, identity)

    def _refilter(self):
        self.beginFilterChange()
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        self._source_changed()

    def _source_changed(self, *args):
        self._revision += 1
        self.changed.emit()

    def path(self, index):
        return self.sourceModel().filePath(self.mapToSource(index))

    def stop(self):
        self._stop.set()
        self._recheck.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

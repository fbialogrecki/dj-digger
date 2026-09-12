"""Native asynchronous directory listing with accurate leaf indicators."""
from PySide6.QtCore import Property, QDir, QModelIndex, Signal
from PySide6.QtWidgets import QFileSystemModel


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

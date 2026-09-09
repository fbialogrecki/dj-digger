"""Qt-owned table state; external values are always displayed as plain text."""
from PySide6.QtCore import Property, QAbstractTableModel, QModelIndex, Qt, Signal, Slot

COLUMNS = ('status', 'artist', 'title', 'genre', 'bpm', 'keySignature', 'year', 'label', 'duration', 'stores')
HEADERS = ('Status', 'Artist', 'Title', 'Genre', 'BPM', 'Key', 'Year', 'Label', 'Time', 'Stores')
STATUS_LABELS = {'new': '\u00b7', 'opened': '\u25cb Opened', 'got': '\u2713 Got', 'skip': '\u2717 Skipped'}


class TrackModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.visible = []
        self.selected = set()
        self.search = ''
        self.store = ''
        self.hide = False
        self.sort_column = -1
        self.reverse = False
        self.anchor = ''
        self.progress = {}

    def roleNames(self):
        return {Qt.DisplayRole: b'display', Qt.UserRole: b'trackKey', Qt.UserRole + 1: b'chosen',
                Qt.UserRole + 2: b'status', Qt.UserRole + 3: b'progress', Qt.UserRole + 4: b'local'}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.visible)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.visible):
            return None
        row = self.visible[index.row()]
        if role == Qt.UserRole:
            return row['key']
        if role == Qt.UserRole + 1:
            return row['key'] in self.selected
        if role == Qt.UserRole + 2:
            return row['status']
        if role == Qt.UserRole + 3:
            return self.progress.get(row['key'], -1.0)
        if role == Qt.UserRole + 4:
            return bool(row.get('local'))
        if role == Qt.DisplayRole:
            name = COLUMNS[index.column()]
            value = row.get(name, '')
            if name == 'status':
                return f"{self.progress[row['key']] * 100:.0f}%" if row['key'] in self.progress else self.tr(STATUS_LABELS.get(value, value))
            if name == 'duration':
                seconds = int(value or 0) // 1000
                return f'{seconds//60}:{seconds%60:02d}'
            if name == 'bpm' and value != '':
                return f'{float(value):g}'
            return str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.tr(HEADERS[section])
        return super().headerData(section, orientation, role)

    def replace(self, rows):
        self.rows = rows
        self.progress.clear()
        self.selected.intersection_update(r['key'] for r in rows)
        self.refilter()

    def update_progress(self, updates):
        self.progress.update(updates)
        for i, row in enumerate(self.visible):
            if row['key'] in updates:
                self.dataChanged.emit(self.index(i, 0), self.index(i, 0))

    def update_rows(self, rows):
        changed = set(self.progress)
        self.progress.clear()
        if [r['key'] for r in rows] != [r['key'] for r in self.rows]:
            self.replace(rows)
            return
        changed.update(a['key'] for a, b in zip(self.rows, rows) if a != b)
        self.rows = rows
        if self.hide or self.search or self.store or self.sort_column >= 0:
            self.refilter()
        else:
            self.visible = list(rows)
            for i, row in enumerate(self.visible):
                if row['key'] in changed:
                    self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLUMNS)-1))
            self.changed.emit()

    def refilter(self):
        tokens = self.search.casefold().split()
        visible = [r for r in self.rows if all(t in r['search'].casefold() for t in tokens)
                   and (not self.store or self.store in r['stores'])
                   and not (self.hide and r['status'] in ('got', 'skip'))]
        if self.sort_column >= 0:
            name = COLUMNS[self.sort_column]
            numeric = name in {'bpm', 'duration', 'year'}
            visible.sort(key=lambda r: float(r.get(name) or 0) if numeric else str(r.get(name, '')).casefold(), reverse=self.reverse)
        self.beginResetModel()
        self.visible = visible
        self.endResetModel()
        self.changed.emit()

    def store_counts(self):
        counts = {}
        for row in self.rows:
            for store in filter(None, row['stores'].split(', ')):
                counts[store] = counts.get(store, 0) + 1
        return [dict(name=name, count=count) for name, count in sorted(counts.items(), key=lambda item: -item[1])]

    def summary(self):
        got = sum(r['status'] == 'got' for r in self.rows)
        skipped = sum(r['status'] == 'skip' for r in self.rows)
        return dict(visible=len(self.visible), total=len(self.rows), got=got, skipped=skipped, selected=len(self.keys()))

    stores = Property('QVariantList', store_counts, notify=changed)
    counts = Property('QVariantMap', summary, notify=changed)
    sortColumn = Property(int, lambda self: self.sort_column, notify=changed)
    sortReverse = Property(bool, lambda self: self.reverse, notify=changed)

    @Slot(str, str, bool)
    def filter(self, text, store, hide):
        self.search, self.store, self.hide = text, store, hide
        self.refilter()

    @Slot(int)
    def sortBy(self, column):
        self.reverse = not self.reverse if self.sort_column == column else False
        self.sort_column = column
        self.refilter()

    @Slot(int, bool, bool)
    def select(self, row, toggle=False, extend=False):
        if not 0 <= row < len(self.visible):
            return
        key = self.visible[row]['key']
        if extend and self.anchor:
            anchor = next((i for i, r in enumerate(self.visible) if r['key'] == self.anchor), row)
            self.selected.update(r['key'] for r in self.visible[min(row, anchor):max(row, anchor)+1])
        elif toggle:
            self.selected.symmetric_difference_update({key})
            self.anchor = key
        else:
            self.selected = {key}
            self.anchor = key
        self.selection_changed()

    @Slot()
    def selectAll(self):
        self.selected = {r['key'] for r in self.visible}
        self.selection_changed()

    @Slot()
    def clearSelection(self):
        self.selected = set()
        self.selection_changed()

    def selection_changed(self):
        if self.visible:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.visible)-1, len(COLUMNS)-1), [Qt.UserRole + 1])
        self.changed.emit()

    def keys(self):
        return [r['key'] for r in self.visible if r['key'] in self.selected]

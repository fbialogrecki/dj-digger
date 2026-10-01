"""Qt-owned table state; external values are always displayed as plain text."""
from PySide6.QtCore import Property, QAbstractTableModel, QModelIndex, Qt, Signal, Slot

COLUMNS = ('status', 'artist', 'title', 'genre', 'bpm', 'keySignature', 'year', 'label', 'duration', 'stores')
HEADERS = ('Status', 'Artist', 'Title', 'Genre', 'BPM', 'Key', 'Year', 'Label', 'Time', 'Stores')
# Bulk status changes skip the row flash: a hundred rows lighting up is noise, not feedback.
FLASH_LIMIT = 100
STATUS_LABELS = {'new': '\u00b7', 'opened': '\u25cb Opened', 'got': '\u2713 Got', 'skip': '\u2717 Skipped'}


def camelot_order(row):
    """Keys sort around the Camelot wheel (1A, 1B, 2A ...); unknown keys last, by text."""
    code = row.get('camelot', '')
    return (0, int(code[:-1]), code[-1], '') if code else (1, 0, '', str(row.get('keySignature', '')).casefold())


class TrackModel(QAbstractTableModel):
    changed = Signal()
    storesChanged = Signal()
    # Track key -> new status, for rows whose status just changed.
    statusFlashed = Signal('QVariantMap')

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
        self.key_notation = 'camelot'
        # Bindings read counts and stores many times per change; each is computed once per change.
        self._cache = {}

    def _changed(self, stores=False):
        self._cache.clear()
        if stores:
            self.storesChanged.emit()
        self.changed.emit()

    def _cached(self, name, compute):
        if name not in self._cache:
            self._cache[name] = compute()
        return self._cache[name]

    def roleNames(self):
        return {Qt.DisplayRole: b'display', Qt.UserRole: b'trackKey', Qt.UserRole + 1: b'chosen',
                Qt.UserRole + 2: b'status', Qt.UserRole + 3: b'progress', Qt.UserRole + 4: b'local',
                Qt.UserRole + 5: b'camelot'}

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
        if role == Qt.UserRole + 5:
            return row.get('camelot', '')
        if role == Qt.DisplayRole:
            name = COLUMNS[index.column()]
            value = row.get(name, '')
            if name == 'status':
                return f"{self.progress[row['key']] * 100:.0f}%" if row['key'] in self.progress else self.tr(STATUS_LABELS.get(value, value))
            if name == 'duration':
                seconds = int(value or 0) // 1000
                return f'{seconds//60}:{seconds%60:02d}'
            if name == 'bpm' and value != '':
                return f'{float(value):.1f}'.removesuffix('.0')
            if name == 'keySignature':
                return row.get('camelot' if self.key_notation == 'camelot' else 'classicKey') or str(value)
            return str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.tr(HEADERS[section])
        return super().headerData(section, orientation, role)

    @Slot(int, result=str)
    def headerName(self, column):
        return self.headerData(column, Qt.Horizontal) if 0 <= column < len(COLUMNS) else ''

    @Slot(int, result=str)
    def longestText(self, column):
        """Visible cell text with the most characters; the view measures it for fit-to-contents."""
        if not 0 <= column < len(COLUMNS):
            return ''
        return max((self.data(self.index(i, column)) for i in range(len(self.visible))), key=len, default='')

    def replace(self, rows):
        self.rows = rows
        self.progress.clear()
        self.selected.intersection_update(r['key'] for r in rows)
        self.refilter(stores=True)

    def update_progress(self, updates):
        self.progress.update(updates)
        for i, row in enumerate(self.visible):
            if row['key'] in updates:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLUMNS)-1))

    def update_rows(self, rows):
        changed = set(self.progress)
        self.progress.clear()
        if [r['key'] for r in rows] != [r['key'] for r in self.rows]:
            self.replace(rows)
            return
        changed.update(a['key'] for a, b in zip(self.rows, rows) if a != b)
        flashed = {b['key']: b['status'] for a, b in zip(self.rows, rows) if a['status'] != b['status']}
        self.rows = rows
        if self.hide or self.search or self.store or self.sort_column >= 0:
            self.refilter(stores=True)
        else:
            self.visible = list(rows)
            for i, row in enumerate(self.visible):
                if row['key'] in changed:
                    self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLUMNS)-1))
            self._changed(stores=True)
        if 0 < len(flashed) <= FLASH_LIMIT:
            self.statusFlashed.emit(flashed)

    def refilter(self, stores=False):
        tokens = self.search.casefold().split()
        visible = [r for r in self.rows if all(t in r['search'].casefold() for t in tokens)
                   and (not self.store or self.store in r['stores'])
                   and not (self.hide and r['status'] in ('got', 'skip'))]
        if self.sort_column >= 0:
            name = COLUMNS[self.sort_column]
            numeric = name in {'bpm', 'duration', 'year'}
            order = camelot_order if name == 'keySignature' else (lambda r: float(r.get(name) or 0)) if numeric \
                else (lambda r: str(r.get(name, '')).casefold())
            visible.sort(key=order, reverse=self.reverse)
        self.beginResetModel()
        self.visible = visible
        self.endResetModel()
        self._changed(stores)

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

    stores = Property('QVariantList', lambda self: self._cached('stores', self.store_counts), notify=storesChanged)
    counts = Property('QVariantMap', lambda self: self._cached('counts', self.summary), notify=changed)
    sortColumn = Property(int, lambda self: self.sort_column, notify=changed)
    sortReverse = Property(bool, lambda self: self.reverse, notify=changed)

    @Slot(str, str, bool)
    def filter(self, text, store, hide):
        self.search, self.store, self.hide = text, store, hide
        self.refilter()

    @Slot(str)
    def setKeyNotation(self, notation):
        if notation not in ('camelot', 'classic') or notation == self.key_notation:
            return
        self.key_notation = notation
        column = COLUMNS.index('keySignature')
        if self.visible:
            self.dataChanged.emit(self.index(0, column), self.index(len(self.visible) - 1, column), [Qt.DisplayRole])

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
        self._changed()

    firstSelectedKey = Property(str, lambda self: self._cached(
        'first', lambda: next((r['key'] for r in self.visible if r['key'] in self.selected), '')), notify=changed)

    def keys(self):
        return [r['key'] for r in self.visible if r['key'] in self.selected]

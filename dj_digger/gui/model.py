"""Qt-owned table state; external values are always displayed as plain text."""
from itertools import groupby

from PySide6.QtCore import Property, QAbstractTableModel, QModelIndex, Qt, Signal, Slot

COLUMNS = ('status', 'artist', 'title', 'genre', 'bpm', 'keySignature', 'year', 'label', 'duration', 'stores', 'format', 'comment')
HEADERS = ('Status', 'Artist', 'Track name', 'Genre', 'BPM', 'Key', 'Year', 'Label', 'Time', 'Stores', 'Format', 'Comment')
# Bulk status changes skip the row flash: a hundred rows lighting up is noise, not feedback.
FLASH_LIMIT = 100
# A played track is not decided on yet; the view mutes it, so the listening history still shows.
STATUS_LABELS = {'new': 'New', 'played': 'Played', 'opened': '○ Opened', 'got': '✓ Got', 'skip': '✗ Skipped'}


def clock(seconds):
    """m:ss, or h:mm:ss from an hour: a two-hour mix reads 2:20:08, not 140:08."""
    hours, rest = divmod(int(seconds), 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60}:{rest % 60:02d}'


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
        # Range selection grows from the anchor; the keyboard row is the cursor. Both are track keys,
        # so a sort or a filter never leaves them on another track.
        self.anchor = ''
        self.cursor = ''
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
                Qt.UserRole + 5: b'camelot', Qt.UserRole + 6: b'stores'}

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
        if role == Qt.UserRole + 6:
            return row.get('stores', '')
        if role == Qt.DisplayRole:
            return self.text(row, COLUMNS[index.column()])
        return None

    def text(self, row, name):
        value = row.get(name, '')
        if name == 'status':
            if row['key'] in self.progress:
                # -1 is the batch's "settled without a file"; the bar hides, the cell says so.
                done = self.progress[row['key']]
                return self.tr('✗ Failed') if done < 0 else f"{done * 100:.0f}%"
            label = STATUS_LABELS.get(value, value)
            return self.tr(label) if label else ''
        if name == 'stores':
            return row.get('storeNames', value)  # the filter keeps the categories
        if name == 'duration':
            return clock(int(value or 0) // 1000)
        if name == 'bpm' and value != '':
            return f'{float(value):.1f}'.removesuffix('.0')
        if name == 'keySignature':
            return row.get('camelot' if self.key_notation == 'camelot' else 'classicKey') or str(value)
        return str(value)

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
        # Straight from the rows: an index() and a data() call per cell froze big libraries.
        name = COLUMNS[column]
        return max((self.text(r, name) for r in self.visible), key=len, default='')

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
        # Starting a track takes its "new" away; that is not a decision worth a flash.
        flashed = {b['key']: b['status'] for a, b in zip(self.rows, rows) if a['status'] != b['status'] and b['status'] != 'played'}
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
            # Ties keep artist, then title order in either direction (Python's sort is stable).
            visible.sort(key=lambda r: (str(r.get('artist', '')).casefold(), str(r.get('title', '')).casefold()))
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
    # The keyboard row's index among the visible rows; -1 without one or while its track is filtered out.
    cursorRow = Property(int, lambda self: self._cached('cursor', lambda: self.row_of(self.cursor)), notify=changed)

    def row_of(self, key):
        return next((i for i, r in enumerate(self.visible) if r['key'] == key), -1) if key else -1

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
        """A new column sorts ascending, the second click descending, the third back to source order."""
        if self.sort_column != column:
            self.sort_column, self.reverse = column, False
        elif not self.reverse:
            self.reverse = True
        else:
            self.sort_column, self.reverse = -1, False
        self.refilter()

    @Slot(int, bool)
    def setSort(self, column, reverse):
        """Restores a saved sort; a column out of range means source order."""
        self.sort_column, self.reverse = (column, bool(reverse)) if 0 <= column < len(COLUMNS) else (-1, False)
        self.refilter()

    @Slot(int, bool, bool)
    def select(self, row, toggle=False, extend=False):
        """Click, Ctrl (toggle), Shift (anchor..row replaces the selection) or Ctrl+Shift (adds anchor..row)."""
        if not 0 <= row < len(self.visible):
            return
        before = set(self.selected)
        key = self.visible[row]['key']
        if extend:
            # Without a visible anchor (after Select all, a filter or in a new view) the range starts at the cursor.
            anchor = self.row_of(self.anchor)
            if anchor < 0:
                anchor = self.row_of(self.cursor)
                anchor = row if anchor < 0 else anchor
                self.anchor = self.visible[anchor]['key']
            span = {r['key'] for r in self.visible[min(row, anchor):max(row, anchor)+1]}
            self.selected = self.selected | span if toggle else span
        elif toggle:
            self.selected = self.selected ^ {key}
            self.anchor = key
        else:
            self.selected = {key}
            self.anchor = key
        self.cursor = key
        self.selection_changed(before)

    @Slot()
    def selectAll(self):
        before = set(self.selected)
        self.selected = {r['key'] for r in self.visible}
        if self.row_of(self.cursor) < 0 and self.visible:
            self.cursor = self.visible[0]['key']
        self.anchor = self.cursor
        self.selection_changed(before)

    @Slot()
    def invertSelection(self):
        """Select the visible rows that are not selected; hidden rows drop out of the selection."""
        before = set(self.selected)
        self.selected = {r['key'] for r in self.visible} - self.selected
        self.selection_changed(before)

    @Slot()
    def clearSelection(self):
        before = set(self.selected)
        self.selected = set()
        self.selection_changed(before)

    def selection_changed(self, before):
        # Only rows whose selection flipped repaint, with one signal per contiguous run of them.
        flipped = before ^ self.selected
        if flipped:
            for hit, run in groupby(enumerate(self.visible), key=lambda item: item[1]['key'] in flipped):
                if hit:
                    run = list(run)
                    self.dataChanged.emit(self.index(run[0][0], 0), self.index(run[-1][0], len(COLUMNS)-1), [Qt.UserRole + 1])
        self._changed()

    firstSelectedKey = Property(str, lambda self: self._cached(
        'first', lambda: next((r['key'] for r in self.visible if r['key'] in self.selected), '')), notify=changed)

    def keys(self):
        return [r['key'] for r in self.visible if r['key'] in self.selected]

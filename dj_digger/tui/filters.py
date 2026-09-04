"""Narrowing the visible rows: by store, by search text, by whether you have handled them.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

from textual.widgets import DataTable, Input

from ..links import CATEGORY_NAMES
from ..models import LinkRecord
from ..state import GOT, NEW, OPENED, SKIP
from .rows import Row

# What ``t`` cycles through, in order. The last three only when their column
# is switched on in Settings.
SORT_ORDER = ("title", "time", "genre", "status", "store", "bpm", "key", "year")
SORT_BASE = frozenset({"title", "time", "genre", "status", "store"})
_STATUS_RANK = {NEW: 0, OPENED: 1, SKIP: 2, GOT: 3}
SORT_KEYS = {
    "title": lambda _: (lambda row: row.track.label.lower()),
    "time": lambda _: (lambda row: row.track.duration),
    "genre": lambda _: (lambda row: row.track.genre_label.lower()),
    "status": lambda app: (lambda row: _STATUS_RANK.get(app.status_of(row), 0)),
    "store": lambda _: (
        lambda row: min(
            (CATEGORY_NAMES.index(c) for c in row.categories if c in CATEGORY_NAMES),
            default=len(CATEGORY_NAMES),
        )
    ),
    "bpm": lambda _: (lambda row: row.track.bpm or 0.0),
    "key": lambda _: (lambda row: row.track.key_signature.lower()),
    "year": lambda _: (lambda row: row.track.release_year or 0),
}
# Which table column carries each sort key's arrow.
SORT_COLUMN = {
    "title": "Track", "time": "Time", "genre": "Genre", "status": "mark",
    "store": "Stores", "bpm": "BPM", "key": "Key", "year": "Year",
}


class FilterMixin:
    """Narrowing the visible rows: by store, by search text, by whether you have handled them."""

    def soft_matching_rows(self) -> list[Row]:
        """What search and hide-handled left, before the store filter.

        Split out because the store legend counts these: see ``_store_line``.
        """

        # Every word must appear somewhere, in any order: "techno dub" finds a
        # dub techno track whether the genre or the title says so.
        tokens = self.search_term.lower().split()
        rows = []
        for row in self.rows:
            if self.hide_handled and self.status_of(row) in (GOT, SKIP):
                continue
            if tokens:
                haystack = row.haystack
                if not all(token in haystack for token in tokens):
                    continue
            rows.append(row)
        return rows

    def matching_rows(self) -> list[Row]:
        rows = [
            row
            for row in self.soft_matching_rows()
            if not self.store_filters
            or any(cat in row.categories for cat in self.store_filters)
        ]
        if self.sort_key:
            # sorted is stable, so the crate's own order survives inside ties.
            rows.sort(key=SORT_KEYS[self.sort_key](self), reverse=self.sort_reverse)
        return rows

    def targets(self) -> list[Row]:
        """What a whole-list action works on: the selection, else every row shown."""

        if self.selected:
            return [row for row in self.visible_rows if row.track.key in self.selected]
        return list(self.visible_rows)

    def selected_rows(self) -> list[Row]:
        return [row for row in self.visible_rows if row.track.key in self.selected]

    def status_of(self, row: Row) -> str:
        return self.state.get(row.track.key)

    def record_to_open(self, row: Row) -> LinkRecord:
        """The link ``o`` would follow: the filtered store, else the best one."""

        if self.store_filters:
            for cat in self.present:
                if cat in self.store_filters:
                    chosen = row.record_for(cat)
                    if chosen is not None:
                        return chosen
        return row.records[0]

    def current_row(self) -> Row | None:
        table = self.query_one("#tracks", DataTable)
        if not self.visible_rows:
            return None
        index = table.cursor_row
        if 0 <= index < len(self.visible_rows):
            return self.visible_rows[index]
        return None

    def _apply_store_filter(self, category: str) -> None:
        if not category:
            self.store_filters.clear()
        else:
            if category in self.store_filters:
                self.store_filters.remove(category)
            else:
                self.store_filters.add(category)
        self._pending_open = None
        self.refresh_rows(keep_cursor=False)

    def action_filter_index(self, index: int) -> None:
        """``0`` clears the filter, ``1``-``9`` toggle the nth store in this crate."""

        if index == 0:
            self._apply_store_filter("")
            return
        if index <= len(self.present):
            self._apply_store_filter(self.present[index - 1])
        else:
            self.notify(f"This playlist has no store {index}", timeout=2)

    # Sorting

    def action_sort_next(self) -> None:
        """Cycle the sort: source order, then each key in turn."""

        options = [None, *self._sort_options()]
        current = options.index(self.sort_key) if self.sort_key in options else 0
        self.sort_key = options[(current + 1) % len(options)]
        self.sort_reverse = False
        self._resort()

    def action_sort_flip(self) -> None:
        if self.sort_key is None:
            self.notify("Press t to choose what to sort by first", timeout=3)
            return
        self.sort_reverse = not self.sort_reverse
        self._resort()

    def _sort_options(self) -> list[str]:
        enabled = {name for name, _header, _width in self.enabled_columns()}
        return [name for name in SORT_ORDER if name in SORT_BASE or name in enabled]

    def _resort(self) -> None:
        self.refresh_rows(keep_cursor=False)
        self._paint_headers()
        label = self.sort_key or "playlist order"
        self.notify(f"Sorted by {label}{' (reversed)' if self.sort_reverse else ''}", timeout=2)

    # Selection

    def action_toggle_select(self) -> None:
        row = self.current_row()
        if row is None:
            return
        index = self.query_one("#tracks", DataTable).cursor_row
        if row.track.key in self.selected:
            self.selected.discard(row.track.key)
        else:
            self.selected.add(row.track.key)
            self._anchor = index
        self._paint_row(index)
        self.update_status()

    def action_select_range(self) -> None:
        """Select from the row selected last to the cursor, inclusive."""

        row = self.current_row()
        if row is None:
            return
        cursor = self.query_one("#tracks", DataTable).cursor_row
        start = self._anchor if self._anchor is not None else cursor
        low, high = sorted((start, cursor))
        for index in range(low, min(high, len(self.visible_rows) - 1) + 1):
            self.selected.add(self.visible_rows[index].track.key)
            self._paint_row(index)
        self._anchor = cursor
        self.update_status()

    def action_select_visible(self) -> None:
        if self.selected >= {row.track.key for row in self.visible_rows}:
            self.selected.clear()
            self.notify("Selection cleared", timeout=2)
        else:
            self.selected.update(row.track.key for row in self.visible_rows)
        self.refresh_rows()

    def clear_selection(self) -> None:
        if not self.selected:
            return
        self.selected.clear()
        self._anchor = None
        self.refresh_rows()

    def action_toggle_handled(self) -> None:
        self.hide_handled = not self.hide_handled
        self.refresh_rows(keep_cursor=False)

    def action_start_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("visible")
        search.focus()

    def action_leave_search(self) -> None:
        """Escape in the search box: back to the list, the filter still applied.

        Clearing it is one more Escape from the table; typing a term and losing
        it on the way back to the rows was the old behaviour.
        """

        search = self.query_one("#search", Input)
        if not self.search_term:
            search.remove_class("visible")
        self.query_one("#tracks", DataTable).focus()

    def action_clear_filters(self) -> None:
        """Escape peels one layer at a time: selection, then search, then the filters."""

        search = self.query_one("#search", Input)
        if self.selected:
            self.clear_selection()
        elif self.search_term or search.has_class("visible"):
            search.value = ""
            search.remove_class("visible")
            self.search_term = ""
            self.refresh_rows(keep_cursor=False)
        else:
            self.store_filters.clear()
            self.hide_handled = False
            self._pending_open = None
            self.refresh_rows(keep_cursor=False)
        self.query_one("#tracks", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        self.search_term = event.value
        self.refresh_rows(keep_cursor=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        self.query_one("#tracks", DataTable).focus()

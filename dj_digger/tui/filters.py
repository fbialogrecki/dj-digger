"""Narrowing the visible rows: by store, by search text, by whether you have handled them.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging

from textual.widgets import DataTable, Input

from ..models import LinkRecord
from ..state import GOT, SKIP
from .rows import Row

LOGGER = logging.getLogger(__name__)


class FilterMixin:
    """Narrowing the visible rows: by store, by search text, by whether you have handled them."""

    def soft_matching_rows(self) -> list[Row]:
        """What search and hide-handled left, before the store filter.

        Split out because the store legend counts these: see ``_store_line``.
        """

        term = self.search_term.strip().lower()
        rows = []
        for row in self.rows:
            if self.hide_handled and self.status_of(row) in (GOT, SKIP):
                continue
            if term and term not in row.track.label.lower():
                continue
            rows.append(row)
        return rows

    def matching_rows(self) -> list[Row]:
        return [
            row
            for row in self.soft_matching_rows()
            if not self.store_filters
            or any(cat in row.categories for cat in self.store_filters)
        ]

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
        self._pending_open_all = False
        self.refresh_rows(keep_cursor=False)

    def action_filter_index(self, index: int) -> None:
        """``0`` clears the filter, ``1``-``9`` toggle the nth store in this crate."""

        if index == 0:
            self._apply_store_filter("")
            return
        if index <= len(self.present):
            self._apply_store_filter(self.present[index - 1])
        else:
            self.notify(f"This crate has no store {index}", timeout=2)

    def action_cycle_store(self, step: int) -> None:
        """Step through the stores this crate actually has, plus 'all'."""

        if not self.present:
            return
        options = [""] + self.present
        current_single = list(self.store_filters)[0] if len(self.store_filters) == 1 else ""
        current = options.index(current_single) if current_single in options else 0
        next_cat = options[(current + step) % len(options)]
        self.store_filters.clear()
        if next_cat:
            self.store_filters.add(next_cat)
        self._pending_open_all = False
        self.refresh_rows(keep_cursor=False)

    def action_toggle_handled(self) -> None:
        self.hide_handled = not self.hide_handled
        self.refresh_rows(keep_cursor=False)

    def action_start_search(self) -> None:
        search = self.query_one("#search", Input)
        search.add_class("visible")
        search.focus()

    def action_clear_filters(self) -> None:
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        self.search_term = ""
        self.store_filters.clear()
        self.hide_handled = False
        self._pending_open_all = False
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

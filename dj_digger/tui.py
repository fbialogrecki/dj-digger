"""Interactive crate browser.

Opening every link at once means 282 browser tabs on a big playlist, which is
not a workflow. This screen lets you walk the list, open one link at a time,
filter down to a single store and mark what you already own - and the marks
survive between runs because they live in ``state.TrackState``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Input, Static

from . import browser as browser_module
from . import links as links_module
from .models import LinkRecord
from .state import GOT, NEW, OPENED, SKIP, TrackState

STATUS_STYLES = {
    NEW: ("new", "white"),
    OPENED: ("opened", "yellow"),
    SKIP: ("skipped", "bright_black"),
    GOT: ("got it", "bold green"),
}
CATEGORY_KEYS = {
    str(index + 1): category for index, category in enumerate(links_module.CATEGORY_NAMES)
}
OPEN_ALL_CONFIRM_THRESHOLD = 20


@dataclass
class Row:
    position: int
    record: LinkRecord


class DiggerApp(App):
    CSS = """
    #status {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    #search {
        display: none;
    }
    #search.visible {
        display: block;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("o,enter", "open_link", "Open"),
        Binding("g", "mark_got", "Got it"),
        Binding("s", "mark_skip", "Skip"),
        Binding("u", "mark_new", "Unmark"),
        Binding("a", "open_visible", "Open all"),
        Binding("slash", "start_search", "Search"),
        Binding("h", "toggle_handled", "Hide handled"),
        Binding("e", "export", "Export"),
        Binding("escape", "clear_filters", "Clear filters", show=False),
        Binding("0", "filter_store('')", "All stores", show=False),
        Binding("q", "quit", "Quit"),
    ] + [
        Binding(key, f"filter_store({category!r})", category, show=False)
        for key, category in CATEGORY_KEYS.items()
    ]

    def __init__(
        self,
        records: Sequence[LinkRecord],
        *,
        state: Optional[TrackState] = None,
        crate_title: str = "",
        browser: str = "default",
        export_format: str = "json",
        export_path: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.rows: List[Row] = [
            Row(position=index + 1, record=record) for index, record in enumerate(records)
        ]
        self.state = state or TrackState()
        self.crate_title = crate_title or "crate"
        self.browser = browser
        self.export_format = export_format
        self.export_path = export_path
        self.store_filter: str = ""
        self.search_term: str = ""
        self.hide_handled: bool = False
        self.visible_rows: List[Row] = []
        self._pending_open_all = False

    # Layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="status")
        yield Input(placeholder="Filter by artist or title", id="search")
        yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "dj-digger"
        self.sub_title = self.crate_title
        table = self.query_one("#tracks", DataTable)
        table.add_column("#", width=4)
        table.add_column("Track", width=44)
        table.add_column("Store", width=12)
        table.add_column("Link", width=30)
        table.add_column("Status", width=7)
        self.refresh_rows()
        table.focus()

    # Filtering

    def matching_rows(self) -> List[Row]:
        term = self.search_term.strip().lower()
        rows = []
        for row in self.rows:
            record = row.record
            if self.store_filter and record.category != self.store_filter:
                continue
            if self.hide_handled and self.status_of(row) in (GOT, SKIP):
                continue
            if term and term not in record.track.label.lower():
                continue
            rows.append(row)
        return rows

    def status_of(self, row: Row) -> str:
        return self.state.get(row.record.track.key)

    def refresh_rows(self, *, keep_cursor: bool = True) -> None:
        table = self.query_one("#tracks", DataTable)
        previous = table.cursor_row if keep_cursor else 0
        self.visible_rows = self.matching_rows()

        table.clear()
        for row in self.visible_rows:
            record = row.record
            status = self.status_of(row)
            label, style = STATUS_STYLES[status]
            is_missing = record.link_text == links_module.NO_STORE_LINK
            table.add_row(
                Text(str(row.position), style="bright_black"),
                Text(record.track.label, style="bright_black" if status == SKIP else ""),
                Text(record.category, style="bright_black" if is_missing else "cyan"),
                Text(
                    "-" if is_missing else _short_url(record.link_url),
                    style="bright_black" if is_missing else "",
                ),
                Text(label, style=style),
            )

        if self.visible_rows:
            table.move_cursor(row=min(previous, len(self.visible_rows) - 1))
        self.update_status()

    def update_status(self) -> None:
        counts: Dict[str, int] = {status: 0 for status in STATUS_STYLES}
        for row in self.rows:
            counts[self.status_of(row)] += 1

        pieces = [
            f"{len(self.visible_rows)}/{len(self.rows)} links",
            f"got {counts[GOT]}",
            f"skipped {counts[SKIP]}",
            f"opened {counts[OPENED]}",
        ]
        if self.store_filter:
            pieces.append(f"store: {self.store_filter}")
        if self.search_term:
            pieces.append(f"search: {self.search_term!r}")
        if self.hide_handled:
            pieces.append("hiding handled")
        self.query_one("#status", Static).update("  ".join(pieces))

    # Helpers

    def current_row(self) -> Optional[Row]:
        table = self.query_one("#tracks", DataTable)
        if not self.visible_rows:
            return None
        index = table.cursor_row
        if 0 <= index < len(self.visible_rows):
            return self.visible_rows[index]
        return None

    def _set_status(self, status: str, message: str) -> None:
        row = self.current_row()
        if row is None:
            return
        self.state.set(row.record.track.key, status)
        self.notify(f"{message}: {row.record.track.label}", timeout=2)
        self.refresh_rows()
        self._advance_cursor()

    def _advance_cursor(self) -> None:
        table = self.query_one("#tracks", DataTable)
        if self.visible_rows and table.cursor_row < len(self.visible_rows) - 1:
            table.move_cursor(row=table.cursor_row + 1)

    # Actions

    def action_open_link(self) -> None:
        row = self.current_row()
        if row is None:
            return
        record = row.record
        if record.link_text == links_module.NO_STORE_LINK:
            self.notify("No store link for this track - opening it on SoundCloud", timeout=3)
        if browser_module.open_url(record.link_url, self.browser):
            if self.status_of(row) == NEW:
                self.state.set(record.track.key, OPENED)
            self.refresh_rows()
        else:
            self.notify("Could not open the link", severity="error")

    def action_mark_got(self) -> None:
        self._set_status(GOT, "Got it")

    def action_mark_skip(self) -> None:
        self._set_status(SKIP, "Skipped")

    def action_mark_new(self) -> None:
        self._set_status(NEW, "Unmarked")

    def action_open_visible(self) -> None:
        if not self.visible_rows:
            self.notify("Nothing to open", timeout=2)
            return

        count = len(self.visible_rows)
        if count > OPEN_ALL_CONFIRM_THRESHOLD and not self._pending_open_all:
            self._pending_open_all = True
            self.notify(
                f"That opens {count} tabs. Press 'a' again to confirm, "
                "or filter the list down first.",
                severity="warning",
                timeout=6,
            )
            return

        self._pending_open_all = False
        urls = [row.record.link_url for row in self.visible_rows]
        opened = browser_module.open_urls(urls, self.browser)
        for row in self.visible_rows:
            if self.status_of(row) == NEW:
                self.state.set(row.record.track.key, OPENED)
        self.notify(f"Opened {opened} links", timeout=3)
        self.refresh_rows()

    def action_filter_store(self, category: str) -> None:
        self.store_filter = category
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
        self.store_filter = ""
        self.hide_handled = False
        self._pending_open_all = False
        self.refresh_rows(keep_cursor=False)
        self.query_one("#tracks", DataTable).focus()

    def action_export(self) -> None:
        if self.export_format == "none":
            self.notify("Export is disabled for this run", timeout=3)
            return
        records = [row.record for row in self.visible_rows]
        if not records:
            self.notify("Nothing to export", timeout=2)
            return
        path = links_module.export_records(records, self.export_format, self.export_path)
        if path:
            self.notify(f"Exported {len(records)} links to {path}", timeout=4)
        else:
            self.notify("Export failed - see the log", severity="error")

    # Events

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_open_link()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        self.search_term = event.value
        self.refresh_rows(keep_cursor=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        self.query_one("#tracks", DataTable).focus()


def _short_url(url: str, limit: int = 29) -> str:
    trimmed = url.replace("https://", "").replace("http://", "")
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: limit - 1] + "\u2026"


def run_tui(
    records: Sequence[LinkRecord],
    *,
    state: Optional[TrackState] = None,
    crate_title: str = "",
    browser: str = "default",
    export_format: str = "json",
    export_path: Optional[Path] = None,
) -> None:
    DiggerApp(
        records,
        state=state,
        crate_title=crate_title,
        browser=browser,
        export_format=export_format,
        export_path=export_path,
    ).run()

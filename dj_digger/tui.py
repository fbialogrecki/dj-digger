"""Interactive crate browser.

Opening every link at once means 287 browser tabs on a big playlist, which is
not a workflow. This screen lets you walk the list, open one link at a time,
filter down to a single store and mark what you already own - and the marks
survive between runs because they live in ``state.TrackState``.

It can also start from nothing: with no records it asks for a link, digs it in a
worker thread so the interface stays responsive, and fills itself in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static

from . import browser as browser_module
from . import dig as dig_module
from . import links as links_module
from .models import Crate, LinkRecord
from .state import GOT, NEW, OPENED, SKIP, TrackState

STATUS_STYLES = {
    NEW: ("new", "white"),
    OPENED: ("opened", "yellow"),
    SKIP: ("skipped", "bright_black"),
    GOT: ("got it", "bold green"),
}
OPEN_ALL_CONFIRM_THRESHOLD = 20
# Number keys select the nth store that this crate actually contains, so `1` is
# always the first store you have rather than a fixed category.
QUICK_FILTER_KEYS = 9

SELECTED = "Selected track"
WHOLE_LIST = "Whole visible list"
CRATES = "Crates"
OTHER = "Other"

# One source for the footer and the help screen, so they cannot drift apart.
# Only a handful show in the footer - a footer with twelve entries is unreadable,
# and `?` covers the rest.
KEYMAP = [
    ("o,enter", "open_link", "Open", SELECTED, True),
    ("g", "mark_got", "Got", SELECTED, True),
    ("s", "mark_skip", "Skip", SELECTED, True),
    ("u", "mark_new", "Unmark", SELECTED, False),
    ("a", "open_visible", "Open all shown", WHOLE_LIST, True),
    ("e", "export", "Export shown", WHOLE_LIST, False),
    ("slash", "start_search", "Search", WHOLE_LIST, True),
    ("f", "cycle_store(1)", "Store", WHOLE_LIST, True),
    ("F", "cycle_store(-1)", "Previous store", WHOLE_LIST, False),
    ("h", "toggle_handled", "Hide handled", WHOLE_LIST, False),
    ("escape", "clear_filters", "Clear filters", WHOLE_LIST, False),
    ("d", "dig_link", "Crate", CRATES, True),
    ("question_mark", "help", "Help", OTHER, True),
    ("q", "quit", "Quit", OTHER, True),
]

# What each group actually operates on. The old footer never said, so it was
# impossible to tell whether a key hit one row or the whole list.
HELP_SCOPES = {
    SELECTED: "the highlighted row only",
    WHOLE_LIST: "every row shown, after filters",
    CRATES: "loads another playlist",
    OTHER: "",
}
HELP_EXTRA = {
    WHOLE_LIST: [("0", "Show every store"), ("1-9", "Show only the nth store")],
}


@dataclass
class Row:
    position: int
    record: LinkRecord


class AskLinkScreen(ModalScreen[Optional[str]]):
    """Asks for a SoundCloud link (or a saved HTML file)."""

    CSS = """
    AskLinkScreen {
        align: center middle;
    }
    #ask {
        width: 78;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #ask-hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, message: str = "Paste a SoundCloud link") -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="ask"):
            yield Label(self.message)
            yield Label(
                "Playlist, artist profile, /likes, one track, or a saved .html file.",
                id="ask-hint",
            )
            yield Input(placeholder="https://soundcloud.com/...", id="ask-input")

    def on_mount(self) -> None:
        self.query_one("#ask-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        target = event.value.strip()
        if target:
            self.dismiss(target)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Every key, grouped by what it acts on."""

    CSS = """
    HelpScreen {
        align: center middle;
    }
    #help {
        width: 56;
        height: auto;
        max-height: 90%;
        overflow-y: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    """

    BINDINGS = [Binding("escape,question_mark,q", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static(self._body())

    def _body(self) -> Text:
        body = Text()
        sections = (SELECTED, WHOLE_LIST, CRATES, OTHER)
        for section in sections:
            entries = [
                (key.replace("slash", "/").replace("question_mark", "?"), label)
                for key, _action, label, group, _show in KEYMAP
                if group == section
            ] + HELP_EXTRA.get(section, [])
            if not entries:
                continue
            body.append(section + "\n", style="bold")
            if HELP_SCOPES[section]:
                body.append(f"  acts on {HELP_SCOPES[section]}\n", style="bright_black")
            for key, label in entries:
                body.append(f"  {key:<10}", style="cyan")
                body.append(f"{label}\n")
            if section != sections[-1]:
                body.append("\n")
        return body

    def action_dismiss(self) -> None:
        self.dismiss(None)


class DiggerApp(App):
    # The built-in palette showed up in the footer as an unexplained "palette".
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    #status {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    #stores {
        height: auto;
        padding: 0 1;
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
        Binding(key, action, label, show=show) for key, action, label, _group, show in KEYMAP
    ] + [
        Binding(str(index), f"filter_index({index})", f"Store {index}", show=False)
        for index in range(0, QUICK_FILTER_KEYS + 1)
    ]

    def __init__(
        self,
        records: Sequence[LinkRecord] = (),
        *,
        state: Optional[TrackState] = None,
        crate_title: str = "",
        browser: str = "default",
        export_format: str = "json",
        export_path: Optional[Path] = None,
        dig_options: Optional[dig_module.DigOptions] = None,
    ) -> None:
        super().__init__()
        self.rows: List[Row] = []
        self.state = state or TrackState()
        self.crate_title = crate_title
        self.browser = browser
        self.export_format = export_format
        self.export_path = export_path
        self.dig_options = dig_options or dig_module.DigOptions()
        self.store_filter: str = ""
        self.search_term: str = ""
        self.hide_handled: bool = False
        self.visible_rows: List[Row] = []
        self.present: List[str] = []
        self._pending_open_all = False
        self._digging = False
        self._set_records(records)

    # Layout

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter by artist or title", id="search")
        yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
        yield Static(id="status")
        yield Static(id="stores")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#tracks", DataTable)
        table.add_column("#", width=4)
        table.add_column("Track", width=44)
        table.add_column("Store", width=12)
        table.add_column("Link", width=30)
        table.add_column("Status", width=7)
        self.refresh_rows()
        table.focus()
        if not self.rows:
            self.action_dig_link()

    # Records

    def _set_records(self, records: Sequence[LinkRecord]) -> None:
        self.rows = [
            Row(position=index + 1, record=record) for index, record in enumerate(records)
        ]
        self.present = links_module.present_categories(records)
        if self.store_filter not in self.present:
            self.store_filter = ""

    def load_records(self, records: Sequence[LinkRecord], *, title: str = "") -> None:
        self._set_records(records)
        if title:
            self.crate_title = title
            self.sub_title = title
        self.search_term = ""
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        self.refresh_rows(keep_cursor=False)
        self.query_one("#tracks", DataTable).focus()

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

        # The crate name lives here now that the decorative header is gone.
        pieces = [self.crate_title or "no crate yet"]
        pieces.append(f"{len(self.visible_rows)}/{len(self.rows)} links")
        pieces += [
            f"got {counts[GOT]}",
            f"skipped {counts[SKIP]}",
            f"opened {counts[OPENED]}",
        ]
        if self.search_term:
            pieces.append(f"search: {self.search_term!r}")
        if self.hide_handled:
            pieces.append("hiding handled")
        self.query_one("#status", Static).update(" \u00b7 ".join(pieces))
        self.update_store_line()

    def update_store_line(self) -> None:
        """Show the stores in this crate, numbered, so the number keys explain themselves."""

        line = Text()
        if not self.rows:
            line.append("press d to dig a link", style="bright_black")
            self.query_one("#stores", Static).update(line)
            return

        by_category = links_module.count_by_category(
            [row.record for row in self.rows]
        )
        line.append("0 all", style="bold" if not self.store_filter else "bright_black")
        for index, category in enumerate(self.present, start=1):
            line.append("  ")
            active = category == self.store_filter
            label = f"{index} {category}" if index <= QUICK_FILTER_KEYS else category
            line.append(label, style="bold cyan" if active else "cyan")
            line.append(f"\u00b7{by_category[category]}", style="bright_black")
        self.query_one("#stores", Static).update(line)

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

    # Digging

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_dig_link(self) -> None:
        if self._digging:
            self.notify("Already digging - hold on", timeout=2)
            return
        message = "Paste a SoundCloud link" if self.rows else "What are we digging?"
        self.push_screen(AskLinkScreen(message=message), self._link_entered)

    def _link_entered(self, target: Optional[str]) -> None:
        if not target:
            if not self.rows:
                # Nothing was asked for and there is nothing to show.
                self.exit()
            return
        self._digging = True
        self.query_one("#tracks", DataTable).loading = True
        self.query_one("#status", Static).update(f"Digging {target}")
        self.dig_in_background(target)

    @work(thread=True, exclusive=True)
    def dig_in_background(self, target: str) -> None:
        def on_progress(stage: str, done: int, total: Optional[int]) -> None:
            suffix = f" {done}/{total}" if total else ""
            self.call_from_thread(
                self.query_one("#status", Static).update, f"{stage}{suffix}"
            )

        try:
            crate = dig_module.dig(
                target,
                limit=self.dig_options.limit,
                timeout=self.dig_options.timeout,
                delay=self.dig_options.delay,
                on_progress=on_progress,
            )
        except Exception as exc:  # a worker must never take the app down with it
            self.call_from_thread(self._dig_failed, str(exc))
            return
        self.call_from_thread(self._dig_finished, crate)

    def _finish_digging(self) -> None:
        self._digging = False
        self.query_one("#tracks", DataTable).loading = False

    def _dig_failed(self, message: str) -> None:
        self._finish_digging()
        self.refresh_rows(keep_cursor=False)
        self.notify(message, severity="error", timeout=8)
        self.action_dig_link()

    def _dig_finished(self, crate: Crate) -> None:
        self._finish_digging()
        if not crate.tracks:
            self._dig_failed(f"Found no tracks behind {crate.source}")
            return

        records = links_module.categorise_all(crate.tracks)
        self.load_records(records, title=crate.title or crate.source)

        written = None
        if self.export_format != "none":
            written = links_module.export_records(
                records, self.export_format, self.export_path
            )
        message = f"{len(crate.tracks)} tracks, {len(records)} links"
        if written:
            message += f" - saved to {written}"
        self.notify(message, timeout=5)

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

    def _apply_store_filter(self, category: str) -> None:
        self.store_filter = category
        self._pending_open_all = False
        self.refresh_rows(keep_cursor=False)

    def action_filter_index(self, index: int) -> None:
        """``0`` clears the filter, ``1``-``9`` pick the nth store in this crate."""

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
        current = options.index(self.store_filter) if self.store_filter in options else 0
        self._apply_store_filter(options[(current + step) % len(options)])

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
    records: Sequence[LinkRecord] = (),
    *,
    state: Optional[TrackState] = None,
    crate_title: str = "",
    browser: str = "default",
    export_format: str = "json",
    export_path: Optional[Path] = None,
    dig_options: Optional[dig_module.DigOptions] = None,
) -> None:
    DiggerApp(
        records,
        state=state,
        crate_title=crate_title,
        browser=browser,
        export_format=export_format,
        export_path=export_path,
        dig_options=dig_options,
    ).run()

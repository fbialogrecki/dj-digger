"""Interactive crate browser.

Opening every link at once means 287 browser tabs on a big playlist, which is
not a workflow. This screen lets you walk the list, open one link at a time,
filter down to a single store and mark what you already own - and the marks
survive between runs because they live in ``state.TrackState``.

One row is one track, not one link. A track selling on Bandcamp and gated on
Hypeddit is a single decision, so it gets a single row with a badge per store;
the store filter doubles as the way to say which of them ``o`` should follow.

It can also start from nothing: with no records it asks for a link, digs it in a
worker thread so the interface stays responsive, and fills itself in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from rich.table import Table
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Input, Label, ListItem, ListView, Static
from textual.widgets.data_table import ColumnKey

from . import browser as browser_module
from . import dig as dig_module
from . import library as library_module
from . import links as links_module
from .library import CrateRecord
from .models import Crate, LinkRecord, Track
from .player import (
    SEEK_STEP,
    VOLUME_STEP,
    Player,
    PlaybackUnavailable,
    PlayerBar,
    Stream,
    fetch_waveform,
    open_source,
    resolve_stream,
)
from .soundcloud import SoundCloudClient, SoundCloudError
from .state import GOT, NEW, OPENED, SKIP, TrackState

# A mark is one glyph in a one-cell gutter. Spelling "skipped" out cost seven
# columns on every row to say "new" on nearly all of them; the width belongs to
# the track title instead. HelpScreen carries the words.
STATUS_STYLES = {
    NEW: ("\u00b7", "bright_black", "not looked at yet"),
    OPENED: ("\u25cb", "yellow", "link opened, outcome unknown"),
    SKIP: ("\u2717", "bright_black", "skipped"),
    GOT: ("\u2713", "bold green", "got it"),
}
LOGGER = logging.getLogger(__name__)

PLAYING_GLYPH = "\u25b6"
OPEN_ALL_CONFIRM_THRESHOLD = 20
# How long before the end of a track we start getting the next one ready. Long
# enough to cover a signed URL, a waveform and the first megabytes of audio on a
# poor connection; short enough that a filter change rarely wastes the work.
PREFETCH_LEAD = 20.0

# Thirty frames a second, which is what a pulse needs to read as one rather than
# as a stutter. It only costs anything while a track is playing: with nothing
# going out, _tick leaves on its first line. Redrawing a waveform this often is
# only affordable because a frame is now a few style ranges - see paint_waveform.
TICK = 1 / 30
# Turning animation off - TEXTUAL_ANIMATIONS=none, which is what you do over a
# slow link - has to turn this off too, or the one thing that repaints the most
# would carry on regardless. The clock and the auto-advance still need a pulse,
# just not thirty of them a second.
CALM_TICK = 0.25
# The spinner is slower than the frame rate on purpose; braille that turns thirty
# times a second is a smear.
SPINNER = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
SPINNER_EVERY = 4
# Long enough to catch the eye, short enough that holding `s` down still works.
FLASH = 0.25
# Number keys select the nth store that this crate actually contains, so `1` is
# always the first store you have rather than a fixed category.
QUICK_FILTER_KEYS = 9

# Everything except the title gets a fixed budget; the title takes the rest, so
# a wide terminal shows long titles instead of an empty margin.
MARK_WIDTH = 1
INDEX_WIDTH = 4
STORES_WIDTH = 22
GENRE_WIDTH = 14
TIME_WIDTH = 5
BPM_WIDTH = 3
MIN_TITLE_WIDTH = 20

# These two say nothing as a word - "shop" and "others" are what is left after
# every recognised store, so the domain is the only thing that identifies them.
DOMAIN_BADGE_CATEGORIES = {"shop", "others"}

SELECTED = "Selected track"
WHOLE_LIST = "Whole visible list"
CRATES = "Crates"
PLAYBACK = "Playback"
OTHER = "Other"

# One source for the footer and the help screen, so they cannot drift apart:
# (key, action, footer label, section, show in footer, longer help text).
# Footer labels stay short because it gets one line; help has the room to explain.
KEYMAP = [
    ("o,enter", "open_link", "Open", SELECTED, True, "Open its best link, or the filtered store"),
    ("g", "mark_got", "Got", SELECTED, True, "Mark as got, press again to undo"),
    ("s", "mark_skip", "Skip", SELECTED, True, "Mark as skipped, press again to undo"),
    ("u", "mark_new", "Unmark", SELECTED, False, "Clear the mark either way"),
    ("x", "remove_track", "Remove", SELECTED, False, "Remove from this crate, locally only"),
    ("ctrl+z", "undo_remove", "Undo", SELECTED, False, "Put back the last removed track"),
    ("space", "play_pause", "Play", PLAYBACK, True, "Play or pause the highlighted track"),
    ("left_square_bracket", "seek(-1)", "Back", PLAYBACK, False, "Back 10 seconds"),
    ("right_square_bracket", "seek(1)", "Forward", PLAYBACK, False, "Forward 10 seconds"),
    ("n", "play_step(1)", "Next", PLAYBACK, False, "Play the next track in the list"),
    ("p", "play_step(-1)", "Previous", PLAYBACK, False, "Play the previous track"),
    ("minus", "volume(-1)", "Quieter", PLAYBACK, False, "Turn it down"),
    ("equals_sign", "volume(1)", "Louder", PLAYBACK, False, "Turn it up"),
    ("m", "mute", "Mute", PLAYBACK, False, "Mute or unmute"),
    ("a", "open_visible", "Open all", WHOLE_LIST, True, "Open every link shown, asks above 20"),
    ("e", "export", "Export", WHOLE_LIST, False, "Write the rows shown to the export file"),
    ("slash", "start_search", "Search", WHOLE_LIST, True, "Filter by artist or title"),
    ("f", "cycle_store(1)", "Next store", WHOLE_LIST, True, "Step to the next store in this crate"),
    ("F", "cycle_store(-1)", "Previous store", WHOLE_LIST, False, "Step back a store"),
    ("0", "filter_index(0)", "Show all", WHOLE_LIST, True, "Drop the store filter, show everything"),
    ("h", "toggle_handled", "Hide handled", WHOLE_LIST, False, "Hide what is got or skipped"),
    ("escape", "clear_filters", "Clear filters", WHOLE_LIST, False, "Clear store, search and hiding"),
    ("d", "dig_link", "Add crate", CRATES, True, "Dig a link into a new crate"),
    ("r", "refresh_crate", "Refresh", CRATES, False, "Re-dig this crate from SoundCloud"),
    ("X", "delete_crate", "Delete", CRATES, False, "Delete this crate, after confirming"),
    ("ctrl+b", "toggle_sidebar", "Crates", CRATES, False, "Show or hide the crate sidebar"),
    ("question_mark", "help", "Help", OTHER, True, "This screen"),
    ("q", "quit", "Quit", OTHER, True, "Leave"),
]

# What each group actually operates on. The old footer never said, so it was
# impossible to tell whether a key hit one row or the whole list.
HELP_SCOPES = {
    SELECTED: "acts on the highlighted row only",
    WHOLE_LIST: "acts on every row shown, after filters",
    CRATES: "loads another playlist",
    PLAYBACK: "click the waveform to seek",
    OTHER: "",
}

# Textual's key identifiers are not what anyone wants to read in a help screen.
KEY_DISPLAY = {
    "slash": "/",
    "question_mark": "?",
    "minus": "-",
    "equals_sign": "=",
    "left_square_bracket": "[",
    "right_square_bracket": "]",
    "o,enter": "o, enter",
    "X": "shift+X",
}
HELP_EXTRA = {
    WHOLE_LIST: [("1-9", "Show only the nth store")],
}


@dataclass
class Prepared:
    """A track made ready to play before anything asked for it."""

    track: Track
    stream: Stream
    waveform: List[int] = field(default_factory=list)
    # An HTTP source already filling with audio, or None if miniaudio is absent.
    source: object = None

    @property
    def key(self) -> str:
        return self.track.key

    def close(self) -> None:
        if self.source is not None:
            self.source.close()
            self.source = None


@dataclass
class Row:
    """One track, with every store it turned up in."""

    position: int
    track: Track
    # Best first, in CATEGORY_NAMES order - see links.group_by_track.
    records: List[LinkRecord]

    @property
    def categories(self) -> List[str]:
        return [record.category for record in self.records]

    def record_for(self, category: str) -> Optional[LinkRecord]:
        for record in self.records:
            if record.category == category:
                return record
        return None


class TrackTable(DataTable):
    """A table whose title column absorbs whatever width is left over.

    DataTable columns are fixed or content-sized, neither of which fills the
    terminal, so the width is worked out here and refreshed whenever the table
    is resized - by the terminal, or by the sidebar folding away.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.flexible_column: Optional[ColumnKey] = None

    def on_resize(self, event: events.Resize) -> None:
        self.fit_flexible_column()

    def fit_flexible_column(self) -> None:
        if self.flexible_column is None or self.flexible_column not in self.columns:
            return
        column = self.columns[self.flexible_column]
        spent = sum(
            other.get_render_width(self)
            for key, other in self.columns.items()
            if key != self.flexible_column
        )
        width = self.size.width - spent - 2 * self.cell_padding
        column.width = max(MIN_TITLE_WIDTH, width)
        self.refresh(layout=True)


class StatusBar(Static):
    """The bottom bar, which has to be rebuilt whenever its width changes.

    Whether the counts fit beside the store legend is a width question, and the
    app-level resize event fires before the layout settles - so the widget that
    actually changed size is the one that has to ask.
    """

    def on_resize(self, event: events.Resize) -> None:
        self.app.update_status()


class CrateButton(Button):
    """A per-crate icon button. Carries its crate so no widget ids are needed."""

    def __init__(self, label: str, record: CrateRecord, intent: str, tooltip: str) -> None:
        super().__init__(label, classes="crate-icon", tooltip=tooltip)
        self.record = record
        self.intent = intent


class CrateItem(ListItem):
    def __init__(self, record: CrateRecord) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        # A star marks a crate imported from an export file, which is missing
        # fields the API would have given us. Text(), not a markup string: a
        # playlist called "Techno [2026]" would otherwise lose the bracketed part
        # to Textual's markup parser. no_wrap because the row is one line tall,
        # so a wrapped "Hard Techno Ressurection" would simply lose its surname.
        title = self.record.title + (" *" if self.record.partial else "")
        yield Label(
            Text(title, no_wrap=True, overflow="ellipsis"), classes="crate-name"
        ).with_tooltip(title)
        yield CrateButton("\u21bb", self.record, "refresh", "Refresh from SoundCloud (r)")
        yield CrateButton("\u2715", self.record, "delete", "Delete crate (shift+X)")


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
        sections = (SELECTED, PLAYBACK, WHOLE_LIST, CRATES, OTHER)
        for section in sections:
            entries = [
                (KEY_DISPLAY.get(key, key), detail)
                for key, _action, _label, group, _show, detail in KEYMAP
                if group == section
            ] + HELP_EXTRA.get(section, [])
            if not entries:
                continue
            body.append(section + "\n", style="bold")
            if HELP_SCOPES[section]:
                body.append(f"  {HELP_SCOPES[section]}\n", style="bright_black")
            for key, label in entries:
                body.append(f"  {key:<10}", style="cyan")
                body.append(f"{label}\n")
            body.append("\n")

        # The marks are one glyph wide in the table, so this is where they get
        # to say what they mean.
        body.append("Marks\n", style="bold")
        body.append(f"  {PLAYING_GLYPH:<10}", style="cyan")
        body.append("playing now\n")
        for glyph, style, meaning in STATUS_STYLES.values():
            body.append(f"  {glyph:<10}", style=style)
            body.append(f"{meaning}\n")
        return body

    def action_dismiss(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no, for the one action here that cannot be undone."""

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm {
        width: 62;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    #confirm-buttons {
        height: auto;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "refuse", "No"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Label(self.question)
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes (y)", variant="error", id="confirm-yes")
                yield Button("No (n)", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_refuse(self) -> None:
        self.dismiss(False)


class DiggerApp(App):
    # The built-in palette showed up in the footer as an unexplained "palette".
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    #body {
        height: 1fr;
    }
    #sidebar {
        width: 28;
        border-right: solid $panel;
    }
    #sidebar.collapsed {
        display: none;
    }
    #sidebar-title {
        padding: 0 1;
        color: $text-muted;
    }
    /* Auto height so the add button sits right under the last crate, not pinned
       to the bottom of the sidebar. */
    #crates {
        height: auto;
        max-height: 1fr;
        border: none;
        background: transparent;
    }
    CrateItem {
        layout: horizontal;
        height: 1;
    }
    .crate-name {
        width: 1fr;
        height: 1;
    }
    /* The icons cost six of the sidebar's columns, which the crate names need
       more than a row you are not pointing at does. They come back on the row
       under the cursor or the mouse, which is the only row you can act on. */
    .crate-icon {
        display: none;
        width: 3;
        min-width: 3;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
    }
    CrateItem.-highlight .crate-icon,
    CrateItem.-hovered .crate-icon {
        display: block;
    }
    .crate-icon:hover {
        background: $accent;
    }
    #crate-add {
        width: 1fr;
        height: 1;
        border: none;
        background: transparent;
        color: $text-muted;
        content-align: left middle;
        padding: 0 1;
    }
    #crate-add:hover {
        background: $accent;
        color: $text;
    }
    /* Exactly one line. Left to wrap, this bar grew back into the three rows of
       chrome it was meant to replace. */
    #status {
        height: 1;
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
        Binding(key, action, label, show=show, key_display=KEY_DISPLAY.get(key))
        for key, action, label, _group, show, _detail in KEYMAP
    ] + [
        # 0 is declared in KEYMAP so it shows in the footer as the way back.
        Binding(str(index), f"filter_index({index})", f"Store {index}", show=False)
        for index in range(1, QUICK_FILTER_KEYS + 1)
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
        crate_record: Optional[CrateRecord] = None,
    ) -> None:
        super().__init__()
        self.rows: List[Row] = []
        self.state = state or TrackState()
        self.crate = crate_record
        self.crates: List[CrateRecord] = []
        self.crate_title = crate_title or (crate_record.title if crate_record else "")
        self.browser = browser
        self.export_format = export_format
        self.export_path = export_path
        self.dig_options = dig_options or dig_module.DigOptions()
        self.show_bpm = False
        self.bpm_column: Optional[ColumnKey] = None
        self.store_filter: str = ""
        self.search_term: str = ""
        self.hide_handled: bool = False
        self.visible_rows: List[Row] = []
        self.present: List[str] = []
        self._pending_open_all = False
        self._digging = False
        self._undone: List[str] = []
        self._ticker: Optional[Timer] = None
        # Decided fresh each time playback moves: does the cursor come along?
        self._cursor_follows = True
        self._prepared: Optional[Prepared] = None
        self._preparing: str = ""
        self._frame = 0
        self._dig_message = ""
        self.player = Player()
        self._client: Optional[SoundCloudClient] = None
        self._set_records(records)

    # Layout

    def compose(self) -> ComposeResult:
        yield PlayerBar(self.player, id="player")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("Crates", id="sidebar-title")
                yield ListView(id="crates")
                yield Button("+ Add crate", id="crate-add", tooltip="Add a crate (d)")
            with Vertical(id="main"):
                yield Input(placeholder="Filter by artist or title", id="search")
                yield TrackTable(id="tracks", cursor_type="row", zebra_stripes=True)
        yield StatusBar(id="status")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#tracks", TrackTable)
        table.add_column(Text(PLAYING_GLYPH, style="bright_black"), width=MARK_WIDTH)
        table.add_column("", width=MARK_WIDTH)
        table.add_column("#", width=INDEX_WIDTH)
        table.flexible_column = table.add_column("Track", width=MIN_TITLE_WIDTH)
        table.add_column("Stores", width=STORES_WIDTH)
        table.add_column("Genre", width=GENRE_WIDTH)
        table.add_column("Time", width=TIME_WIDTH)
        await self.reload_sidebar()
        if not self.rows:
            # Someone with a library wants to see it, not be interrogated.
            latest = self.latest_crate()
            if latest is not None:
                self.load_crate(latest)
        self.refresh_rows()
        table.focus()
        # Needs a laid-out width to size itself against.
        self.call_after_refresh(table.fit_flexible_column)
        # Asleep until there is something to animate: waking thirty times a
        # second to look at a list nobody is playing is just a warm laptop.
        self._ticker = self.set_interval(self.frame_interval, self._tick, pause=True)
        if not self.rows:
            self.action_dig_link()

    def on_unmount(self) -> None:
        # A tick landing after the widgets have gone would go looking for a
        # player bar that no longer exists.
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        self._discard_prepared()
        self.player.close()
        if self._client is not None:
            self._client.close()

    # Records

    def _set_records(self, records: Sequence[LinkRecord]) -> None:
        self.rows = [
            Row(position=index + 1, track=group[0].track, records=group)
            for index, group in enumerate(links_module.group_by_track(records))
        ]
        # Most artists never say. A column of dashes would cost the title three
        # of its columns to tell you nothing, so it only turns up when the crate
        # has tempos in it - which some genres and labels are good about.
        self.show_bpm = any(row.track.bpm for row in self.rows)
        self.present = links_module.present_categories(records)
        if self.store_filter not in self.present:
            self.store_filter = ""

    def all_records(self) -> List[LinkRecord]:
        return [record for row in self.rows for record in row.records]

    # Crate library

    def latest_crate(self) -> Optional[CrateRecord]:
        if not self.crates:
            return None
        return max(self.crates, key=lambda r: r.refreshed_at or r.imported_at or "")

    async def reload_sidebar(self) -> None:
        # clear() only queues the removal, so appending without awaiting it
        # leaves the old items in place and duplicates the list.
        self.crates = library_module.list_crates()
        listing = self.query_one("#crates", ListView)
        await listing.clear()
        for record in self.crates:
            listing.append(CrateItem(record))
        if self.crate is not None:
            slugs = [record.slug for record in self.crates]
            if self.crate.slug in slugs:
                listing.index = slugs.index(self.crate.slug)

    def highlighted_crate(self) -> Optional[CrateRecord]:
        highlighted = self.query_one("#crates", ListView).highlighted_child
        if isinstance(highlighted, CrateItem):
            return highlighted.record
        return self.crate

    def load_crate(self, record: CrateRecord) -> None:
        self.crate = record
        records = links_module.categorise_all(record.active_tracks)
        self.load_records(records, title=record.title)

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
            if self.store_filter and self.store_filter not in row.categories:
                continue
            if self.hide_handled and self.status_of(row) in (GOT, SKIP):
                continue
            if term and term not in row.track.label.lower():
                continue
            rows.append(row)
        return rows

    def status_of(self, row: Row) -> str:
        return self.state.get(row.track.key)

    def record_to_open(self, row: Row) -> LinkRecord:
        """The link ``o`` would follow: the filtered store, else the best one.

        Filtering to a store is how you say which shop you want, so while a
        filter is on it also decides what opens - otherwise picking `bandcamp`
        and pressing `o` would still send you to a follow gate.
        """

        if self.store_filter:
            chosen = row.record_for(self.store_filter)
            if chosen is not None:
                return chosen
        return row.records[0]

    def _store_badges(self, row: Row) -> Text:
        """Every store this track turned up in, the one ``o`` opens picked out."""

        opening = self.record_to_open(row)
        badges = Text()
        for record in row.records:
            if badges:
                badges.append(" ")
            free = record.link_text == links_module.FREE_DOWNLOAD
            if record.category in DOMAIN_BADGE_CATEGORIES:
                name = links_module.host_of(record.link_url) or record.category
            elif free:
                name = "\u2193" + record.category
            else:
                name = record.category
            if record is not opening:
                style = "bright_black"
            elif free:
                style = "bold green"
            elif record.link_text == links_module.NO_STORE_LINK:
                style = "bright_black"
            else:
                style = "bold cyan"
            badges.append(name, style=style)
        return badges

    def _playing_key(self) -> Optional[str]:
        loaded = self.player.loaded
        return loaded.track.key if loaded is not None else None

    def _cells(self, row: Row, playing_key: Optional[str]) -> List[Text]:
        status = self.status_of(row)
        glyph, style, _meaning = STATUS_STYLES[status]
        dim = "bright_black" if status == SKIP else ""
        cells = [
            Text(PLAYING_GLYPH if row.track.key == playing_key else "", style="green"),
            Text(glyph, style=style),
            Text(str(row.position), style="bright_black"),
            Text(row.track.label, style=dim),
            self._store_badges(row),
            Text(row.track.genre_label or "-", style="bright_black"),
            Text(row.track.duration_label or "-", style="bright_black"),
        ]
        if self.show_bpm:
            bpm = row.track.bpm
            cells.append(Text(str(bpm) if bpm else "-", style="bright_black"))
        return cells

    def _paint_row(self, index: int, flash: str = "") -> None:
        """Rewrite one row in place, rather than rebuilding the whole table."""

        if not self.query("#tracks") or not 0 <= index < len(self.visible_rows):
            return
        table = self.query_one("#tracks", TrackTable)
        if index >= table.row_count:
            return
        for column, cell in enumerate(self._cells(self.visible_rows[index], self._playing_key())):
            if flash:
                cell.stylize(flash)
            table.update_cell_at(Coordinate(index, column), cell, update_width=False)

    def _flash_row(self, index: int, style: str) -> None:
        """Light the row you acted on, so a keypress is visibly a change."""

        self._paint_row(index, flash=style)
        self.set_timer(FLASH, lambda: self._paint_row(index))

    def _fit_bpm_column(self, table: TrackTable) -> None:
        if self.show_bpm and self.bpm_column is None:
            # Last, because it is the column that comes and goes.
            self.bpm_column = table.add_column("BPM", width=BPM_WIDTH)
        elif self.bpm_column is not None and not self.show_bpm:
            table.remove_column(self.bpm_column)
            self.bpm_column = None

    def refresh_rows(self, *, keep_cursor: bool = True) -> None:
        table = self.query_one("#tracks", TrackTable)
        previous = table.cursor_row if keep_cursor else 0
        self.visible_rows = self.matching_rows()
        playing_key = self._playing_key()
        self._fit_bpm_column(table)

        table.clear()
        for row in self.visible_rows:
            table.add_row(*self._cells(row, playing_key))

        if self.visible_rows:
            table.move_cursor(row=min(previous, len(self.visible_rows) - 1))
        table.fit_flexible_column()
        self._drop_stale_preparation()
        self.update_status()

    def update_status(self) -> None:
        """One bar: the store legend on the left, where you are up to on the right.

        These were two stacked bars above the footer, which made three rows of
        chrome under the table. The crate name went with them - the sidebar
        already highlights which crate you are in.
        """

        bar = self.query_one("#status", Static)
        stores = self._store_line()
        progress = self._progress_line()

        grid = Table.grid(expand=True)
        grid.add_column(no_wrap=True)
        # Narrow terminals cannot have both. The legend is what the number keys
        # are documented by, so the counts are what goes.
        if len(stores) + len(progress) + 2 <= bar.size.width:
            grid.add_column(justify="right", no_wrap=True)
            grid.add_row(stores, progress)
        else:
            grid.add_row(stores)
        bar.update(grid)

    def _progress_line(self) -> Text:
        counts: Dict[str, int] = {status: 0 for status in STATUS_STYLES}
        for row in self.rows:
            counts[self.status_of(row)] += 1

        pieces = [f"{len(self.visible_rows)}/{len(self.rows)} tracks"]
        pieces += [
            f"got {counts[GOT]}",
            f"skipped {counts[SKIP]}",
            f"opened {counts[OPENED]}",
        ]
        if self.search_term:
            pieces.append(f"search: {self.search_term!r}")
        if self.hide_handled:
            pieces.append("hiding handled")
        if self.crate is not None and self.crate.partial:
            pieces.append("imported from a file, press r to complete it")
        return Text(" \u00b7 ".join(pieces), style="bright_black")

    def _store_line(self) -> Text:
        """The stores in this crate, numbered, so the number keys explain themselves."""

        line = Text()
        if not self.rows:
            line.append("press d to dig a link", style="bright_black")
            return line

        by_category = links_module.count_by_category(self.all_records())
        showing_all = not self.store_filter
        line.append("\u25b8 " if showing_all else "  ", style="bold")
        line.append("0 all", style="bold reverse" if showing_all else "bright_black")
        for index, category in enumerate(self.present, start=1):
            active = category == self.store_filter
            line.append("  \u25b8" if active else "   ", style="bold")
            label = f"{index} {category}" if index <= QUICK_FILTER_KEYS else category
            line.append(label, style="bold reverse cyan" if active else "cyan")
            line.append(f"\u00b7{by_category[category]}", style="bright_black")
        return line

    # Helpers

    def current_row(self) -> Optional[Row]:
        table = self.query_one("#tracks", DataTable)
        if not self.visible_rows:
            return None
        index = table.cursor_row
        if 0 <= index < len(self.visible_rows):
            return self.visible_rows[index]
        return None

    def _mark(self, row: Row, index: int, status: str, message: str) -> None:
        self.state.set(row.track.key, status)
        self.notify(f"{message}: {row.track.label}", timeout=2)
        if self.hide_handled:
            # The row is on its way out of the list, so there is nothing to light.
            self.refresh_rows()
            return
        self._flash_row(index, STATUS_STYLES[status][1])
        self.update_status()

    def _toggle_status(self, status: str, message: str) -> None:
        """Pressing the same key again clears the mark, which is what people try."""

        row = self.current_row()
        if row is None:
            return
        clearing = self.status_of(row) == status
        cursor = self.query_one("#tracks", DataTable).cursor_row
        judging_what_plays = self._playing_index() == cursor
        label = "Unmarked" if clearing else message
        self._mark(row, cursor, NEW if clearing else status, label)
        if clearing:
            # Undoing a mark should leave you looking at what you just undid.
            return
        self._advance_cursor()
        if judging_what_plays and self.player.playing:
            # You marked the track you were listening to, so listening moves on
            # with you rather than finishing something you already ruled on.
            self.action_play_step(1)

    def _set_status(self, status: str, message: str) -> None:
        row = self.current_row()
        if row is None:
            return
        self._mark(row, self.query_one("#tracks", DataTable).cursor_row, status, message)

    def _advance_cursor(self) -> None:
        table = self.query_one("#tracks", DataTable)
        if self.visible_rows and table.cursor_row < len(self.visible_rows) - 1:
            table.move_cursor(row=table.cursor_row + 1)

    # Digging

    # Playback

    @property
    def client(self) -> SoundCloudClient:
        if self._client is None:
            self._client = SoundCloudClient()
        return self._client

    def _player_bar(self) -> PlayerBar:
        return self.query_one("#player", PlayerBar)

    def _tick(self) -> None:
        # The timer belongs to the app and the bar to the screen, so on the way
        # out a tick can arrive after the bar has already gone.
        if not self.query("#player"):
            return
        self._frame += 1
        if self._digging:
            self._spin()
        if self.player.take_finished():
            # Auditioning a crate means hearing all of it, not pressing a key
            # between every track.
            self._advance_playback()
            return
        if self.player.playing:
            self._player_bar().refresh_bar()
            self._prepare_next()
        elif not self._digging:
            self._sleep()

    @property
    def frame_interval(self) -> float:
        return TICK if self.animation_level == "full" else CALM_TICK

    def _wake(self) -> None:
        if self._ticker is not None:
            self._ticker.resume()

    def _sleep(self) -> None:
        if self._ticker is not None:
            self._ticker.pause()

    def _spin(self) -> None:
        if self._frame % SPINNER_EVERY == 0:
            self._draw_digging()

    def _draw_digging(self) -> None:
        """Something turning is the difference between working and hung."""

        glyph = SPINNER[(self._frame // SPINNER_EVERY) % len(SPINNER)]
        self.query_one("#status", Static).update(
            Text(f"{glyph} {self._dig_message}", style="bright_black")
        )

    def _prepare_next(self) -> None:
        """Get the next track ready while this one plays it out.

        Everything a track needs - a signed URL, a waveform, the audio itself -
        used to be fetched after the previous one ended, which put a second of
        "Loading" between every pair of tracks in the crate.
        """

        duration = self.player.duration
        if not duration or duration - self.player.position > PREFETCH_LEAD:
            return
        index = self._step_from_playing(1)
        if index is None:
            return
        track = self.visible_rows[index].track
        if not track.id or self._preparing == track.key:
            return
        if self._prepared is not None and self._prepared.key == track.key:
            return
        self._discard_prepared()
        self._preparing = track.key
        self.prepare_track(track)

    @work(thread=True, exclusive=True, group="prefetch")
    def prepare_track(self, track: Track) -> None:
        try:
            stream = resolve_stream(self.client, track.id)
            samples = fetch_waveform(self.client, stream.waveform_url)
            source = open_source(self.client.session, stream.url)
        except Exception as exc:
            # Nothing is owed here: if this fails the track loads the ordinary
            # way in its own time, and says so then.
            LOGGER.debug("Could not prepare %s: %s", track.label, exc)
            self.call_from_thread(self._preparation_done, track.key, None)
            return
        prepared = Prepared(track=track, stream=stream, waveform=samples, source=source)
        self.call_from_thread(self._preparation_done, track.key, prepared)

    def _preparation_done(self, key: str, prepared: Optional[Prepared]) -> None:
        if self._preparing != key:
            # The list moved under it while it was working.
            if prepared is not None:
                prepared.close()
            return
        self._preparing = ""
        self._prepared = prepared

    def _discard_prepared(self) -> None:
        if self._prepared is not None:
            self._prepared.close()
            self._prepared = None

    def _drop_stale_preparation(self) -> None:
        """A filter that changes what comes next makes the prepared track useless."""

        if self._prepared is None:
            return
        index = self._step_from_playing(1)
        following = self.visible_rows[index].track.key if index is not None else None
        if self._prepared.key != following:
            self._discard_prepared()

    def _take_prepared(self, track: Track) -> Optional[Prepared]:
        if self._prepared is None or self._prepared.key != track.key:
            return None
        prepared, self._prepared = self._prepared, None
        return prepared

    def _advance_playback(self) -> None:
        """Roll on by itself, taking the cursor only if it was keeping up.

        Asking the question here, rather than watching every cursor move, keeps
        it out of the way of the redraw - which moves the cursor too.
        """

        table = self.query_one("#tracks", DataTable)
        self._cursor_follows = self._playing_index() == table.cursor_row
        self._play_at(self._step_from_playing(1))

    def _playing_index(self) -> Optional[int]:
        loaded = self.player.loaded
        if loaded is None:
            return None
        for index, row in enumerate(self.visible_rows):
            if row.track.key == loaded.track.key:
                return index
        return None

    def _player_op(self, operation) -> None:
        """Run a player call. Every one of them can hit a missing audio device."""

        try:
            operation()
        except PlaybackUnavailable as exc:
            self._playback_failed(str(exc))
            return
        self._player_bar().refresh_bar()

    def action_play_pause(self) -> None:
        row = self.current_row()
        if row is None:
            return
        loaded = self.player.loaded
        if loaded is not None and loaded.track.key == row.track.key:
            self._player_op(self.player.toggle)
            self._wake()
            return
        # Playing what the cursor is on re-couples the two.
        self._cursor_follows = True
        self._start_playback(row.track)

    def _start_playback(self, track: Track) -> None:
        if not track.id:
            self.notify("No track id, so there is nothing to stream", timeout=4)
            return
        self._wake()
        prepared = self._take_prepared(track)
        if prepared is not None:
            self._audio_ready(track, prepared.stream, prepared.waveform, prepared.source)
            return
        bar = self._player_bar()
        bar.message = f"Loading {track.label}"
        bar.refresh_bar()
        self.fetch_audio(track)

    @work(thread=True, exclusive=True, group="audio")
    def fetch_audio(self, track: Track) -> None:
        """Only resolves the URL - the audio itself is decoded off the socket."""

        try:
            stream = resolve_stream(self.client, track.id)
            samples = fetch_waveform(self.client, stream.waveform_url)
        except (SoundCloudError, PlaybackUnavailable, OSError) as exc:
            self.call_from_thread(self._playback_failed, str(exc))
            return
        self.call_from_thread(self._audio_ready, track, stream, samples)

    def _audio_ready(
        self, track: Track, stream: Stream, samples: List[int], source=None
    ) -> None:
        bar = self._player_bar()
        bar.message = ""
        try:
            self.player.load(track, stream, self.client.session, samples, source)
            self.player.play()
        except PlaybackUnavailable as exc:
            self._playback_failed(str(exc))
            return
        except Exception as exc:  # a bad stream must not take the app down
            self._playback_failed(f"Could not start the stream ({exc})")
            return
        # Redraw first so the play marker lands on the new row, then chase it.
        self.refresh_rows()
        self._focus_playing_track()
        bar.refresh_bar()

    def _playback_failed(self, message: str) -> None:
        bar = self._player_bar()
        bar.message = message
        bar.refresh_bar()
        self.notify(message, severity="warning", timeout=6)

    def _focus_playing_track(self) -> None:
        """Drag the cursor to what is playing, unless you steered it away.

        Wandering down the list while something plays is normal, and having the
        cursor yanked back on every auto-advance would make it impossible.
        """

        if not self._cursor_follows:
            return
        index = self._playing_index()
        if index is not None:
            self.query_one("#tracks", DataTable).move_cursor(row=index)

    def action_seek(self, direction: int) -> None:
        if self.player.loaded is None:
            return
        self._player_op(lambda: self.player.nudge(direction * SEEK_STEP))

    def _step_from_playing(self, step: int) -> Optional[int]:
        if not self.visible_rows:
            return None
        playing = self._playing_index()
        # Nothing of ours is playing, so step from wherever you are looking.
        start = playing if playing is not None else self.query_one("#tracks", DataTable).cursor_row
        index = start + step
        return index if 0 <= index < len(self.visible_rows) else None

    def _play_at(self, index: Optional[int]) -> None:
        if index is None:
            if self.visible_rows:
                self.notify("End of the list", timeout=2)
            return
        self._start_playback(self.visible_rows[index].track)

    def action_play_step(self, step: int) -> None:
        # Asking for the next track means you want to be taken there.
        self._cursor_follows = True
        self._play_at(self._step_from_playing(step))

    def action_volume(self, direction: int) -> None:
        self._player_op(lambda: self.player.change_volume(direction * VOLUME_STEP))

    def action_mute(self) -> None:
        self._player_op(self.player.toggle_mute)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_dig_link(self) -> None:
        if self._digging:
            self.notify("Already digging - hold on", timeout=2)
            return
        message = "Paste a SoundCloud link" if self.rows else "What are we digging?"
        self.push_screen(AskLinkScreen(message=message), self._link_entered)

    def refresh_crate(self, record: Optional[CrateRecord]) -> None:
        if record is None:
            self.notify("No crate to refresh", timeout=2)
            return
        if not record.source:
            self.notify("This crate has no source to refresh from", severity="warning")
            return
        self.crate = record
        self._start_dig(record.source)

    def confirm_delete_crate(self, record: Optional[CrateRecord]) -> None:
        if record is None:
            self.notify("No crate to delete", timeout=2)
            return
        self.push_screen(
            ConfirmScreen(f"Delete the crate '{record.title}'? This cannot be undone."),
            lambda confirmed: self._crate_delete_answered(record, bool(confirmed)),
        )

    def action_refresh_crate(self) -> None:
        self.refresh_crate(self.highlighted_crate())

    def action_delete_crate(self) -> None:
        self.confirm_delete_crate(self.highlighted_crate())

    def _crate_delete_answered(self, record: CrateRecord, confirmed: bool) -> None:
        if not confirmed:
            return
        library_module.delete(record.slug)
        if self.crate is not None and self.crate.slug == record.slug:
            self.crate = None
            self.crate_title = ""
            self.load_records([])
        self.crates = library_module.list_crates()
        self.notify(f"Deleted '{record.title}'", timeout=3)
        remaining = self.latest_crate()
        if not self.rows and remaining is not None:
            self.load_crate(remaining)
        self.call_next(self.reload_sidebar)

    def action_toggle_sidebar(self) -> None:
        self.query_one("#sidebar").toggle_class("collapsed")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        record = self.highlighted_crate()
        if record is not None:
            self.load_crate(record)
        self.query_one("#tracks", DataTable).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button
        if isinstance(button, CrateButton):
            if button.intent == "refresh":
                self.refresh_crate(button.record)
            else:
                self.confirm_delete_crate(button.record)
            return
        if button.id == "crate-add":
            self.action_dig_link()

    def _link_entered(self, target: Optional[str]) -> None:
        if not target:
            if not self.rows:
                # Nothing was asked for and there is nothing to show.
                self.exit()
            return
        self._start_dig(target)

    def _start_dig(self, target: str) -> None:
        self._digging = True
        self.query_one("#tracks", DataTable).loading = True
        self._dig_message = f"Digging {target}"
        self._draw_digging()
        self._wake()
        self.dig_in_background(target)

    @work(thread=True, exclusive=True)
    def dig_in_background(self, target: str) -> None:
        def on_progress(stage: str, done: int, total: Optional[int]) -> None:
            suffix = f" {done}/{total}" if total else ""
            # The ticker draws it, so the spinner keeps turning between stages.
            self._dig_message = f"{stage}{suffix}"

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
        # Only re-ask when there is nothing to fall back to; a failed refresh
        # should not turn into a prompt for a different link.
        if not self.rows:
            self.action_dig_link()

    def _dig_finished(self, crate: Crate) -> None:
        self._finish_digging()
        if not crate.tracks:
            self._dig_failed(f"Found no tracks behind {crate.source}")
            return

        # Adding and refreshing both land here, so both persist the same way.
        record = library_module.remember(crate)
        self.load_crate(record)
        self.call_next(self.reload_sidebar)
        records = self.all_records()

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
        record = self.record_to_open(row)
        if record.link_text == links_module.NO_STORE_LINK:
            self.notify("No store link for this track - opening it on SoundCloud", timeout=3)
        elif record.link_text == links_module.FREE_DOWNLOAD:
            self.notify("Free on SoundCloud - the download button is on the page", timeout=4)
        if browser_module.open_url(record.link_url, self.browser):
            if self.status_of(row) == NEW:
                self.state.set(row.track.key, OPENED)
            self.refresh_rows()
        else:
            self.notify("Could not open the link", severity="error")

    def action_mark_got(self) -> None:
        self._toggle_status(GOT, "Got it")

    def action_mark_skip(self) -> None:
        self._toggle_status(SKIP, "Skipped")

    def action_mark_new(self) -> None:
        self._set_status(NEW, "Unmarked")

    def _reload_from_crate(self) -> None:
        """Rebuild the rows from the crate, keeping filters and cursor in place."""

        if self.crate is None:
            return
        self._set_records(links_module.categorise_all(self.crate.active_tracks))
        self.refresh_rows()

    def action_remove_track(self) -> None:
        """Drop a track from your copy. SoundCloud is read-only to us."""

        row = self.current_row()
        if row is None:
            return
        if self.crate is None:
            self.notify("This list is not a saved crate, nothing to remove from", timeout=4)
            return
        track = row.track
        self.crate.remove(track.key)
        library_module.save(self.crate)
        self._undone.append(track.key)
        self._reload_from_crate()
        self.notify(f"Removed {track.label} - ctrl+z to undo", timeout=4)

    def action_undo_remove(self) -> None:
        if self.crate is None or not self._undone:
            self.notify("Nothing to undo", timeout=2)
            return
        key = self._undone.pop()
        self.crate.restore(key)
        library_module.save(self.crate)
        self._reload_from_crate()
        self.notify("Restored", timeout=2)

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
        urls = [self.record_to_open(row).link_url for row in self.visible_rows]
        opened = browser_module.open_urls(urls, self.browser)
        for row in self.visible_rows:
            if self.status_of(row) == NEW:
                self.state.set(row.track.key, OPENED)
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
        records = [record for row in self.visible_rows for record in row.records]
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


def run_tui(
    records: Sequence[LinkRecord] = (),
    *,
    state: Optional[TrackState] = None,
    crate_title: str = "",
    browser: str = "default",
    export_format: str = "json",
    export_path: Optional[Path] = None,
    dig_options: Optional[dig_module.DigOptions] = None,
    crate_record: Optional[CrateRecord] = None,
) -> None:
    DiggerApp(
        records,
        state=state,
        crate_title=crate_title,
        browser=browser,
        export_format=export_format,
        export_path=export_path,
        dig_options=dig_options,
        crate_record=crate_record,
    ).run()

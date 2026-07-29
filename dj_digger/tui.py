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
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, ListItem, ListView, Static

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
    download_stream,
    fetch_waveform,
    resolve_stream,
)
from .soundcloud import SoundCloudClient, SoundCloudError
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
PLAYBACK = "Playback"
OTHER = "Other"

# One source for the footer and the help screen, so they cannot drift apart.
# Only a handful show in the footer - a footer with twelve entries is unreadable,
# and `?` covers the rest.
KEYMAP = [
    ("o,enter", "open_link", "Open", SELECTED, True),
    ("g", "mark_got", "Got", SELECTED, True),
    ("s", "mark_skip", "Skip", SELECTED, True),
    ("u", "mark_new", "Unmark", SELECTED, False),
    ("x", "remove_track", "Remove from this crate", SELECTED, False),
    ("ctrl+z", "undo_remove", "Undo remove", SELECTED, False),
    ("a", "open_visible", "Open all shown", WHOLE_LIST, True),
    ("e", "export", "Export shown", WHOLE_LIST, False),
    ("slash", "start_search", "Search", WHOLE_LIST, True),
    ("f", "cycle_store(1)", "Store", WHOLE_LIST, True),
    ("F", "cycle_store(-1)", "Previous store", WHOLE_LIST, False),
    ("h", "toggle_handled", "Hide handled", WHOLE_LIST, False),
    ("escape", "clear_filters", "Clear filters", WHOLE_LIST, False),
    ("space", "play_pause", "Play", PLAYBACK, True),
    ("left_square_bracket", "seek(-1)", "Back 10s", PLAYBACK, False),
    ("right_square_bracket", "seek(1)", "Forward 10s", PLAYBACK, False),
    ("n", "play_step(1)", "Next track", PLAYBACK, False),
    ("p", "play_step(-1)", "Previous track", PLAYBACK, False),
    ("minus", "volume(-1)", "Quieter", PLAYBACK, False),
    ("equals_sign", "volume(1)", "Louder", PLAYBACK, False),
    ("m", "mute", "Mute", PLAYBACK, False),
    ("d", "dig_link", "Add crate", CRATES, True),
    ("r", "refresh_crate", "Refresh crate", CRATES, False),
    ("X", "delete_crate", "Delete crate", CRATES, False),
    ("ctrl+b", "toggle_sidebar", "Show/hide crates", CRATES, False),
    ("question_mark", "help", "Help", OTHER, True),
    ("q", "quit", "Quit", OTHER, True),
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
        sections = (SELECTED, PLAYBACK, WHOLE_LIST, CRATES, OTHER)
        for section in sections:
            entries = [
                (KEY_DISPLAY.get(key, key), label)
                for key, _action, label, group, _show in KEYMAP
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
            if section != sections[-1]:
                body.append("\n")
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
        width: 24;
        border-right: solid $panel;
    }
    #sidebar.collapsed {
        display: none;
    }
    #sidebar-title {
        padding: 0 1;
        color: $text-muted;
    }
    #crates {
        height: 1fr;
        border: none;
        background: transparent;
    }
    #crate-actions {
        height: auto;
    }
    #crate-actions Button {
        min-width: 6;
        width: 1fr;
        height: 1;
        border: none;
    }
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
        Binding(key, action, label, show=show, key_display=KEY_DISPLAY.get(key))
        for key, action, label, _group, show in KEYMAP
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
        self.store_filter: str = ""
        self.search_term: str = ""
        self.hide_handled: bool = False
        self.visible_rows: List[Row] = []
        self.present: List[str] = []
        self._pending_open_all = False
        self._digging = False
        self._undone: List[str] = []
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
                with Horizontal(id="crate-actions"):
                    yield Button("+", id="crate-add", tooltip="Add a crate (d)")
                    yield Button("\u21bb", id="crate-refresh", tooltip="Refresh (r)")
                    yield Button("\u2715", id="crate-delete", tooltip="Delete (shift+X)")
            with Vertical(id="main"):
                yield Input(placeholder="Filter by artist or title", id="search")
                yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)
        yield Static(id="status")
        yield Static(id="stores")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#tracks", DataTable)
        table.add_column("#", width=4)
        table.add_column("Track", width=34)
        table.add_column("Store", width=11)
        table.add_column("Link", width=22)
        table.add_column("Genre", width=10)
        table.add_column("Status", width=7)
        await self.reload_sidebar()
        if not self.rows:
            # Someone with a library wants to see it, not be interrogated.
            latest = self.latest_crate()
            if latest is not None:
                self.load_crate(latest)
        self.refresh_rows()
        table.focus()
        self.set_interval(0.25, self._tick)
        if not self.rows:
            self.action_dig_link()

    def on_unmount(self) -> None:
        self.player.close()
        if self._client is not None:
            self._client.close()

    # Records

    def _set_records(self, records: Sequence[LinkRecord]) -> None:
        self.rows = [
            Row(position=index + 1, record=record) for index, record in enumerate(records)
        ]
        self.present = links_module.present_categories(records)
        if self.store_filter not in self.present:
            self.store_filter = ""

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
            # A star marks a crate imported from an export file, which is missing
            # fields the API would have given us.
            listing.append(ListItem(Label(record.title + (" *" if record.partial else ""))))
        if self.crate is not None:
            slugs = [record.slug for record in self.crates]
            if self.crate.slug in slugs:
                listing.index = slugs.index(self.crate.slug)

    def highlighted_crate(self) -> Optional[CrateRecord]:
        listing = self.query_one("#crates", ListView)
        index = listing.index
        if index is None or not (0 <= index < len(self.crates)):
            return self.crate
        return self.crates[index]

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
                Text(record.track.genre_label or "-", style="bright_black"),
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
        if self.crate is not None and self.crate.partial:
            pieces.append("imported from a file, press r to complete it")
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

    # Playback

    @property
    def client(self) -> SoundCloudClient:
        if self._client is None:
            self._client = SoundCloudClient()
        return self._client

    def _player_bar(self) -> PlayerBar:
        return self.query_one("#player", PlayerBar)

    def _tick(self) -> None:
        if self.player.playing:
            self._player_bar().refresh_bar()

    def unique_tracks(self) -> List[Track]:
        """Visible rows collapsed to tracks - a row is a link, so tracks repeat."""

        seen = set()
        tracks = []
        for row in self.visible_rows:
            track = row.record.track
            if track.key not in seen:
                seen.add(track.key)
                tracks.append(track)
        return tracks

    def action_play_pause(self) -> None:
        row = self.current_row()
        if row is None:
            return
        track = row.record.track
        loaded = self.player.loaded
        if loaded is not None and loaded.track.key == track.key:
            self.player.toggle()
            self._player_bar().refresh_bar()
            return
        self._start_playback(track)

    def _start_playback(self, track: Track) -> None:
        if not track.id:
            self.notify("No track id, so there is nothing to stream", timeout=4)
            return
        bar = self._player_bar()
        bar.message = f"Loading {track.label}"
        bar.refresh_bar()
        self.fetch_audio(track)

    @work(thread=True, exclusive=True, group="audio")
    def fetch_audio(self, track: Track) -> None:
        try:
            stream_url, waveform_url = resolve_stream(self.client, track.id)
            path = self.player.tempdir / f"{track.id}.mp3"
            if not path.exists():
                download_stream(self.client, stream_url, path)
            samples = fetch_waveform(self.client, waveform_url)
        except (SoundCloudError, PlaybackUnavailable, OSError) as exc:
            self.call_from_thread(self._playback_failed, str(exc))
            return
        self.call_from_thread(self._audio_ready, track, path, samples)

    def _audio_ready(self, track: Track, path: Path, samples: List[int]) -> None:
        bar = self._player_bar()
        bar.message = ""
        try:
            self.player.load(track, path, samples)
            self.player.play()
        except PlaybackUnavailable as exc:
            self._playback_failed(str(exc))
            return
        self._focus_playing_track()
        bar.refresh_bar()

    def _playback_failed(self, message: str) -> None:
        bar = self._player_bar()
        bar.message = message
        bar.refresh_bar()
        self.notify(message, severity="warning", timeout=6)

    def _focus_playing_track(self) -> None:
        loaded = self.player.loaded
        if loaded is None:
            return
        for index, row in enumerate(self.visible_rows):
            if row.record.track.key == loaded.track.key:
                self.query_one("#tracks", DataTable).move_cursor(row=index)
                return

    def action_seek(self, direction: int) -> None:
        if self.player.loaded is None:
            return
        self.player.nudge(direction * SEEK_STEP)
        self._player_bar().refresh_bar()

    def action_play_step(self, step: int) -> None:
        tracks = self.unique_tracks()
        if not tracks:
            return
        loaded = self.player.loaded
        keys = [track.key for track in tracks]
        if loaded is not None and loaded.track.key in keys:
            index = keys.index(loaded.track.key) + step
        else:
            current = self.current_row()
            index = keys.index(current.record.track.key) + step if current else 0
        if not 0 <= index < len(tracks):
            self.notify("End of the list", timeout=2)
            return
        self._start_playback(tracks[index])

    def action_volume(self, direction: int) -> None:
        self.player.change_volume(direction * VOLUME_STEP)
        self._player_bar().refresh_bar()

    def action_mute(self) -> None:
        self.player.toggle_mute()
        self._player_bar().refresh_bar()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_dig_link(self) -> None:
        if self._digging:
            self.notify("Already digging - hold on", timeout=2)
            return
        message = "Paste a SoundCloud link" if self.rows else "What are we digging?"
        self.push_screen(AskLinkScreen(message=message), self._link_entered)

    def action_refresh_crate(self) -> None:
        record = self.highlighted_crate()
        if record is None:
            self.notify("No crate to refresh", timeout=2)
            return
        if not record.source:
            self.notify("This crate has no source to refresh from", severity="warning")
            return
        self.crate = record
        self._start_dig(record.source)

    def action_delete_crate(self) -> None:
        record = self.highlighted_crate()
        if record is None:
            self.notify("No crate to delete", timeout=2)
            return
        self.push_screen(
            ConfirmScreen(f"Delete the crate '{record.title}'? This cannot be undone."),
            lambda confirmed: self._crate_delete_answered(record, bool(confirmed)),
        )

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
        actions = {
            "crate-add": self.action_dig_link,
            "crate-refresh": self.action_refresh_crate,
            "crate-delete": self.action_delete_crate,
        }
        handler = actions.get(event.button.id or "")
        if handler:
            handler()

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
        records = [row.record for row in self.rows]

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
        track = row.record.track
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


def _short_url(url: str, limit: int = 21) -> str:
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

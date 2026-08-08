"""Main Textual DiggerApp application."""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Label, Static

from .. import browser as browser_module
from .. import dig as dig_module
from .. import library as library_module
from .. import links as links_module
from ..config import AppConfig
from ..library import CrateRecord
from ..models import Crate, LinkRecord, Track
from ..player import (
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
from ..scanner import LocalScanner, copy_to_clipboard
from ..soundcloud import SoundCloudClient, SoundCloudError
from ..state import GOT, NEW, OPENED, SKIP, TrackState
from .footer import ConfigurableFooter
from .modals import ConfirmScreen, ContextMenuModal, ErrorBanner, HelpModal
from .sidebar import CrateSidebar
from .table import STATUS_BADGES, TrackTable

LOGGER = logging.getLogger(__name__)

STATUS_STYLES = {
    NEW: ("·", "bright_black", "not looked at yet"),
    OPENED: ("○", "yellow", "link opened, outcome unknown"),
    SKIP: ("✗", "bright_black", "skipped"),
    GOT: ("✓", "bold green", "got it"),
}


class DiggerApp(App[None]):
    """Interactive TUI Crate Browser & Digging Tool."""

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }
    #error_banner {
        display: none;
        height: 1;
        background: $error;
        color: $text;
    }
    #error_banner.visible {
        display: block;
    }
    #main_layout {
        layout: horizontal;
        height: 1fr;
    }
    #sidebar {
        width: 30;
        border-right: solid $accent;
    }
    #table_container {
        width: 1fr;
    }
    #search_input {
        display: none;
    }
    #search_input.visible {
        display: block;
    }
    #player_bar {
        height: 3;
        border-top: solid $accent;
    }
    #footer {
        height: 1;
        background: $primary-background;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "play_pause_or_select", "Play/Pause/Select"),
        Binding("g", "mark_got", "Got"),
        Binding("s", "mark_skip", "Skip"),
        Binding("u", "clear_mark", "Clear"),
        Binding("x", "remove_track", "Remove"),
        Binding("c", "copy_path", "Copy Path"),
        Binding("m", "context_menu", "Context Menu"),
        Binding("slash", "focus_search", "Search"),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("question", "show_help", "Help"),
    ]

    def __init__(
        self,
        target: Optional[str] = None,
        *,
        options: Optional[dig_module.DigOptions] = None,
        state: Optional[TrackState] = None,
        config: Optional[AppConfig] = None,
    ) -> None:
        super().__init__()
        self.target = target
        self.options = options or dig_module.DigOptions()
        self.state = state or TrackState()
        self.config = config or AppConfig()
        self.active_crate: Optional[CrateRecord] = None
        self.tracks: List[Track] = []
        self.player: Optional[Player] = None
        self.scanner = LocalScanner()

    def compose(self) -> ComposeResult:
        yield ErrorBanner(id="error_banner")
        with Horizontal(id="main_layout"):
            yield CrateSidebar(id="sidebar")
            with Vertical(id="table_container"):
                yield Input(placeholder="Search artist or title...", id="search_input")
                yield TrackTable(id="track_table")
        yield PlayerBar(id="player_bar")
        yield ConfigurableFooter(items=self.config.footer_keys, id="footer")

    def on_mount(self) -> None:
        try:
            self.player = Player()
        except PlaybackUnavailable as exc:
            LOGGER.warning("Audio playback unavailable: %s", exc)

        sidebar = self.query_one("#sidebar", CrateSidebar)
        sidebar.reload()

        if self.target:
            self.dig_target(self.target)
        elif sidebar.crates:
            self.load_crate(sidebar.crates[0].slug)

        self.scan_local_files_in_background()

    @work(exclusive=True, thread=True)
    def scan_local_files_in_background(self) -> None:
        scanned = self.scanner.scan()
        if scanned > 0:
            LOGGER.info("Scanned %d new local files", scanned)
        self.call_from_thread(self.apply_local_file_matches)

    def apply_local_file_matches(self) -> None:
        updated = False
        table = self.query_one("#track_table", TrackTable)
        for track in self.tracks:
            if not track.local_path:
                match = self.scanner.match_track(track)
                if match:
                    track.local_path = match
                    self.state.set(track.key, GOT)
                    updated = True
        if updated:
            self.populate_table()

    @work(exclusive=True, thread=True)
    def dig_target(self, target: str) -> None:
        try:
            client = SoundCloudClient()
            crate = client.resolve_crate(target, max_tracks=self.options.max_tracks)
            record = library_module.remember(crate)
            self.call_from_thread(self.load_crate, record.slug)
        except Exception as exc:
            self.call_from_thread(self.show_error, f"Dig failed: {exc}")

    def load_crate(self, slug: str) -> None:
        try:
            self.active_crate = library_module.load(slug)
            self.tracks = self.active_crate.active_tracks
            self.apply_local_file_matches()
            self.populate_table()
        except Exception as exc:
            self.show_error(f"Could not load crate: {exc}")

    def populate_table(self) -> None:
        table = self.query_one("#track_table", TrackTable)
        table.clear()
        table._row_track_map.clear()

        for idx, track in enumerate(self.tracks, 1):
            status = self.state.get(track.key)
            badge = STATUS_BADGES.get(status, "·")
            if track.local_path:
                badge += " 📁"
            play_indicator = " "
            stores = "soundcloud" if track.has_direct_download else "links"

            row_key = track.key
            table.add_row(
                play_indicator,
                badge,
                str(idx),
                track.label,
                stores,
                track.genre_label,
                track.duration_label,
                key=row_key,
            )
            table._row_track_map[row_key] = track

    def show_error(self, message: str) -> None:
        banner = self.query_one("#error_banner", ErrorBanner)
        banner.show(message)

    def action_play_pause_or_select(self) -> None:
        table = self.query_one("#track_table", TrackTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(table.ordered_rows):
            row_key = str(table.ordered_rows[table.cursor_row].key.value)
            table.toggle_selection(row_key)
            self.populate_table()

    def action_mark_got(self) -> None:
        self._apply_status_to_selection(GOT)

    def action_mark_skip(self) -> None:
        self._apply_status_to_selection(SKIP)

    def action_clear_mark(self) -> None:
        self._apply_status_to_selection(NEW)

    def _apply_status_to_selection(self, status: str) -> None:
        table = self.query_one("#track_table", TrackTable)
        keys = table.get_selected_or_current_track_keys()
        for key in keys:
            self.state.set(key, status)
        table.selected_row_keys.clear()
        self.populate_table()

    def action_remove_track(self) -> None:
        if not self.active_crate:
            return
        table = self.query_one("#track_table", TrackTable)
        keys = table.get_selected_or_current_track_keys()
        for key in keys:
            self.active_crate.remove(key)
        library_module.save(self.active_crate)
        self.tracks = self.active_crate.active_tracks
        table.selected_row_keys.clear()
        self.populate_table()

    def action_copy_path(self) -> None:
        table = self.query_one("#track_table", TrackTable)
        keys = table.get_selected_or_current_track_keys()
        if not keys:
            return
        track = table._row_track_map.get(keys[0])
        if track and track.local_path:
            if copy_to_clipboard(track.local_path):
                self.show_error(f"Copied local path: {track.local_path}")
            else:
                self.show_error("Could not copy path to clipboard")
        else:
            self.show_error("No local file path available for this track")

    def action_context_menu(self) -> None:
        table = self.query_one("#track_table", TrackTable)
        keys = table.get_selected_or_current_track_keys()
        if not keys:
            return
        track = table._row_track_map.get(keys[0])
        if not track:
            return

        options = [
            ("open", "🔗 Open Best Link"),
            ("got", "✓ Mark as Got"),
            ("skip", "✗ Mark as Skipped"),
            ("clear", "· Clear Mark"),
            ("remove", "🗑️ Remove Track"),
        ]
        if track.local_path:
            options.insert(1, ("copy_path", f"📁 Copy Path ({Path(track.local_path).name})"))

        def handle_context_action(action_id: Optional[str]) -> None:
            if action_id == "open":
                browser_module.open_url(track.purchase_url or track.permalink_url)
            elif action_id == "got":
                self.action_mark_got()
            elif action_id == "skip":
                self.action_mark_skip()
            elif action_id == "clear":
                self.action_clear_mark()
            elif action_id == "remove":
                self.action_remove_track()
            elif action_id == "copy_path":
                self.action_copy_path()

        self.push_screen(ContextMenuModal(track.title, options), handle_context_action)

    def on_track_table_context_menu_requested(self, event: TrackTable.ContextMenuRequested) -> None:
        self.action_context_menu()

    def action_focus_search(self) -> None:
        inp = self.query_one("#search_input", Input)
        inp.add_class("visible")
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip().lower()
        inp = self.query_one("#search_input", Input)
        inp.remove_class("visible")
        if not self.active_crate:
            return
        if not query:
            self.tracks = self.active_crate.active_tracks
        else:
            self.tracks = [
                t for t in self.active_crate.active_tracks
                if query in t.title.lower() or query in t.artist.lower()
            ]
        self.populate_table()

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", CrateSidebar)
        sidebar.display = not sidebar.display

    def action_show_help(self) -> None:
        self.push_screen(HelpModal())


def make_app(
    target: Optional[str] = None,
    *,
    options: Optional[dig_module.DigOptions] = None,
    state: Optional[TrackState] = None,
) -> DiggerApp:
    return DiggerApp(target, options=options, state=state)


def run(
    target: Optional[str] = None,
    *,
    options: Optional[dig_module.DigOptions] = None,
    state: Optional[TrackState] = None,
) -> None:
    app = make_app(target, options=options, state=state)
    app.run()

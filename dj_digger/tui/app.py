"""The crate browser application itself: state, actions and workers."""

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Input, ListView, Static

from .. import dig as dig_module
from .. import links as links_module
from ..library import CrateRecord
from ..models import LinkRecord
from ..player import (
    Player,
    PlayerBar,
)
from ..soundcloud import SoundCloudClient
from ..state import TrackState
from .crates import CrateMixin
from .digging import DiggingMixin
from .downloads import DownloadMixin
from .filters import FilterMixin
from .keymap import (
    GENRE_WIDTH,
    INDEX_WIDTH,
    KEY_DISPLAY,
    KEYMAP,
    MARK_WIDTH,
    MIN_TITLE_WIDTH,
    PLAYING_GLYPH,
    QUICK_FILTER_KEYS,
    STORES_WIDTH,
    TIME_WIDTH,
)
from .library_scan import LibraryScanMixin
from .opening import OpeningMixin
from .playback import PlaybackMixin
from .render import RenderMixin
from .rows import Prepared, Row
from .screens import HelpScreen, SettingsScreen
from .widgets import ErrorBanner, StatusBar, TrackTable

LOGGER = logging.getLogger(__name__)


class DiggerApp(
    CrateMixin,
    RenderMixin,
    FilterMixin,
    PlaybackMixin,
    DiggingMixin,
    DownloadMixin,
    OpeningMixin,
    LibraryScanMixin,
    App,
):
    """The crate browser.

    The mixins carry one concern each and never override one another - there is
    a test that says so, because a name defined twice is how this class lost an
    ``on_unmount`` for two releases. What is left here is the shell: the layout,
    the bindings, the state they all reach for, and setup and teardown.
    """
    # The built-in palette showed up in the footer as an unexplained "palette".
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    DiggerApp {
        layers: base top;
    }
    #error-banner {
        width: 100%;
        dock: top;
        layer: top;
    }
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
        state: TrackState | None = None,
        crate_title: str = "",
        browser: str = "default",
        export_format: str = "json",
        export_path: Path | None = None,
        dig_options: dig_module.DigOptions | None = None,
        crate_record: CrateRecord | None = None,
    ) -> None:
        super().__init__()
        self.rows: list[Row] = []
        self.state = state or TrackState()
        self.crate = crate_record
        self.crates: list[CrateRecord] = []
        self.crate_title = crate_title or (crate_record.title if crate_record else "")
        self.browser = browser
        self.export_format = export_format
        self.export_path = export_path
        self.dig_options = dig_options or dig_module.DigOptions()
        self.store_filters: set[str] = set()
        self._badge_click_regions: list[tuple[int, int, int]] = []
        self.search_term: str = ""
        self.hide_handled: bool = False
        self.visible_rows: list[Row] = []
        self.present: list[str] = []
        self._pending_open_all = False
        self._digging = False
        self._undone: list[str] = []
        self._ticker: Timer | None = None
        # Decided fresh each time playback moves: does the cursor come along?
        self._cursor_follows = True
        self._prepared: Prepared | None = None
        self._preparing: str = ""
        self._frame = 0
        self._dig_message = ""
        self.download_progress: dict[str, float] = {}
        self._last_progress_redraw: float = 0.0
        # Only a batch download builds one. Declared here so the teardown path
        # can ask about it plainly rather than through getattr.
        self._download_executor: ThreadPoolExecutor | None = None
        self.player = Player()
        self._client: SoundCloudClient | None = None
        self._set_records(records)

    def compose(self) -> ComposeResult:
        yield ErrorBanner(id="error-banner")
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

    def show_error(self, message: str) -> None:
        """Display an error/debug message in the top ErrorBanner."""
        try:
            banner = self.query_one(ErrorBanner)
            banner.add_error(message)
        except Exception:
            LOGGER.error("Error: %s", message)
            self.notify(f"Error: {message}", severity="error", timeout=8)

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
        # Off the interface thread: a first scan of a real music folder takes a
        # while, and the crate is usable long before it finishes.
        self.scan_local_files()
        if not self.rows:
            self.action_dig_link()

    def on_unmount(self) -> None:
        """Let go of everything this screen owns, in one place.

        There were two of these, and Python kept the second - so the ticker went
        on running and the download pool was shut down twice over while the
        prefetched stream was closed by hand rather than through its own method.

        No ``workers.cancel_all()``: Textual runs one itself, and traced against
        8.2.8 it lands before this method is dispatched, not after.
        """

        # A tick landing after the widgets have gone would go looking for a
        # player bar that no longer exists. Textual does stop its timers, but
        # only further down the same teardown, so this one goes first.
        if self._ticker is not None:
            self._ticker.stop()
            self._ticker = None
        if self._download_executor is not None:
            self._download_executor.shutdown(wait=False, cancel_futures=True)
            self._download_executor = None
        self._discard_prepared()
        self.player.close()
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> SoundCloudClient:
        if self._client is None:
            self._client = SoundCloudClient()
        return self._client

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_open_settings(self) -> None:
        self.push_screen(SettingsScreen(self.client.config))

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_open_link()

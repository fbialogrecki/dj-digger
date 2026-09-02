"""The crate browser application itself: state, actions and workers."""

import asyncio
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Button, DataTable, ListView, Static
from textual.widgets.data_table import ColumnKey

from .. import cart as cart_module
from .. import dig as dig_module
from .. import links as links_module
from ..config import AppConfig
from ..library import CrateHeader, CrateRecord
from ..models import LinkRecord
from ..player import (
    Player,
    PlayerBar,
    PlayerControls,
)
from ..soundcloud import SoundCloudClient
from ..state import TrackState
from .crates import CrateMixin
from .digging import DiggingMixin
from .downloads import DownloadMixin
from .filters import FilterMixin
from .jobs import Job, JobMixin
from .keymap import (
    KEY_DISPLAY,
    KEYMAP,
    PRIORITY_KEYS,
    QUICK_FILTER_KEYS,
)
from .library_scan import LibraryScanMixin
from .opening import OpeningMixin
from .playback import PlaybackMixin
from .render import RenderMixin
from .rows import Prepared, Row
from .screens import ContextMenuScreen, HelpScreen, SettingsScreen
from .theme import CORRECTED_THEMES, FALLBACK_PALETTE, Palette, palette_for
from .widgets import ErrorBanner, FittedFooter, SearchInput, StatusBar, TrackTable

LOGGER = logging.getLogger(__name__)

# The table needs 79 columns before the title stops shrinking, and the sidebar
# and its border cost 29. Below the sum of the two the sidebar is crate names
# paid for with Genre, Time and half the title, so it folds itself away.
NARROW_WIDTH = 110


class DiggerApp(
    CrateMixin,
    JobMixin,
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
    # Off: it brings Textual's own Screenshot, Maximize and Theme commands
    # along, none of which belongs in a crate browser. Settings and ? cover
    # everything the app itself offers.
    ENABLE_COMMAND_PALETTE = False
    # Otherwise the terminal's window and tab say "DiggerApp", which is the name
    # of the class rather than of anything the user installed.
    TITLE = "dj-digger"

    CSS = """
    #error-banner {
        width: 100%;
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
    /* Centred over the list it heads, with a blank row under it: the crate
       names started immediately below and read as one more crate. */
    #sidebar-title {
        padding: 0 1;
        margin-bottom: 1;
        text-align: center;
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
    /* One line, like the status bar: the default Input spends three rows on a
       border to hold one row of text, and this sits above the list you are
       filtering. */
    #search {
        display: none;
        height: 1;
        border: none;
        padding: 0 1;
        background: $panel;
    }
    #search.visible {
        display: block;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding(
            key,
            action,
            label,
            show=show,
            key_display=KEY_DISPLAY.get(key),
            priority=key in PRIORITY_KEYS,
        )
        for key, action, label, _group, show, _detail in KEYMAP
    ] + [
        # Textual 8 answers ctrl+c with a toast saying to press ctrl+q, which
        # is not what anyone reaching for ctrl+c wants. A binding on the app
        # replaces the base one for the same key; priority puts it ahead of
        # the search box, where Input would otherwise take ctrl+c as "copy".
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
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
        export_format: str = "json",
        export_path: Path | None = None,
        dig_options: dig_module.DigOptions | None = None,
        crate_record: CrateRecord | None = None,
    ) -> None:
        super().__init__()
        self.rows: list[Row] = []
        self.state = state or TrackState()
        # One profile for the whole app: Settings edits this object, and the
        # SoundCloud client is handed the same one so the gate resolvers see
        # your name and email rather than a copy loaded before you changed them.
        self.config = AppConfig()
        self.crate = crate_record
        self.crates: list[CrateHeader] = []
        self.crate_title = crate_title or (crate_record.title if crate_record else "")
        # load_records sets this when you switch crates; this covers the one the
        # command line opened us on.
        self.sub_title = self.crate_title
        self.export_format = export_format
        self.export_path = export_path
        self.dig_options = dig_options or dig_module.DigOptions()
        self.store_filters: set[str] = set()
        self._badge_click_regions: list[tuple[int, int, int]] = []
        self.search_term: str = ""
        self.hide_handled: bool = False
        # Track keys chosen with v / V / ctrl+a; whole-list actions prefer them.
        self.selected: set[str] = set()
        self._anchor: int | None = None
        self.sort_key: str | None = None
        self.sort_reverse: bool = False
        self._column_keys: dict[str, ColumnKey] = {}
        self.visible_rows: list[Row] = []
        self.present: list[str] = []
        # The key whose bulk open is waiting for a second press (see _confirm_many).
        self._pending_open: str | None = None
        self._cart_busy = False
        self._cart_cancel = asyncio.Event()
        self._cart_session = cart_module.CartBrowserSession()
        self._cart_progress_screen = None
        self._gate_cancel = Event()
        self._dig_cancel = Event()
        self._scan_cancel = Event()
        self._browser_batch_active = False
        self._digging = False
        # The long job the status bar reports on, if any (see jobs.py).
        self.job: Job | None = None
        self._undone: list[str] = []
        self._ticker: Timer | None = None
        # Decided fresh each time playback moves: does the cursor come along?
        self._cursor_follows = True
        self._prepared: Prepared | None = None
        self._preparing: str = ""
        self._frame = 0
        self._dig_message = ""
        self.download_progress: dict[str, float] = {}
        self._dirty_download_rows: set[str] = set()
        self._last_progress_redraw: float = 0.0
        # Only a batch download builds one. Declared here so the teardown path
        # can ask about it plainly rather than through getattr.
        self._download_executor: ThreadPoolExecutor | None = None
        self._download_worker_lock = Lock()
        self._active_download_workers = 0
        self._client_refresh_pending = False
        self._client_refresh_token: str | None = None
        self._client_refresh_callbacks: list = []
        # None until the first resize, so the first one always applies.
        self._narrow: bool | None = None
        self.player = Player()
        self._client: SoundCloudClient | None = None
        # The interface's colour roles under the active theme, recomputed
        # whenever the theme changes (see tui/theme.py).
        self.palette: Palette = FALLBACK_PALETTE
        for theme in CORRECTED_THEMES:
            self.register_theme(theme)
        self._set_records(records)

    def compose(self) -> ComposeResult:
        yield ErrorBanner(id="error-banner")
        yield PlayerBar(self.player, id="player")
        yield PlayerControls(self.player, id="player-controls")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("Crates", id="sidebar-title")
                yield ListView(id="crates")
                yield Button("+ Add crate", id="crate-add", tooltip="Add a crate (d)")
            with Vertical(id="main"):
                yield SearchInput(placeholder="Filter by artist, title, genre, tag or label", id="search")
                yield TrackTable(id="tracks", cursor_type="row", zebra_stripes=True)
        yield StatusBar(id="status")
        yield FittedFooter()

    def on_resize(self, event: events.Resize) -> None:
        """Give the narrow terminal back the columns it does not have.

        Both of these already had a manual switch - ``ctrl+b`` for the sidebar,
        ``?`` for the full key list - so this only decides for you at the widths
        where there is nothing to decide.
        """

        narrow = event.size.width < NARROW_WIDTH
        if narrow != self._narrow:
            self._narrow = narrow
            self.query_one("#sidebar").set_class(narrow, "collapsed")
        # The footer picks which bindings fit in its own compose, which resize
        # does not otherwise trigger. Queued on the footer rather than on the
        # app: composing a widget from the app's message pump breaks the data
        # binding Textual's Footer sets up on its own keys.
        footer = self.query_one(FittedFooter)
        footer.call_next(footer.recompose)

    def _handle_exception(self, error: Exception) -> None:
        """Put the crash in the log before Textual tears the screen down.

        A crash report printed to the alternate screen is gone the moment the
        terminal is restored, which is how a session could die with nothing to
        show for it - the log file ended at the last ordinary record. Private
        API, pinned by the test that mounts and crashes an app on purpose.
        """

        LOGGER.exception("Unhandled exception in the TUI", exc_info=error)
        super()._handle_exception(error)

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
        self.build_columns(table)
        if self.config.theme in self.available_themes and self.theme != self.config.theme:
            self.theme = self.config.theme
        else:
            self.palette = palette_for(self.get_css_variables(), self.current_theme)
        await self.reload_sidebar()
        if not self.rows:
            # Someone with a library wants to see it, not be interrogated.
            latest = self.latest_crate()
            if latest is not None:
                self.open_crate(latest)
        self.refresh_rows()
        table.focus()
        # Needs a laid-out width to size itself against.
        self.call_after_refresh(table.fit_flexible_column)
        # Asleep until there is something to animate: waking thirty times a
        # second to look at a list nobody is playing is just a warm laptop.
        self._ticker = self.set_interval(self.frame_interval, self._tick, pause=True)
        if self.config.first_run:
            # Nothing is configured yet, and one of the things being asked about
            # is which folders to scan, so the scan waits for the answer too.
            self.push_screen(SettingsScreen(self.config), lambda _: self._after_setup())
        else:
            self._after_setup()

    def _after_setup(self) -> None:
        # Off the interface thread: a first scan of a real music folder takes a
        # while, and the crate is usable long before it finishes.
        self.scan_local_files()
        if not self.rows:
            self.action_dig_link()

    async def on_unmount(self) -> None:
        """Let go of everything this screen owns, in one place.

        There were two of these, and Python kept the second - so the ticker went
        on running and the download pool was shut down twice over while the
        prefetched stream was closed by hand rather than through its own method.

        No ``workers.cancel_all()``: Textual runs one itself, and traced against
        8.2.8 it lands before this method is dispatched, not after.
        """

        # The async Playwright context lives on this same event loop. Textual has
        # cancelled its workers by now; close the persistent profile explicitly.
        self._cart_cancel.set()
        self._gate_cancel.set()
        self._dig_cancel.set()
        self._scan_cancel.set()
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
        # This runs before Textual gives the terminal back, so a Playwright
        # that will not answer would hang the exit with no key able to reach
        # us. Five seconds, then the process-exit guard in run_tui takes over.
        try:
            await asyncio.wait_for(self._cart_session.close(), timeout=5)
        except Exception as exc:  # TimeoutError included
            LOGGER.warning("Store browser did not close cleanly: %s", exc)
        if self._client is not None:
            self._client.close()
            self._client = None

    @property
    def muted(self) -> str:
        """Rich style for secondary text under the current theme."""

        return self.palette.muted

    def role(self, style: str) -> str:
        """A keymap style such as "bold success" resolved to the theme's colour."""

        words = style.split()
        if not words:
            return style
        name = words[-1]
        colour = getattr(self.palette, name, None)
        if not isinstance(colour, str) or not colour:
            return style
        return " ".join([*words[:-1], colour])

    def watch_theme(self, theme: str) -> None:
        """Keep the colour roles and the saved preference in step with the theme."""

        try:
            self.palette = palette_for(self.get_css_variables(), self.current_theme)
        except Exception:
            self.palette = FALLBACK_PALETTE
        if self.config.theme != theme and not self.config.first_run:
            self.config.theme = theme
            self.config.save()
        if self.is_mounted and self.query("#tracks"):
            self.refresh_rows()

    @property
    def client(self) -> SoundCloudClient:
        if self._client is None:
            self._client = SoundCloudClient(config=self.config)
        return self._client

    @property
    def browser(self) -> str:
        """What Settings says. Empty means the system default.

        Read fresh each time rather than settled in __init__, so changing it in
        Settings takes effect on the next link instead of the next run.
        """

        return self.config.browser

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_open_settings(self) -> None:
        self.push_screen(SettingsScreen(self.config))

    def action_export(self) -> None:
        if self.export_format == "none":
            self.notify("Export is disabled for this run", timeout=3)
            return
        records = [record for row in self.targets() for record in row.records]
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

    def on_track_table_context_menu_requested(
        self, event: TrackTable.ContextMenuRequested
    ) -> None:
        event.stop()
        row = self.current_row()
        if row is None:
            return
        if self._forget_missing_local_file(row.track):
            self._paint_key(row.track.key)
        entries = [
            ("open", "Open best link", self.action_open_link),
            ("got", "Mark as got", self.action_mark_got),
            ("skip", "Mark as skipped", self.action_mark_skip),
            ("new", "Clear mark", self.action_mark_new),
            ("remove", "Remove track", self.action_remove_track),
        ]
        if row.track.local_path and Path(row.track.local_path).is_file():
            entries.insert(1, ("copy", "Copy local file path", self.action_copy_path))
            if self._local_file_needs_copy(row.track):
                entries.insert(
                    2, ("copy_file", "Copy file to playlist folder", self.action_copy_local_file)
                )

        actions = {key: handler for key, _label, handler in entries}
        self.push_screen(
            ContextMenuScreen(
                row.track.label, tuple((key, label) for key, label, _handler in entries)
            ),
            lambda action: actions.get(action, lambda: None)(),
        )

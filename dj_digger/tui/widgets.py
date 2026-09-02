"""The widgets the crate browser is built out of, screens excepted."""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Footer, Input, Label, ListItem, Static
from textual.widgets._footer import FooterKey
from textual.widgets.data_table import ColumnKey

from ..library import CrateHeader
from .keymap import FOOTER_OPTIONAL, MIN_TITLE_WIDTH


class FittedFooter(Footer):
    """A footer that gives keys up rather than clipping the last one mid-word.

    Textual's footer lays its bindings out and lets the row overflow, so the
    thirteen this app shows were cut at "space P" on anything under about 145
    columns. Filtering in ``compose`` rather than by hiding the widgets
    afterwards, because the footer recomposes itself whenever focus moves and
    would undo that on the next keystroke.
    """

    def _dropped_actions(self) -> set[str]:
        """Which bindings will not fit, decided before any widget is built."""

        budget = self.size.width or self.app.size.width
        cost: dict[str, int] = {}
        for _node, binding, _enabled, _tooltip in self.screen.active_bindings.values():
            if binding.show and binding.action not in cost:
                key_display = self.app.get_key_display(binding)
                cost[binding.action] = len(key_display) + len(binding.description) + 3
        total = sum(cost.values())
        dropped: set[str] = set()
        for action in FOOTER_OPTIONAL:
            if total <= budget:
                break
            if action in cost:
                dropped.add(action)
                total -= cost[action]
        return dropped

    def compose(self) -> ComposeResult:
        dropped = self._dropped_actions()
        for key in super().compose():
            if isinstance(key, FooterKey) and key.action in dropped:
                continue
            yield key


class TrackTable(DataTable):
    """A table whose title column absorbs whatever width is left over.

    DataTable columns are fixed or content-sized, neither of which fills the
    terminal, so the width is worked out here and refreshed whenever the table
    is resized - by the terminal, or by the sidebar folding away.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.flexible_column: ColumnKey | None = None
        self._right_click_pending = False
        self._left_click_pending = False

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
        # scrollable_content_region, not size: the vertical scrollbar owns two
        # of those columns, and spending them on the title put the table one
        # column over and hung a horizontal scrollbar under an 80-column
        # terminal with the last digit of Time behind the edge.
        available = self.scrollable_content_region.width or self.size.width
        width = available - spent - 2 * self.cell_padding
        column.width = max(MIN_TITLE_WIDTH, width)
        self.refresh(layout=True)

    class ContextMenuRequested(Message):
        pass

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._right_click_pending = event.button == 3
        self._left_click_pending = event.button == 1

    def _post_selected_message(self) -> None:
        # DataTable.RowSelected no longer carries the mouse button, so suppress
        # it here before the app mistakes a right click for Enter.
        if not self._right_click_pending and not self._left_click_pending:
            super()._post_selected_message()

    async def _on_click(self, event: events.Click) -> None:
        if event.button == 1 and event.chain == 1:
            row = event.style.meta.get("row")
            column = event.style.meta.get("column")
            if isinstance(row, int) and 0 <= row < self.row_count:
                event.stop()
                self.move_cursor(
                    row=row,
                    column=column if isinstance(column, int) else None,
                )
                self.focus()
                self.call_later(setattr, self, "_left_click_pending", False)
                return
        if event.button != 3:
            self._left_click_pending = False
            await super()._on_click(event)
            return
        event.stop()
        row = event.style.meta.get("row")
        column = event.style.meta.get("column")
        if not isinstance(row, int) or not 0 <= row < self.row_count:
            return
        self.move_cursor(row=row, column=column if isinstance(column, int) else None)
        self.post_message(self.ContextMenuRequested())
        self.call_later(setattr, self, "_right_click_pending", False)


class SearchInput(Input):
    """The filter box, carrying the one key that gets you back out of it.

    Textual's footer shows the bindings that are live for whatever has focus,
    and an Input swallows every printable key - so while you are typing here the
    footer emptied out to the key you had just pressed. Escape already worked;
    it was simply never on screen.
    """

    BINDINGS = [Binding("escape", "app.leave_search", "Back to the list")]


class LegendText(Static):
    """The store legend; a click on a store name is the number key for it."""

    def on_click(self, event: events.Click) -> None:
        app = self.app
        x = event.x
        for start_x, end_x, store_idx in app._badge_click_regions:
            if start_x <= x < end_x:
                app.action_filter_index(store_idx)
                break


class StatusBar(ScrollableContainer, can_focus=False, can_focus_children=False):
    """The bar above the footer: the store legend, and the running job on the right.

    Built the way Textual's Footer is - a horizontal scrollable container with
    its scrollbar hidden - so a legend wider than the terminal is never clipped:
    the mouse wheel (or a drag) scrolls it sideways, and every store stays
    reachable by its number key regardless. The job line is docked right so a
    spinner is never scrolled out of sight.
    """

    ALLOW_SELECT = False
    DEFAULT_CSS = """
    StatusBar {
        layout: horizontal;
        height: 1;
        padding: 0 1;
        scrollbar-size: 0 0;
        color: $text-muted;
    }
    StatusBar > #status-legend {
        width: auto;
        height: 1;
    }
    StatusBar > #status-job {
        dock: right;
        width: auto;
        height: 1;
        padding-left: 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield LegendText(id="status-legend")
        yield Static(id="status-job")

    def on_resize(self, event: events.Resize) -> None:
        self.app.update_status()

    # Textual scrolls a container sideways only with shift or ctrl held; a
    # plain wheel scrolls vertically, and a one-line bar has no vertical to
    # give. Here the wheel is the sideways scroll, whichever way it is turned.
    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._scroll_right_for_pointer(animate=False):
            event.stop()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._scroll_left_for_pointer(animate=False):
            event.stop()


class ErrorBanner(Widget):
    """Top bar for errors: one summary line, with the messages a click away.

    A batch download that fails on thirteen gates used to open half the screen
    the moment it finished, over the list you were reading. What is worth that
    much room at that instant is the count; the messages themselves stay one
    click - and one scroll - away.
    """

    DEFAULT_CSS = """
    ErrorBanner {
        display: none;
        background: $error-darken-2;
        color: white;
        height: auto;
        max-height: 12;
        width: 100%;
    }
    ErrorBanner.visible {
        display: block;
        height: 1;
    }
    ErrorBanner.visible.expanded {
        height: auto;
    }
    #error-head {
        height: 1;
        width: 100%;
    }
    #error-summary {
        width: 1fr;
        height: 1;
        padding: 0 1;
        text-style: bold;
    }
    #error-summary:hover {
        background: $error;
    }
    /* Hidden until asked for, and then no more than ten lines of it - past that
       it is a log, and a log belongs in a scroll box rather than on the screen. */
    #error-scroll {
        display: none;
        width: 100%;
        height: auto;
        max-height: 10;
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-background: $error-darken-2;
        scrollbar-color: $error-lighten-2;
        scrollbar-color-hover: white;
        scrollbar-color-active: white;
    }
    ErrorBanner.expanded #error-scroll {
        display: block;
    }
    #error-text {
        width: 1fr;
        height: auto;
    }
    /* Three columns and no border: the old seven-wide yellow block read as the
       most important thing on a screen it was only there to get out of. */
    #error-close {
        width: 3;
        min-width: 3;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
        color: white;
    }
    #error-close:hover {
        background: $error;
        text-style: bold;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.errors: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="error-head"):
            yield Static("", id="error-summary")
            yield Button("\u2715", id="error-close", tooltip="Dismiss all errors")
        with VerticalScroll(id="error-scroll"):
            yield Static("", id="error-text")

    def add_error(self, message: str) -> None:
        if message and message not in self.errors:
            self.errors.append(message)
        self._update_display()

    def clear_errors(self) -> None:
        self.errors.clear()
        # Collapsed again, so the next failure does not arrive pre-opened.
        self.remove_class("expanded")
        self._update_display()

    def _update_display(self) -> None:
        try:
            summary = self.query_one("#error-summary", Static)
            messages = self.query_one("#error-text", Static)
        except Exception:
            return
        if not self.errors:
            self.remove_class("visible")
            summary.update("")
            messages.update("")
            return

        self.add_class("visible")
        open_now = self.has_class("expanded")
        arrow, verb = ("\u25be", "hide") if open_now else ("\u25b8", "read")
        count = len(self.errors)
        plural = "" if count == 1 else "s"
        summary.update(
            Text(
                f"{arrow} {count} error{plural} - click to {verb}",
                style=f"bold {self.app.palette.warning}",
            )
        )
        # Text(), not markup: a failure that quotes a track called "Rido - Sexy
        # Thing [Clip]" must not lose the bracket to the markup parser.
        messages.update(Text("\n".join(f"\u2022 {message}" for message in self.errors)))

    def on_click(self, event: events.Click) -> None:
        # Only the summary toggles. Clicking a message you are trying to read
        # should not be what closes it.
        widget = getattr(event, "widget", None)
        if self.errors and widget is not None and widget.id == "error-summary":
            self.toggle_class("expanded")
            self._update_display()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "error-close":
            self.clear_errors()


class CrateButton(Button):
    """A per-crate icon button. Carries its crate so no widget ids are needed."""

    def __init__(self, label: str, record: CrateHeader, intent: str, tooltip: str) -> None:
        super().__init__(label, classes="crate-icon", tooltip=tooltip)
        self.record = record
        self.intent = intent


class CrateItem(ListItem):
    """One sidebar row. Carries the crate's header only; the tracks load on select."""

    def __init__(self, record: CrateHeader) -> None:
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

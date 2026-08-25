"""The widgets the crate browser is built out of, screens excepted."""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DataTable, Footer, Input, Label, ListItem, Static
from textual.widgets._footer import FooterKey
from textual.widgets.data_table import ColumnKey

from ..library import CrateRecord
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

    BINDINGS = [Binding("escape", "app.clear_filters", "Clear search")]


class StatusBar(Static):
    """The bottom bar, which has to be rebuilt whenever its width changes.

    Whether the counts fit beside the store legend is a width question, and the
    app-level resize event fires before the layout settles - so the widget that
    actually changed size is the one that has to ask.
    """

    def on_resize(self, event: events.Resize) -> None:
        self.app.update_status()

    def on_click(self, event: events.Click) -> None:
        app = self.app
        x = event.x
        for start_x, end_x, store_idx in app._badge_click_regions:
            if start_x <= x < end_x:
                app.action_filter_index(store_idx)
                break


class ErrorBanner(Widget):
    """Top bar displaying error/debug messages with a scrollable view and an [X] close button."""

    DEFAULT_CSS = """
    ErrorBanner {
        display: none;
        background: $error-darken-2;
        color: white;
        height: auto;
        max-height: 12;
        width: 100%;
        padding: 0 1;
        dock: top;
        border-bottom: solid $error;
    }
    ErrorBanner.visible {
        display: block;
    }
    #error-container {
        width: 100%;
        height: auto;
        max-height: 11;
    }
    #error-scroll {
        width: 1fr;
        height: auto;
        max-height: 10;
        overflow-y: scroll;
    }
    #error-text {
        width: 1fr;
        height: auto;
    }
    #error-close {
        width: 7;
        min-width: 7;
        height: 1;
        border: none;
        background: $error;
        color: white;
        text-style: bold;
    }
    #error-close:hover {
        background: yellow;
        color: black;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.errors: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="error-container"):
            with VerticalScroll(id="error-scroll"):
                yield Static("", id="error-text")
            yield Button("X", id="error-close", tooltip="Close error banner (clear all errors)")

    def add_error(self, message: str) -> None:
        if message and message not in self.errors:
            self.errors.append(message)
        self._update_display()

    def clear_errors(self) -> None:
        self.errors.clear()
        self._update_display()

    def _update_display(self) -> None:
        try:
            msg_widget = self.query_one("#error-text", Static)
        except Exception:
            return
        if not self.errors:
            self.remove_class("visible")
            msg_widget.update("")
            return

        self.add_class("visible")
        content = Text(
            f"Errors / Debug Log ({len(self.errors)} total, scrollable):\n",
            style="bold yellow",
        )
        for index, message in enumerate(self.errors):
            if index:
                content.append("\n")
            content.append(f"• {message}")
        msg_widget.update(content)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "error-close":
            self.clear_errors()


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

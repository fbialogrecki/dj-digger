"""The widgets the crate browser is built out of, screens excepted."""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Button, DataTable, Label, ListItem, Static
from textual.widgets.data_table import ColumnKey

from ..library import CrateRecord
from .keymap import MIN_TITLE_WIDTH


class TrackTable(DataTable):
    """A table whose title column absorbs whatever width is left over.

    DataTable columns are fixed or content-sized, neither of which fills the
    terminal, so the width is worked out here and refreshed whenever the table
    is resized - by the terminal, or by the sidebar folding away.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.flexible_column: ColumnKey | None = None

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

    def on_click(self, event: events.Click) -> None:
        app = self.app
        if not hasattr(app, "_badge_click_regions"):
            return
        x = event.x
        for start_x, end_x, store_idx in getattr(app, "_badge_click_regions", []):
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
            yield Button("[X]", id="error-close", tooltip="Close error banner (clear all errors)")

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
        else:
            self.add_class("visible")
            formatted = "\n".join(f"• {e}" for e in self.errors)
            header = f"[bold yellow]Errors / Debug Log ({len(self.errors)} total, scrollable):[/bold yellow]\n"
            msg_widget.update(f"{header}{formatted}")

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

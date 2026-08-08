"""Modal dialogs, popovers, context menus, and error banners for Textual TUI."""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option


class ContextMenuModal(ModalScreen[Optional[str]]):
    """Right-click / keyboard context menu popover for track actions."""

    BINDINGS = [
        ("escape", "cancel", "Close"),
    ]

    def __init__(
        self,
        track_title: str,
        options: List[Tuple[str, str]],
        *,
        name: Optional[str] = None,
        id: Optional[str] = None,
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.track_title = track_title
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="context_menu_dialog"):
            yield Label(f" Track Actions: {self.track_title[:35]}", id="context_title")
            option_items = [Option(label, id=action_id) for action_id, label in self.options]
            yield OptionList(*option_items, id="context_options")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class AskLinkScreen(ModalScreen[Optional[str]]):
    """Modal screen prompting for a SoundCloud URL."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="ask_link_dialog"):
            yield Label("Paste a SoundCloud playlist, likes, or track URL:")
            yield Input(placeholder="https://soundcloud.com/...", id="ask-input")
            yield Button("Dig", variant="primary", id="dig_button")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        inp = self.query_one("#ask-input", Input)
        self.dismiss(inp.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Modal screen asking for Yes/No confirmation."""

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm_dialog"):
            yield Label(self.message, id="confirm_label")
            yield Button("Yes (y)", variant="error", id="confirm_yes")
            yield Button("No (n)", variant="primary", id="confirm_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm_yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ErrorBanner(Static):
    """Top bar displaying error or status messages."""

    def show(self, message: str) -> None:
        self.update(f" ⚠️ {message}")
        self.add_class("visible")

    def hide(self) -> None:
        self.remove_class("visible")
        self.update("")


class HelpModal(ModalScreen[None]):
    """Modal help screen showing keybindings."""

    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close"), ("?", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Label("🎧 dj-soundcloud-digger Shortcuts", id="help_title")
            yield Static(
                " Navigation:\n"
                "  Up/Down / j/k    Navigate track rows\n"
                "  Space            Play/Pause or Toggle Multi-Select\n"
                "  Shift+Up/Down    Select range of rows\n"
                "  o / Enter        Open best link in browser\n"
                "  m / Shift+F10    Open Context Menu (Right Click)\n"
                "  c                Copy local file path\n\n"
                " Track Marks (Applies to selection):\n"
                "  g                Mark as Got (✓)\n"
                "  s                Mark as Skipped (✗)\n"
                "  u                Clear status mark (·)\n"
                "  x                Remove track from crate\n\n"
                " Filtering & Library:\n"
                "  /                Search filter\n"
                "  1-9              Filter by store category\n"
                "  0                Clear store filter\n"
                "  d                Add crate from URL\n"
                "  r                Refresh crate\n"
                "  Ctrl+B           Toggle Crate Sidebar\n"
            )
            yield Button("Close (Esc)", id="close_help")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


HelpScreen = HelpModal

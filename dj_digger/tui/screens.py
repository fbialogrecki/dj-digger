"""The modal screens: asking for a link, help, confirmation and settings."""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Input, Label, Select, Static

from .. import browser as browser_module
from ..config import AppConfig
from .keymap import (
    CRATES,
    HELP_EXTRA,
    HELP_SCOPES,
    KEY_DISPLAY,
    KEYMAP,
    OTHER,
    PLAYBACK,
    PLAYING_GLYPH,
    SELECTED,
    STATUS_STYLES,
    WHOLE_LIST,
)


class AskLinkScreen(ModalScreen[str | None]):
    """Asks for a SoundCloud link (or a saved HTML file)."""

    CSS = """
    AskLinkScreen {
        align: center middle;
    }
    #ask {
        width: 78;
        max-width: 90%;
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
        yield Footer()

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
    /* 64, not 56: the widest line this builds is 60 columns, and at 56 every
       description longer than the box wrapped back to column 0, leaving
       "store" and "matches" hanging underneath as if they were keys. Width auto
       does not work here - a Static holding a Text does not report a width - so
       it is measured against _body() instead, and max-width keeps it inside a
       small terminal, where it wraps again but has nowhere else to go. */
    #help {
        width: 66;
        max-width: 90%;
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
        yield Footer()

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
        body.append(f"  {'NEW':<10}", style="bold yellow")
        body.append("added by the last refresh\n")
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
        max-width: 90%;
        height: auto;
        max-height: 90%;
        overflow-y: auto;
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
            yield Label(self.question, markup=False)
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes (y)", variant="error", id="confirm-yes")
                yield Button("No (n)", id="confirm-no")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_refuse(self) -> None:
        self.dismiss(False)


class SettingsScreen(ModalScreen[None]):
    """Modal dialog for editing user profile (Name, Email) and gate automation comments."""

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    /* Six fields and a button row come to 34 lines, which is taller than an
       80x24 terminal - and this is the screen a first run opens on, so Save was
       simply off the bottom of the screen with no way to reach it. It scrolls
       now, and stays inside the terminal it is drawn in. */
    #settings-dialog {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 90%;
        overflow-y: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #settings-title {
        text-style: bold;
        margin-bottom: 1;
    }
    .settings-label {
        margin-top: 1;
        color: $text-muted;
    }
    /* Sits under the checkbox it explains, so no margin above it. */
    .settings-hint {
        color: $text-muted;
        text-style: italic;
    }
    #input-gate-social {
        margin-top: 1;
        border: none;
        padding: 0;
        background: transparent;
    }
    #settings-buttons {
        height: auto;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Label("Gate Automation & Profile Settings", id="settings-title")
            yield Label("Your Name (for gate forms):", classes="settings-label")
            yield Input(value=self.config.user_name, id="input-name")
            yield Label("Your Email (for gate forms):", classes="settings-label")
            yield Input(value=self.config.user_email, id="input-email")
            yield Label("Random Hype Comments (separated by | or newlines):", classes="settings-label")
            comments_str = " | ".join(self.config.custom_comments)
            yield Input(value=comments_str, id="input-comments")
            yield Label("Folders to scan for music you already own (separated by |):", classes="settings-label")
            yield Input(value=" | ".join(self.config.scan_directories), id="input-scan-dirs")
            yield Label("Save downloads to:", classes="settings-label")
            yield Input(value=self.config.download_directory, id="input-download-dir")
            # Named on this screen because this screen is what a first run opens
            # on. Up to 0.8 the repost and the follow were hard-coded into the
            # gate calls and appeared in no interface at all.
            yield Checkbox(
                "Let gates record a repost, a follow and a comment on my account",
                value=self.config.gate_social_actions,
                id="input-gate-social",
            )
            yield Label(
                "Turning this off keeps your account out of it. Some gates hand "
                "over nothing without it.",
                classes="settings-hint",
            )
            yield Label("Open links with:", classes="settings-label")
            # Only what this machine reported. The saved value names a program
            # that gets executed, so the list is the whitelist.
            choices = browser_module.available_browsers()
            yield Select(
                [(label, value) for value, label in choices],
                value=browser_module.resolve_choice(self.config.browser),
                allow_blank=False,
                id="input-browser",
            )
            with Horizontal(id="settings-buttons"):
                yield Button("Save", variant="primary", id="btn-save-settings")
                yield Button("Cancel", id="btn-cancel-settings")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-settings":
            name = self.query_one("#input-name", Input).value.strip()
            email = self.query_one("#input-email", Input).value.strip()
            comments_text = self.query_one("#input-comments", Input).value
            raw_list = comments_text.split("|") if "|" in comments_text else comments_text.splitlines()
            comments = [c.strip() for c in raw_list if c.strip()]

            self.config.browser = self.query_one("#input-browser", Select).value

            if name:
                self.config.user_name = name
            if email:
                self.config.user_email = email
            if comments:
                self.config.custom_comments = comments
            scan_dirs = [d.strip() for d in self.query_one("#input-scan-dirs", Input).value.split("|") if d.strip()]
            if scan_dirs:
                self.config.scan_directories = scan_dirs
            download_dir = self.query_one("#input-download-dir", Input).value.strip()
            if download_dir:
                self.config.download_directory = download_dir
            self.config.gate_social_actions = self.query_one("#input-gate-social", Checkbox).value

            self.config.first_run = False
            self.config.save()
            self.app.notify("Settings saved!", timeout=4)
            self.dismiss()
        else:
            self.dismiss()

    def action_dismiss(self) -> None:
        self.dismiss()

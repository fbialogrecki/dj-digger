"""The modal screens: asking for a link, help, confirmation and settings."""

import re
from collections.abc import Callable
from threading import Event
from typing import TypeVar

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Input,
    Label,
    OptionList,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from .. import auth as auth_module
from .. import browser as browser_module
from ..config import AppConfig, is_real_email
from .keymap import (
    CRATES,
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

ResultType = TypeVar("ResultType")


class _Modal(ModalScreen[ResultType]):
    """Shared shell for the dialogs: centred, with the common box styling.

    DEFAULT_CSS rather than CSS, and the selector is literally ``_Modal``, on
    purpose: an inherited ``CSS`` attribute is registered once per subclass
    with the subclass name as a *descendant* scope, so it silently stops
    matching the screen itself and every dialog renders top-left. DEFAULT_CSS
    is collected across the MRO and a type selector matches subclasses.
    """

    DEFAULT_CSS = """
    _Modal {
        align: center middle;
    }
    _Modal .modal-box {
        max-width: 90%;
        height: auto;
        padding: 1 2;
        background: $surface;
    }
    """

    def action_cancel(self) -> None:
        self.dismiss(None)


class AskLinkScreen(_Modal[str | None]):
    """Asks for a SoundCloud link (or a saved HTML file)."""

    CSS = """
    #ask {
        width: 78;
        border: round $accent;
    }
    #ask-hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="ask", classes="modal-box"):
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


class HelpScreen(_Modal[None]):
    """Every key, grouped by what it acts on."""

    CSS = """
    /* 64, not 56: the widest line this builds is 60 columns, and at 56 every
       description longer than the box wrapped back to column 0, leaving
       "store" and "matches" hanging underneath as if they were keys. Width auto
       does not work here - a Static holding a Text does not report a width - so
       it is measured against _body() instead, and max-width keeps it inside a
       small terminal, where it wraps again but has nowhere else to go. */
    #help {
        width: 66;
        max-height: 90%;
        overflow-y: auto;
        border: round $accent;
    }
    """

    BINDINGS = [Binding("escape,question_mark,q", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help", classes="modal-box"):
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
            ]
            if section == WHOLE_LIST:
                entries.append(("1-9", "Show only the nth store"))
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


class ConfirmScreen(_Modal[bool]):
    """Yes/no, for the one action here that cannot be undone."""

    CSS = """
    #confirm {
        width: 62;
        max-height: 90%;
        overflow-y: auto;
        border: round $error;
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
        with Vertical(id="confirm", classes="modal-box"):
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


class ContextMenuScreen(_Modal[str | None]):
    """Actions for the row selected with the right mouse button."""

    CSS = """
    #context-menu { width: 58; max-height: 90%; border: round $accent; }
    #context-menu-title { margin-bottom: 1; }
    #context-menu-options { height: auto; max-height: 12; }
    """

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(self, title: str, options: tuple[tuple[str, str], ...]) -> None:
        super().__init__()
        self.title = title
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="context-menu", classes="modal-box"):
            yield Label(self.title, id="context-menu-title")
            yield OptionList(
                *(Option(label, id=action) for action, label in self.options),
                id="context-menu-options",
            )

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.id))


class GateProfileScreen(_Modal[bool]):
    """Ask only for the identity a download gate is about to submit."""

    CSS = """
    #gate-profile { width: 68; border: round $warning; }
    #gate-profile-hint, #gate-profile-error { color: $text-muted; margin-bottom: 1; }
    #gate-profile-error { color: $error; }
    #gate-profile-buttons { height: auto; margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="gate-profile", classes="modal-box"):
            yield Label("This download gate requires your profile", id="gate-profile-title")
            yield Label(
                "Your name and email will be sent to Hypeddit or GateRush and may "
                "also be shared with the track's author.",
                id="gate-profile-hint",
            )
            yield Label("Name")
            yield Input(value=self.config.user_name, id="gate-profile-name")
            yield Label("Email")
            yield Input(value="" if not self.config.has_real_email() else self.config.user_email,
                        id="gate-profile-email")
            yield Label("", id="gate-profile-error")
            with Horizontal(id="gate-profile-buttons"):
                yield Button("Save and continue", variant="primary", id="gate-profile-save")
                yield Button("Cancel", id="gate-profile-cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#gate-profile-email", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "gate-profile-save":
            self.dismiss(False)
            return
        name = self.query_one("#gate-profile-name", Input).value.strip()
        email = self.query_one("#gate-profile-email", Input).value.strip()
        if not name:
            self.query_one("#gate-profile-error", Label).update("Enter your name.")
            return
        if not is_real_email(email):
            self.query_one("#gate-profile-error", Label).update(
                "Enter a valid email address without spaces."
            )
            return
        self.config.user_name = name
        self.config.user_email = email
        self.config.save()
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class SoundCloudAuthScreen(_Modal[str | None]):
    """Verify SoundCloud through app-managed Chromium or a hidden token field."""

    CSS = """
    #soundcloud-auth { width: 72; border: round $accent; }
    #soundcloud-auth-hint, #soundcloud-auth-status { color: $text-muted; margin-bottom: 1; }
    #soundcloud-auth-buttons { height: auto; margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, client_id: Callable[[], str]) -> None:
        super().__init__()
        self.client_id = client_id
        self.cancel = Event()

    def compose(self) -> ComposeResult:
        with Vertical(id="soundcloud-auth", classes="modal-box"):
            yield Label("SoundCloud login required")
            yield Label(
                "Chromium uses a private dj-digger profile. Only the oauth_token "
                "cookie is verified and copied; your password is never stored.",
                id="soundcloud-auth-hint",
            )
            yield Label("Ready", id="soundcloud-auth-status")
            yield Input(placeholder="Paste oauth_token", password=True, id="soundcloud-token")
            with Horizontal(id="soundcloud-auth-buttons"):
                yield Button("Open Chromium", variant="primary", id="soundcloud-browser")
                yield Button("Paste token", id="soundcloud-paste")
                yield Button("Cancel", id="soundcloud-cancel")
        yield Footer()

    def _set_status(self, message: str) -> None:
        self.query_one("#soundcloud-auth-status", Label).update(message)

    def _set_busy(self, busy: bool) -> None:
        self.query_one("#soundcloud-browser", Button).disabled = busy
        self.query_one("#soundcloud-paste", Button).disabled = busy

    def _show_auth_error(self, message: str) -> None:
        self._set_status(message)
        self._set_busy(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "soundcloud-browser":
            self._set_busy(True)
            self._authenticate(
                lambda: auth_module.login_with_chromium(
                    self.client_id(),
                    cancel=self.cancel,
                    status=lambda message: self.app.call_from_thread(
                        self._set_status, message
                    ),
                )
            )
        elif event.button.id == "soundcloud-paste":
            token = self.query_one("#soundcloud-token", Input).value.strip()
            if not token:
                self._set_status("Paste a token first; its value stays hidden.")
            else:
                self._set_busy(True)
                self._set_status("Verifying the SoundCloud token…")
                self._authenticate(
                    lambda: auth_module.verify_and_save(token, self.client_id())
                )
        else:
            self.action_cancel()

    @work(thread=True, exclusive=True, group="soundcloud-auth")
    def _authenticate(self, action: Callable[[], tuple[str, str, int | None]]) -> None:
        try:
            token, _username, _user_id = action()
        except auth_module.SoundCloudAuthCancelled:
            return
        except auth_module.SoundCloudAuthError as exc:
            if not self.cancel.is_set():
                self.app.call_from_thread(self._show_auth_error, str(exc))
            return
        if not self.cancel.is_set():
            self.app.call_from_thread(self.dismiss, token)

    def action_cancel(self) -> None:
        self.cancel.set()
        self.dismiss(None)

    def on_unmount(self) -> None:
        self.cancel.set()


class SettingsScreen(_Modal[None]):
    """Modal dialog for editing user profile (Name, Email) and gate automation comments."""

    CSS = """
    /* Six fields and a button row come to 34 lines, which is taller than an
       80x24 terminal - and this is the screen a first run opens on, so Save was
       simply off the bottom of the screen with no way to reach it. It scrolls
       now, and stays inside the terminal it is drawn in. */
    #settings-dialog {
        width: 72;
        max-height: 90%;
        overflow-y: auto;
        border: round $accent;
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
        with Vertical(id="settings-dialog", classes="modal-box"):
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
            self.config.browser = self.query_one("#input-browser", Select).value

            # Blank fields keep their previous value.
            for widget_id, attribute in (
                ("#input-name", "user_name"),
                ("#input-email", "user_email"),
                ("#input-download-dir", "download_directory"),
            ):
                value = self.query_one(widget_id, Input).value.strip()
                if value:
                    setattr(self.config, attribute, value)
            comments_text = self.query_one("#input-comments", Input).value
            comments = [c.strip() for c in re.split(r"[|\n]", comments_text) if c.strip()]
            if comments:
                self.config.custom_comments = comments
            scan_dirs = [d.strip() for d in self.query_one("#input-scan-dirs", Input).value.split("|") if d.strip()]
            if scan_dirs:
                self.config.scan_directories = scan_dirs
            self.config.gate_social_actions = self.query_one("#input-gate-social", Checkbox).value

            self.config.first_run = False
            self.config.save()
            self.app.notify("Settings saved!", timeout=4)
            self.dismiss()
        else:
            self.dismiss()

    def action_dismiss(self) -> None:
        self.dismiss()

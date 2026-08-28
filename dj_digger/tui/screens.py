"""The modal screens: asking for a link, help, confirmation and settings."""

import re
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, InvalidOperation
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
    DataTable,
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
from .. import cart as cart_module
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


class CartProgressScreen(_Modal[None]):
    """One live cart status instead of a stream of expiring notifications."""

    CSS = """
    #cart-progress { width: 68; border: round $accent; }
    #cart-progress-detail { color: $text-muted; margin-top: 1; }
    #cart-progress-buttons { height: auto; margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, cancel) -> None:
        super().__init__()
        self.cancel_event = cancel
        self.progress = cart_module.CartProgress("starting", 0, 0)

    def compose(self) -> ComposeResult:
        with Vertical(id="cart-progress", classes="modal-box"):
            yield Label("Preparing purchases", id="cart-progress-title")
            yield Static("Starting Chromium…", id="cart-progress-detail")
            with Horizontal(id="cart-progress-buttons"):
                yield Button("Cancel", id="cart-progress-cancel")
        yield Footer()

    def update_progress(self, progress: cart_module.CartProgress) -> None:
        self.progress = progress
        if not self.is_mounted:
            return
        phase = {
            "starting": "Starting Chromium",
            "login": "Waiting for store login",
            "preflight": "Checking products — nothing is added until review",
            "approval": "Ready for review",
            "adding": "Adding to Bandcamp",
            "ready": "Results ready",
        }[progress.phase]
        count = f" {progress.completed}/{progress.total}" if progress.total else ""
        self.query_one("#cart-progress-title", Label).update(phase + count)
        detail = progress.message or progress.track_label or progress.store.capitalize()
        self.query_one("#cart-progress-detail", Static).update(detail)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cart-progress-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.cancel_event.set()
        self.query_one("#cart-progress-detail", Static).update("Stopping safely…")


class CartPlanScreen(_Modal[cart_module.CartPlan | None]):
    """Select exact products and edit only prices the seller allows to vary."""

    CSS = """
    #cart-plan { width: 92; height: 90%; max-height: 32; border: round $warning; }
    #cart-plan-help { color: $text-muted; height: 1; }
    #cart-plan-table { height: 1fr; min-height: 3; }
    #cart-plan-price { margin-top: 1; }
    #cart-plan-error { color: $error; height: auto; }
    #cart-plan-total { color: $text-muted; height: auto; }
    #cart-plan-buttons { height: auto; margin-top: 1; }
    """
    BINDINGS = [
        Binding("space", "toggle_item", "Use / skip"),
        Binding("e", "edit_price", "Edit price"),
        Binding("y", "approve", "Continue selected"),
        Binding("enter", "approve", "Continue selected", show=False, priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, plan: cart_module.CartPlan) -> None:
        super().__init__()
        self.plan = plan
        self.items = list(plan.items)
        self.selected = set(range(len(self.items)))
        self._price_index: int | None = None
        self._row_keys = []
        self._column_keys = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="cart-plan", classes="modal-box"):
            yield Label("Review purchase plan")
            yield Static(
                "Space selects · E edits highlighted price · Y/Enter continues · Esc cancels",
                id="cart-plan-help",
            )
            yield DataTable(cursor_type="row", zebra_stripes=True, id="cart-plan-table")
            yield Input(placeholder="Selected Bandcamp price", id="cart-plan-price")
            yield Static("", id="cart-plan-error")
            yield Static("", id="cart-plan-total")
            with Horizontal(id="cart-plan-buttons"):
                yield Button("Continue selected (y)", variant="primary", id="cart-plan-add")
                yield Button("Cancel", id="cart-plan-cancel")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#cart-plan-table", DataTable)
        labels = ("Use", "Track", "Store", "Price", "State")
        self._column_keys = dict(zip(labels, table.add_columns(*labels), strict=True))
        for index, item in enumerate(self.items):
            if item.already_in_cart:
                state = "already in cart"
            elif item.price_editable:
                state = f"minimum {item.currency} {(item.minimum_price or item.price):.2f} · editable"
            elif item.store == "beatport":
                state = "playlist"
            else:
                state = "ready"
            self._row_keys.append(
                table.add_row(
                    "✓",
                    item.track_label,
                    item.store,
                    f"{item.currency} {item.price:.2f}",
                    state,
                    key=str(index),
                )
            )
        self._select_price_row(0 if self.items else None)
        self._refresh_total()

    def _cursor_index(self) -> int | None:
        table = self.query_one("#cart-plan-table", DataTable)
        return table.cursor_row if 0 <= table.cursor_row < len(self.items) else None

    def _select_price_row(self, index: int | None) -> None:
        self._price_index = index
        field = self.query_one("#cart-plan-price", Input)
        if index is None or not self.items[index].price_editable:
            field.value = ""
            field.disabled = True
            field.placeholder = "The highlighted item has a fixed price"
            return
        field.disabled = False
        field.placeholder = "Bandcamp price (comma or dot)"
        field.value = format(self.items[index].price, "f")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._apply_visible_price(quiet=True)
        self._select_price_row(event.cursor_row)

    def _parse_price(self, item: cart_module.CartItem, raw: str) -> Decimal:
        try:
            value = Decimal(raw.strip().replace(",", "."))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Enter a valid decimal price") from exc
        minimum = item.minimum_price or Decimal(0)
        if not value.is_finite() or value <= 0 or value < minimum:
            raise ValueError(f"Price must be at least {item.currency} {minimum:.2f}")
        if item.price_step and (value - minimum) % item.price_step:
            raise ValueError(f"Price must follow a {item.price_step} step")
        return value

    def _apply_visible_price(self, *, quiet: bool = False) -> bool:
        index = self._price_index
        if index is None or not self.items[index].price_editable:
            return True
        field = self.query_one("#cart-plan-price", Input)
        try:
            price = self._parse_price(self.items[index], field.value)
        except ValueError as exc:
            if not quiet:
                self.query_one("#cart-plan-error", Static).update(str(exc))
            return False
        self.items[index] = replace(self.items[index], price=price)
        self.query_one("#cart-plan-error", Static).update("")
        self._refresh_row(index)
        self._refresh_total()
        return True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "cart-plan-price":
            event.stop()
            self._apply_visible_price()

    def _refresh_row(self, index: int) -> None:
        table = self.query_one("#cart-plan-table", DataTable)
        item = self.items[index]
        table.update_cell(
            self._row_keys[index],
            self._column_keys["Use"],
            "✓" if index in self.selected else "",
        )
        table.update_cell(
            self._row_keys[index],
            self._column_keys["Price"],
            f"{item.currency} {item.price:.2f}",
        )

    def _refresh_total(self) -> None:
        totals: dict[str, Decimal] = {}
        for index in self.selected:
            item = self.items[index]
            if item.already_in_cart:
                continue
            totals[item.currency] = totals.get(item.currency, Decimal(0)) + item.price
        text = " · ".join(f"{currency} {amount:.2f}" for currency, amount in sorted(totals.items()))
        self.query_one("#cart-plan-total", Static).update(
            f"Selected {len(self.selected)}/{len(self.items)}" + (f" · {text}" if text else "")
        )

    def action_toggle_item(self) -> None:
        index = self._cursor_index()
        if index is None:
            return
        if index in self.selected:
            self.selected.remove(index)
        else:
            self.selected.add(index)
        self._refresh_row(index)
        self._refresh_total()

    def action_edit_price(self) -> None:
        index = self._cursor_index()
        if index is None:
            return
        self._select_price_row(index)
        field = self.query_one("#cart-plan-price", Input)
        if not field.disabled:
            field.focus()

    def action_approve(self) -> None:
        if not self._apply_visible_price() or not self.selected:
            if not self.selected:
                self.query_one("#cart-plan-error", Static).update("Select at least one item")
            return
        deselected = [self.items[index] for index in range(len(self.items)) if index not in self.selected]
        results = list(self.plan.results)
        results.extend(
            cart_module.CartResult(
                item.track_key,
                item.track_label,
                item.store,
                "skipped",
                "not selected",
                "not_selected",
            )
            for item in deselected
        )
        self.dismiss(
            cart_module.CartPlan(
                tuple(self.items[index] for index in sorted(self.selected)),
                tuple(results),
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cart-plan-add":
            self.action_approve()
        elif event.button.id == "cart-plan-cancel":
            self.action_cancel()


class CartResultScreen(_Modal[str | None]):
    """Compact batch result with safe retry and cart-focus actions."""

    CSS = """
    #cart-result { width: 86; max-height: 90%; border: round $accent; }
    #cart-result-table { height: 18; }
    #cart-result-buttons { height: auto; margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(self, outcome: cart_module.CartBatchOutcome) -> None:
        super().__init__()
        self.outcome = outcome

    def compose(self) -> ComposeResult:
        with Vertical(id="cart-result", classes="modal-box"):
            yield Label("Store purchase results")
            yield DataTable(cursor_type="row", zebra_stripes=True, id="cart-result-table")
            with Horizontal(id="cart-result-buttons"):
                if self.outcome.retryable_keys:
                    yield Button("Retry safe failures", variant="primary", id="cart-result-retry")
                if self.outcome.cart_stores:
                    yield Button("Show carts", id="cart-result-focus")
                if self.outcome.beatport_playlist_ready:
                    yield Button(
                        "Prepare Beatport playlist (Soundiiz)",
                        id="cart-result-playlist",
                    )
                yield Button("Close", id="cart-result-close")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#cart-result-table", DataTable)
        table.add_columns("Track", "Store", "Result", "Reason")
        for result in self.outcome.results:
            table.add_row(result.track_label, result.store, result.status, result.reason)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "cart-result-retry": "retry",
            "cart-result-focus": "focus",
            "cart-result-playlist": "playlist",
            "cart-result-close": None,
        }
        self.dismiss(actions[event.button.id])


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
    #settings-store-buttons, #settings-buttons {
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
            yield Label("Bandcamp browser session:", classes="settings-label")
            with Horizontal(id="settings-store-buttons"):
                yield Button("Open Bandcamp session", id="btn-store-logins")
                yield Button("Check Bandcamp", id="btn-store-check")
                yield Button("Reset store profile", variant="warning", id="btn-store-reset")
            with Horizontal(id="settings-buttons"):
                yield Button("Save", variant="primary", id="btn-save-settings")
                yield Button("Cancel", id="btn-cancel-settings")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in {
            "btn-store-logins",
            "btn-store-check",
            "btn-store-reset",
        }:
            action = {
                "btn-store-logins": self.app.action_setup_store_logins,
                "btn-store-check": self.app.action_check_store_logins,
                "btn-store-reset": self.app.action_reset_store_profile,
            }[event.button.id]
            self.dismiss()
            action()
            return
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

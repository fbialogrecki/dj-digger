"""Readable local diagnostics; disk and desktop handoff run in workers."""
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Static

from ..diagnostics import log_safe_text, redact_text
from ..logging_setup import current_log_error, current_log_path, open_log_folder, read_log_tail
from .screens import _Modal


class LogsScreen(_Modal):
    BINDINGS = [Binding('escape', 'cancel', 'Close')]
    DEFAULT_CSS = '''LogsScreen .modal-box { width: 110; height: 90%; }
    LogsScreen Horizontal { height: auto; } LogsScreen #log-path { height: auto; }'''

    def compose(self):
        with Vertical(classes='modal-box'):
            yield Static('Local diagnostic logs — no automatic uploads')
            yield Static('', id='log-path', markup=False)
            with VerticalScroll():
                yield Static('Loading…', id='log-text', markup=False)
            with Horizontal():
                yield Button('Refresh', id='logs-refresh')
                yield Button('Open logs folder', id='logs-folder')
                yield Button('Close', id='logs-close')
        yield Footer()

    async def on_mount(self):
        await self.load()

    async def load(self):
        self.path = current_log_path()
        self.query_one('#logs-folder', Button).disabled = self.path is None
        label = str(self.path or 'File logging is unavailable in this session')
        if error := current_log_error():
            label += '\nLog writing failed: ' + error
        self.query_one('#log-path', Static).update(label)
        try:
            content = await self.app.services.io(read_log_tail, self.path) if self.path else 'No active log file.'
        except OSError as exc:
            content = log_safe_text(exc)
        self.query_one('#log-text', Static).update(redact_text(content) or 'No entries yet.')

    async def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'logs-close':
            self.dismiss()
        elif event.button.id == 'logs-refresh':
            await self.load()
        elif event.button.id == 'logs-folder' and self.path:
            try:
                await self.app.services.io(open_log_folder, self.path)
            except Exception as exc:
                self.app.notify(f'Could not open the logs folder: {log_safe_text(exc)}', severity='error')

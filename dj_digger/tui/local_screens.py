"""Local library dialogs with explicit, reviewable export choices."""
from pathlib import Path

from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Input, Label, Select, Static

from ..analysis import NOTES, camelot
from ..decks import DECK_GROUPS, DEFAULT_DECKS, best_profile
from .screens import _Modal


class TextPrompt(_Modal):
    DEFAULT_CSS = "TextPrompt .modal-box { width: 75; }"
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, title, value=''):
        super().__init__()
        self.prompt, self.value = title, value

    def compose(self):
        with Vertical(classes='modal-box'):
            yield Label(self.prompt)
            yield Input(value=self.value, id='value')
            yield Button('Continue', id='accept')
        yield Footer()

    def on_input_submitted(self, event):
        event.stop()
        self.accept()

    def on_button_pressed(self, event):
        event.stop()
        self.accept()

    def accept(self):
        value = self.query_one('#value', Input).value.strip()
        if value:
            self.dismiss(value)


class ExportOptions(_Modal):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]
    DEFAULT_CSS = ('ExportOptions .modal-box { width: 100; max-width: 100%; height: 90%; } ExportOptions VerticalScroll { height: 1fr; } ExportOptions Horizontal { height: 3; } ExportOptions Label { width: 1fr; height: auto; } '
                   'ExportOptions #decks { grid-size: 2; grid-gutter: 0 1; height: auto; } ExportOptions #decks Checkbox { width: 1fr; }')

    def __init__(self, folder, decks=DEFAULT_DECKS):
        super().__init__()
        self.folder = str(folder)
        self.decks = tuple(decks)

    def compose(self):
        with Vertical(classes='modal-box'):
            yield Label('Convert / prepare music folder')
            with VerticalScroll():
                yield Label('Selected files are used. Without selection in a folder, matching files across all pages are included.')
                yield Label('Decks these files must play on (the format follows from them)')
                # Decks that play the same files share one box, two to a row.
                with Grid(id='decks'):
                    for index, group in enumerate(DECK_GROUPS):
                        yield Checkbox(', '.join(group), value=bool(set(group) & set(self.decks)), id=f'deck-{index}')
                yield Label('', id='profile')
                yield Label('Destination folder (or mounted USB)')
                yield Input(value=self.folder, placeholder='Destination parent folder / mounted USB', id='folder')
                yield Checkbox('Replace originals (no permanent backup)', id='replace')
                yield Checkbox('Include subfolders of the open directory', id='recursive')
                yield Label('Default: a new folder with COPIES of every selected audio file, including unchanged files. '
                            'Files every chosen deck already plays keep their format and quality.')
            yield Label('', id='replace-warning')
            with Horizontal():
                yield Button('Review plan', id='plan', variant='primary')
                yield Button('Cancel', id='cancel')
        yield Footer()

    def chosen(self):
        return [name for index, group in enumerate(DECK_GROUPS) if self.query_one(f'#deck-{index}', Checkbox).value
                for name in group]

    def profile(self):
        return best_profile(self.chosen())

    def on_checkbox_changed(self, event):
        if event.checkbox.id == 'replace':
            self.query_one('#replace-warning', Label).update(
                'Replacement permanently removes originals after verification.' if event.value else '')
        else:
            self.update_profile()

    def on_mount(self):
        self.update_profile()

    def update_profile(self):
        chosen = self.chosen()
        self.query_one('#profile', Label).update(
            f'Files that need converting become {self.profile().label()}.' if chosen else 'Choose at least one deck.')
        self.query_one('#plan', Button).disabled = not chosen

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'cancel':
            self.dismiss(None)
            return
        if event.button.id != 'plan' or not self.chosen():
            return
        self.dismiss(dict(decks=self.chosen(), folder=Path(self.query_one('#folder', Input).value).expanduser(),
                          mode='replace' if self.query_one('#replace', Checkbox).value else 'copy',
                          recursive=self.query_one('#recursive', Checkbox).value))


class ExportReview(_Modal):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]
    DEFAULT_CSS = 'ExportReview .modal-box { width: 95; max-width: 100%; height: 90%; } ExportReview VerticalScroll { height: 1fr; } ExportReview Horizontal { height: 3; } ExportReview Label { height: auto; } ExportReview .export-note { color: $warning; }'

    def __init__(self, plan):
        super().__init__()
        self.plan = plan

    def compose(self):
        with Vertical(classes='modal-box'):
            yield Label(f'{len(self.plan.items)} audio files · {self.plan.mode}')
            with VerticalScroll():
                for note in self.plan.notes:
                    yield Static(f'Warning: {note}', classes='export-note', markup=False)
                yield Static(f'Files that need converting become {self.plan.profile.label()}.\n'
                             'Actual planned set on the chosen decks, according to documentation:\n'
                             + '\n'.join(f'{deck}: {state}' for deck, state in self.plan.compatibility().items()), markup=False)
                yield Static('\n'.join(f'{item.action}: {Path(item.source).name} → {Path(item.destination).name}' + (f' — {item.reason}' if item.reason else '') for item in self.plan.items[:200]), markup=False)
                if len(self.plan.items) > 200:
                    yield Label('First 200 shown; the saved report includes the complete plan.')
                yield Label('Exceptions remain unexported and are listed in the report.')
            if self.plan.mode == 'replace':
                yield Label('Replacement permanently removes originals. Close playback first.')
            with Horizontal():
                yield Button('Execute this plan', id='execute', variant='warning' if self.plan.mode == 'replace' else 'primary')
                yield Button('Cancel', id='cancel')
        yield Footer()

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'execute':
            self.dismiss(True)
        elif event.button.id == 'cancel':
            self.dismiss(None)


class AnalysisEdit(_Modal):
    DEFAULT_CSS = "AnalysisEdit .modal-box { width: 75; max-height: 95%; overflow-y: auto; } AnalysisEdit Horizontal { height: 3; }"
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, track, resolved=None):
        super().__init__()
        self.track = track
        self.resolved = resolved or {}

    def compose(self):
        with Vertical(classes='modal-box'):
            yield Label('Manual BPM / key — automatic values are estimates')
            yield Label('BPM source: ' + self.resolved.get('bpm', (None, 'Not available'))[1], id='bpm-source')
            yield Label('Key source: ' + self.resolved.get('key', (None, 'Not available'))[1], id='key-source')
            yield Input(value=self.track.bpm_label, placeholder='BPM (empty = use analysis/tags)', id='bpm')
            with Horizontal():
                yield Button('÷2', id='half')
                yield Button('×2', id='double')
            keys = [('', '')] + [(f'{note}{suffix} / {camelot(note + suffix)}', note + suffix) for note in NOTES for suffix in ('', 'm')]
            yield Select(keys, value=self.track.key_signature if self.track.key_signature in [value for _, value in keys] else '', allow_blank=False, id='key')
            yield Button('Save manual values', id='save')
            yield Button('Clear manual overrides', id='clear')
        yield Footer()

    def on_button_pressed(self, event):
        event.stop()
        field = self.query_one('#bpm', Input)
        try:
            bpm = float(field.value) if field.value.strip() else None
            if bpm is not None and not 0 < bpm <= 999:
                raise ValueError
        except ValueError:
            self.notify('BPM must be between 0 and 999', severity='error')
            return
        if event.button.id in ('half', 'double'):
            if bpm:
                field.value = f'{bpm * (.5 if event.button.id == "half" else 2):g}'
        else:
            values = {}
            if event.button.id != 'clear':
                if bpm:
                    values['bpm'] = bpm
                key = self.query_one('#key', Select).value
                if key:
                    values['key'] = key
            self.dismiss(values)


class ProfileImportOptions(_Modal):
    DEFAULT_CSS = "ProfileImportOptions .modal-box { width: 75; }"
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def compose(self):
        with Vertical(classes='modal-box'):
            yield Label('Import playlists created by a SoundCloud profile')
            yield Input(placeholder='https://soundcloud.com/profile', id='profile')
            yield Checkbox('Include private playlists (requires owner login)', id='private')
            yield Button('Import playlists', id='import')
        yield Footer()

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss((self.query_one('#profile', Input).value.strip(), self.query_one('#private', Checkbox).value))

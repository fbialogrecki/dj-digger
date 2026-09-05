"""Collection controller; workers use a service and immutable operation settings."""

from copy import deepcopy

from ..models import Cancelled
from ..services.operations import OperationBusy

DIG_JOB = 'Digging'


class DiggingController:
    def __init__(self, service, operations, *, run, dispatch, prompt, notify,
                 has_rows, view_generation, options, export_settings, display, changed, exit_empty):
        self.service = service
        self.operations = operations
        self.run = run
        self.dispatch = dispatch
        self.prompt = prompt
        self.notify = notify
        self.has_rows = has_rows
        self.view_generation = view_generation
        self.options = options
        self.export_settings = export_settings
        self.display = display
        self.changed = changed
        self.exit_empty = exit_empty

    def ask(self):
        if self.operations.active() is not None:
            self.notify('Another operation is still running; ctrl+x stops it', timeout=3)
            return
        self.prompt('Paste a SoundCloud link' if self.has_rows() else 'What are we digging?', self.entered)

    def entered(self, target):
        if target:
            self.start(target)
        elif not self.has_rows():
            self.exit_empty()

    def start(self, target):
        try:
            handle = self.operations.start(DIG_JOB, detail=f'Digging {target}')
        except OperationBusy as exc:
            self.notify(str(exc), timeout=3)
            return
        generation = self.service.db.snapshot_generations()
        view_generation = self.view_generation()
        options = deepcopy(self.options())
        export_format, export_path = self.export_settings()
        self.changed()
        return self.run(lambda: self._collect(
            target, handle, generation, view_generation, options, export_format, export_path,
        ))

    def _collect(self, target, handle, generation, view_generation, options, export_format, export_path):
        def progress(stage, done, total):
            suffix = f' {done}/{total}' if total else ''
            if self.operations.progress(handle, detail=f'{stage}{suffix}'):
                self._send(self.changed)
        failure = None
        try:
            result = self.service.collect(
                target, options, generation, export_format, export_path, handle.cancel, progress,
            )
            self._send(lambda: self.display(result, view_generation))
        except Cancelled:
            failure = "Dig stopped"
        except Exception as exc:
            failure = str(exc)
        finally:
            self.operations.finish(handle)
            self._send(self.changed)
            if failure is not None:
                self._send(lambda: self._failed(failure))

    def _failed(self, message):
        self.notify(message, severity="error", timeout=8)
        if not self.has_rows():
            self.ask()

    def _send(self, callback):
        try:
            self.dispatch(callback)
        except RuntimeError:
            pass

"""Presentation callbacks for the operation coordinator."""

from ..services.operations import OperationCoordinator


class JobController:
    def __init__(self, operations: OperationCoordinator, *, changed, wake, sleep, playing, notify):
        self.operations = operations
        self.changed = changed
        self.wake = wake
        self.sleep = sleep
        self.playing = playing
        self.notify = notify

    def start(self, name, total=None, *, cancel=None, detail='', animate=True):
        handle = self.operations.start(
            name, total, cancel=cancel, detail=detail, animate=animate,
            lane='scan' if name == 'Scanning' else 'main',
        )
        if animate:
            self.wake()
        self.changed()
        return handle

    def progress(self, handle, done=0, *, failed=0, detail=None):
        if handle is not None and self.operations.progress(handle, done, failed=failed, detail=detail):
            self.changed()

    def finish(self, handle):
        if handle is not None and self.operations.finish(handle):
            self.changed()
            if not self.playing() and self.operations.visible is None:
                self.sleep()

    def cancel(self):
        handle = self.operations.visible
        if handle is None:
            self.notify('Nothing to stop', timeout=2)
            return
        self.operations.cancel(handle)
        self.changed()
        self.notify(f'Stopping {handle.name.lower()}; what finished is kept', timeout=4)

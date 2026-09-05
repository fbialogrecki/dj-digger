"""Operation admission and settlement; execution remains with the caller."""

from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Literal, Protocol
from uuid import uuid4


class Cancellation(Protocol):
    def set(self) -> None: ...
    def is_set(self) -> bool: ...


class OperationBusy(RuntimeError):
    pass


@dataclass(eq=False)
class OperationHandle:
    name: str
    total: int | None = None
    cancel: Cancellation = field(default_factory=Event)
    detail: str = ''
    animate: bool = True
    done: int = 0
    failed: int = 0
    id: str = field(default_factory=lambda: uuid4().hex)
    lane: Literal['main', 'scan'] = 'main'
    state: Literal['running', 'cancelling', 'finished'] = 'running'

    def describe(self) -> str:
        parts = [self.name]
        if self.total:
            parts.append(f'{self.done}/{self.total}')
        elif self.done:
            parts.append(str(self.done))
        if self.failed:
            parts.append(f'{self.failed} failed')
        if self.detail:
            parts.append(self.detail)
        parts.append('stopping' if self.state == 'cancelling' else '^X stop')
        return ' · '.join(parts)


class OperationCoordinator:
    """Two independent slots; cancellation requests never release a slot."""

    def __init__(self):
        self._lock = RLock()
        self._slots: dict[str, OperationHandle] = {}
        self._accepting = True

    @property
    def visible(self) -> OperationHandle | None:
        with self._lock:
            return self._slots.get('main') or self._slots.get('scan')

    def active(self, lane='main'):
        with self._lock:
            return self._slots.get(lane)

    def start(self, name, total=None, *, lane='main', cancel=None, detail='', animate=True):
        with self._lock:
            if not self._accepting:
                raise OperationBusy('Application is closing')
            if lane in self._slots:
                raise OperationBusy(f'{self._slots[lane].name} is still running')
            handle = OperationHandle(name, total, cancel or Event(), detail, animate, lane=lane)
            self._slots[lane] = handle
            return handle

    def current(self, handle: OperationHandle) -> bool:
        with self._lock:
            return self._slots.get(handle.lane) is handle

    def progress(self, handle, done=0, *, failed=0, detail=None):
        with self._lock:
            if not self.current(handle):
                return False
            handle.done += done
            handle.failed += failed
            if detail is not None:
                handle.detail = detail
            return True

    def cancel(self, handle):
        with self._lock:
            if self.current(handle):
                handle.state = 'cancelling'
                handle.cancel.set()

    def finish(self, handle):
        with self._lock:
            if not self.current(handle):
                return False
            handle.state = 'finished'
            del self._slots[handle.lane]
            return True

    def stop_accepting(self):
        with self._lock:
            self._accepting = False
            for handle in self._slots.values():
                self.cancel(handle)

"""Presentation state owned by the playlist, audio, sidebar and operation panels."""

import asyncio
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import TYPE_CHECKING

from textual.timer import Timer
from textual.widgets.data_table import ColumnKey

from ..crate_models import CrateHeader, CrateRecord
from ..services.operations import OperationHandle
from ..services.playback import Prepared
from .rows import Row

if TYPE_CHECKING:
    from .downloads import DownloadContext
    from .screens import CartProgressScreen


@dataclass
class PlaylistState:
    rows: list[Row] = field(default_factory=list)
    crate: CrateRecord | None = None
    crate_title: str = ""
    store_filters: set[str] = field(default_factory=set)
    search_term: str = ""
    hide_handled: bool = False
    selected: set[str] = field(default_factory=set)
    _anchor: int | None = None
    sort_key: str | None = None
    sort_reverse: bool = False
    _column_keys: dict[str, ColumnKey] = field(default_factory=dict)
    visible_rows: list[Row] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    _badge_click_regions: list[tuple[int, int, int]] = field(default_factory=list)
    _undone: list[str] = field(default_factory=list)
    _view_generation: int = 0


@dataclass
class AudioState:
    _ticker: Timer | None = None
    _cursor_follows: bool = True
    _prepared: Prepared | None = None
    _preparing: str = ""
    _frame: int = 0
    _ticker: Timer | None = None
    _playback_generation: int = 0
    _preparation_generation: int = 0


@dataclass
class DownloadState:
    _download_handle: OperationHandle | None = None
    download_progress: dict[str, float] = field(default_factory=dict)
    _dirty_download_rows: set[str] = field(default_factory=set)
    _last_progress_redraw: float = 0.0
    _gate_cancel: Event = field(default_factory=Event)
    _browser_batch_active: bool = False
    _download_context: "DownloadContext | None" = None
    _progress_lock: Lock = field(default_factory=Lock)
    _pending_progress: dict[str, tuple[str, float]] = field(default_factory=dict)


@dataclass
class CartState:
    _cart_context: tuple | None = None
    _cart_busy: bool = False
    _cart_cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _cart_handle: OperationHandle | None = None
    _cart_progress_screen: "CartProgressScreen | None" = None
    _pending_open: str | None = None


@dataclass
class SidebarState:
    _load_generation: int = 0
    crates: list[CrateHeader] = field(default_factory=list)


@dataclass
class ScanState:
    _scan_cancel: Event = field(default_factory=Event)

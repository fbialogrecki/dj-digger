"""Track Table DataTable widget with multi-selection, mouse context menu, and badges."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple
from textual.events import Click, MouseUp
from textual.widgets import DataTable
from textual.widgets.data_table import CellDoesNotExist

from ..models import Track
from ..state import GOT, NEW, OPENED, SKIP

LOGGER = logging.getLogger(__name__)

STATUS_BADGES = {
    NEW: "·",
    OPENED: "○",
    GOT: "✓",
    SKIP: "✗",
}


from textual.message import Message


class TrackTable(DataTable):
    """DataTable widget for tracks with multi-selection and context menu support."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self.selected_row_keys: Set[str] = set()
        self._row_track_map: Dict[str, Track] = {}

    def on_mount(self) -> None:
        self.add_columns("▶", "mark", "#", "Track", "Stores", "Genre", "Time")

    def toggle_selection(self, row_key: str) -> None:
        if row_key in self.selected_row_keys:
            self.selected_row_keys.remove(row_key)
        else:
            self.selected_row_keys.add(row_key)

    def get_selected_or_current_track_keys(self) -> List[str]:
        if self.selected_row_keys:
            return list(self.selected_row_keys)
        if self.cursor_row is not None and 0 <= self.cursor_row < len(self.ordered_rows):
            row_key = self.ordered_rows[self.cursor_row].key
            if row_key:
                return [str(row_key.value)]
        return []

    def on_mouse_up(self, event: MouseUp) -> None:
        if event.button == 3:  # Right-click mouse button
            self.post_message(self.ContextMenuRequested(self.cursor_row))

    class ContextMenuRequested(Message):
        def __init__(self, row_index: Optional[int]) -> None:
            super().__init__()
            self.row_index = row_index

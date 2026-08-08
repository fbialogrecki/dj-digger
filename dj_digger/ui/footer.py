"""Customizable lower action bar / footer for Textual TUI."""

from __future__ import annotations

from typing import Dict, List, Optional
from textual.app import ComposeResult
from textual.widgets import Static


class ConfigurableFooter(Static):
    """Lower action bar with customizable keybinding hints and store legends."""

    def __init__(self, items: Optional[List[Dict[str, str]]] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.items = items or [
            {"key": "space", "label": "Play/Pause"},
            {"key": "g", "label": "Got"},
            {"key": "s", "label": "Skip"},
            {"key": "c", "label": "Copy Path"},
            {"key": "m", "label": "Menu"},
            {"key": "?", "label": "Help"},
        ]

    def render(self) -> str:
        parts = []
        for item in self.items:
            k = item.get("key", "")
            lbl = item.get("label", "")
            parts.append(f"[{k}] {lbl}")
        return "  ".join(parts)

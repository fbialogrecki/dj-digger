"""Crate library sidebar widget for Textual TUI."""

from __future__ import annotations

from typing import List, Optional
from textual.app import ComposeResult
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..library import CrateRecord, list_crates


class CrateSidebar(Static):
    """Sidebar widget displaying saved crate library."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.crates: List[CrateRecord] = []

    def compose(self) -> ComposeResult:
        yield Static(" 📦 Crates Library", id="sidebar_title")
        yield OptionList(id="crate_list")

    def reload(self, active_slug: Optional[str] = None) -> None:
        self.crates = list_crates()
        option_list = self.query_one("#crate_list", OptionList)
        option_list.clear_options()

        for crate in self.crates:
            count = len(crate.active_tracks)
            label = f"{crate.title[:25]} ({count})"
            option_list.add_option(Option(label, id=crate.slug))

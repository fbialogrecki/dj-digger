"""Interactive crate browser.

Opening every link at once means 287 browser tabs on a big playlist, which is
not a workflow. This screen lets you walk the list, open one link at a time,
filter down to a single store and mark what you already own - and the marks
survive between runs because they live in ``state.TrackState``.

One row is one track, not one link. A track selling on Bandcamp and gated on
Hypeddit is a single decision, so it gets a single row with a badge per store;
the store filter doubles as the way to say which of them ``o`` should follow.

It can also start from nothing: with no records it asks for a link, digs it in a
worker thread so the interface stays responsive, and fills itself in.
"""

from collections.abc import Sequence
from pathlib import Path

from .. import browser as browser_module
from .. import dig as dig_module
from ..library import CrateRecord
from ..models import LinkRecord
from ..player import PlayerBar
from ..state import TrackState
from .app import DiggerApp
from .keymap import (
    CALM_TICK,
    CRATES,
    FLASH,
    HELP_EXTRA,
    HELP_SCOPES,
    KEY_DISPLAY,
    KEYMAP,
    MIN_TITLE_WIDTH,
    OTHER,
    PLAYBACK,
    PLAYING_GLYPH,
    SELECTED,
    SPINNER,
    SPINNER_EVERY,
    STATUS_STYLES,
    TICK,
    WHOLE_LIST,
)
from .rows import Prepared, Row
from .screens import AskLinkScreen, ConfirmScreen, HelpScreen, SettingsScreen
from .widgets import CrateButton, CrateItem, ErrorBanner, StatusBar, TrackTable

__all__ = [
    "CALM_TICK", "CRATES", "FLASH", "HELP_EXTRA", "HELP_SCOPES", "KEYMAP",
    "KEY_DISPLAY", "MIN_TITLE_WIDTH", "OTHER", "PLAYBACK", "PLAYING_GLYPH",
    "SELECTED", "SPINNER", "SPINNER_EVERY", "STATUS_STYLES", "TICK",
    "WHOLE_LIST", "AskLinkScreen", "ConfirmScreen", "CrateButton", "CrateItem",
    "DiggerApp", "ErrorBanner", "HelpScreen", "PlayerBar", "Prepared", "Row",
    "SettingsScreen", "StatusBar", "TrackTable", "browser_module", "run_tui",
]


def run_tui(
    records: Sequence[LinkRecord] = (),
    *,
    state: TrackState | None = None,
    crate_title: str = "",
    export_format: str = "json",
    export_path: Path | None = None,
    dig_options: dig_module.DigOptions | None = None,
    crate_record: CrateRecord | None = None,
) -> None:
    app = DiggerApp(
        records,
        state=state,
        crate_title=crate_title,
        export_format=export_format,
        export_path=export_path,
        dig_options=dig_options,
        crate_record=crate_record,
    )
    try:
        app.run()
    finally:
        player = getattr(app, "player", None)
        if player is not None:
            player.close()
        client = getattr(app, "_client", None)
        if client is not None:
            client.close()

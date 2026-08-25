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

import logging
from collections.abc import Sequence
from pathlib import Path

from .. import browser as browser_module
from .. import dig as dig_module
from ..library import CrateRecord
from ..models import LinkRecord
from ..state import TrackState
from .app import DiggerApp
from .keymap import (
    CALM_TICK,
    CRATES,
    FLASH,
    KEYMAP,
    MIN_TITLE_WIDTH,
    OTHER,
    PLAYING_GLYPH,
    SELECTED,
    SPINNER,
    SPINNER_EVERY,
    STATUS_STYLES,
    TICK,
    WHOLE_LIST,
)
from .rows import Prepared, Row
from .screens import (
    AskLinkScreen,
    ConfirmScreen,
    ContextMenuScreen,
    GateProfileScreen,
    HelpScreen,
    SettingsScreen,
    SoundCloudAuthScreen,
)
from .widgets import (
    CrateButton,
    CrateItem,
    ErrorBanner,
    TrackTable,
)

__all__ = [
    "CALM_TICK", "CRATES", "FLASH", "KEYMAP",
    "MIN_TITLE_WIDTH", "OTHER", "PLAYING_GLYPH",
    "SELECTED", "SPINNER", "SPINNER_EVERY", "STATUS_STYLES", "TICK",
    "WHOLE_LIST", "AskLinkScreen", "ConfirmScreen", "ContextMenuScreen", "GateProfileScreen", "CrateButton", "CrateItem",
    "DiggerApp", "ErrorBanner", "HelpScreen",
    "Prepared", "Row",
    "SettingsScreen", "SoundCloudAuthScreen", "TrackTable", "browser_module", "run_tui",
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
    keep_logging: bool = False,
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
    # Textual draws the interface on stderr, so anything logged to the terminal
    # lands in the middle of the crate list: our own records at any level, and at
    # DEBUG the libraries' too, since that level puts a handler on the root
    # logger. Both are muted for as long as the app owns the screen - unless
    # ``keep_logging`` says --log-file has given the log somewhere else to go, in
    # which case silencing it is the opposite of what was asked for.
    silenced = [] if keep_logging else [logging.getLogger("dj_digger"), logging.getLogger()]
    levels = [(logger, logger.level) for logger in silenced]
    for logger in silenced:
        logger.setLevel(logging.CRITICAL + 1)
    try:
        app.run()
    finally:
        for logger, level in levels:
            logger.setLevel(level)

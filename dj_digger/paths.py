"""XDG base directories for dj-digger, in one place.

A leaf module on purpose: config, auth, db, state, library and cart all need
these, so anything imported here would be one step from an import cycle.
Not memoized - tests point XDG_* somewhere private after import.
"""

import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "dj-digger"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "dj-digger"

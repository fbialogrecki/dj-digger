"""Shared application directories and filename rules for dj-digger.

A leaf module on purpose: config, auth, db, state, library and cart all need
these, so anything imported here would be one step from an import cycle.
Not memoized - tests point XDG_* somewhere private after import.
"""

import os
import re
import sys
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "dj-digger"


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "dj-digger"


def log_dir() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'dj-digger' / 'Logs'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / 'dj-digger'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'dj-digger'


def playlist_download_directory(directory: str | Path, title: str) -> Path:
    """Share the playlist folder policy between TUI and desktop downloads."""
    base = Path(directory).expanduser()
    if not title.strip():
        return base
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', title)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
    folder = cleaned[:120].rstrip(' .') or 'playlist'
    return base if base.name.casefold() == folder.casefold() else base / folder


def unique_target(directory: Path, stem: str, suffix: str) -> Path:
    """The first of ``stem``, ``stem (1)``, ``stem (2)``... that is free in ``directory``.

    Nothing is created: the caller moves its finished file onto the name it is
    given, and holds whatever lock it needs against a neighbour doing the same.
    """

    target = directory / f"{stem}{suffix}"
    counter = 1
    while target.exists():
        target = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return target

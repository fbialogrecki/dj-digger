"""Shared application directories and filename rules for dj-digger.

A leaf module on purpose: config, auth, db, state, library and cart all need
these, so anything imported here would be one step from an import cycle.
Not memoized - tests point XDG_* somewhere private after import.
"""

import os
import re
import sys
from pathlib import Path

WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)

def portable_stem(value: str, budget: int) -> str:
    if value.partition('.')[0].upper() in WINDOWS_RESERVED:
        value = '_' + value
    stem = value.encode('utf-8')[:budget].decode('utf-8', errors='ignore').rstrip(' .') or '_'
    return stem


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
    """Share the playlist folder policy between desktop downloads."""
    base = Path(directory).expanduser()
    if not title.strip():
        return base
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', title)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
    folder = portable_stem(cleaned or 'playlist', 120)
    return base if base.name.casefold() == folder.casefold() else base / folder


def unique_target(directory: Path, stem: str, suffix: str) -> Path:
    """The first of ``stem``, ``stem (1)``, ``stem (2)``... that is free in ``directory``.

    Nothing is created: the caller moves its finished file onto the name it is
    given, and holds whatever lock it needs against a neighbour doing the same.
    """

    target = directory / f"{portable_stem(stem, 255 - len(suffix.encode()))}{suffix}"
    counter = 1
    while os.path.lexists(target):
        ending = f" ({counter}){suffix}"
        target = directory / (portable_stem(stem, 255 - len(ending.encode())) + ending)
        counter += 1
    return target

"""XDG base directories for dj-digger, and the one filename rule they share.

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

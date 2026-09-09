"""Locations of explicitly bundled tools; source installations keep system tools."""
import sys
from pathlib import Path


def tool(name: str) -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys.executable).resolve().parent
    candidate = root / (name + ".exe")
    if name != "dj-digger-analysis":
        candidate = root / "tools" / (name + ".exe")
    if not candidate.is_file():
        raise FileNotFoundError(f"Bundled tool missing: {name}; reinstall dj-digger")
    return candidate


def hold():
    if sys.platform != 'win32':
        return None
    import ctypes
    create = ctypes.windll.kernel32.CreateMutexW
    create.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create.restype = ctypes.c_void_p
    handle = create(None, False, 'dj-digger-desktop-install')
    if not handle:
        raise ctypes.WinError()
    return handle

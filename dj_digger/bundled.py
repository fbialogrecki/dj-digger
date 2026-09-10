"""Locations of explicitly bundled tools; source installations keep system tools."""
import sys
from pathlib import Path


def resource_root() -> Path:
    """Frozen resources live beside the EXE on Windows, in Resources on macOS."""
    executable_dir = Path(sys.executable).resolve().parent
    return executable_dir.parent / "Resources" if sys.platform == "darwin" else executable_dir


def tool(name: str) -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys.executable).resolve().parent if name == "dj-digger-analysis" else resource_root() / "tools"
    candidate = root / (name + (".exe" if sys.platform == "win32" else ""))
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

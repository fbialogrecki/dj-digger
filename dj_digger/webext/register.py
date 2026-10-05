"""Register the Native Messaging host and install the extension copy.

Writes the ``com.github.fbialogrecki.djdigger`` host manifest for Chrome,
Chromium, Brave, Edge (``allowed_origins``) and Firefox (``allowed_extensions``)
on Linux, macOS and Windows (HKCU registry). Only browsers whose profile
directory exists are touched. Flatpak/Snap browsers cannot reach the host and
are only reported, never written to. Both operations are idempotent.

The Chrome extension ID is computed from the ``key`` in the extension's
``manifest.json``; the Firefox ID is its ``browser_specific_settings.gecko.id``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from ..paths import data_dir
from ..private_json import write_json_atomic

HOST_NAME = "com.github.fbialogrecki.djdigger"
HOST_EXECUTABLE = "dj-digger-host"
EXTENSION_SOURCE = Path(__file__).parent / "extension"


class RegistrationError(RuntimeError):
    """The host or extension cannot be registered (shown to the user as-is)."""


class Target(NamedTuple):
    name: str
    kind: str  # "chrome" -> allowed_origins, "firefox" -> allowed_extensions
    present: bool  # the browser's profile directory exists
    where: Path | str  # NativeMessagingHosts directory, or HKCU registry key on Windows


@dataclass
class Registration:
    extension_dir: Path
    written: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # unsupported browsers etc.


def chrome_extension_id(public_key_b64: str) -> str:
    """Chrome's ID for an unpacked extension: SHA-256 of the DER key, hex digits mapped to a-p."""

    try:
        der = base64.b64decode(public_key_b64, validate=True)
    except ValueError as exc:
        raise RegistrationError("Extension manifest 'key' is not valid base64") from exc
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])


def read_extension_manifest(directory: Path | None = None) -> dict:
    path = (directory or EXTENSION_SOURCE) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistrationError(f"Browser extension manifest is missing or unreadable: {path}") from exc
    if not isinstance(manifest, dict):
        raise RegistrationError(f"Browser extension manifest is not an object: {path}")
    return manifest


def extension_ids(manifest: dict) -> tuple[str, str]:
    """``(chrome_id, firefox_id)`` of the extension."""

    key = manifest.get("key")
    gecko = ((manifest.get("browser_specific_settings") or {}).get("gecko") or {}).get("id")
    if not isinstance(key, str) or not isinstance(gecko, str):
        raise RegistrationError("Extension manifest needs 'key' and browser_specific_settings.gecko.id")
    return chrome_extension_id(key), gecko


def host_executable() -> Path:
    """Absolute path of the ``dj-digger-host`` launcher for this install."""

    name = HOST_EXECUTABLE + (".exe" if sys.platform == "win32" else "")
    sibling = Path(sys.executable).with_name(name)  # frozen bundle or the venv's bin
    found = None if getattr(sys, "frozen", False) else shutil.which(HOST_EXECUTABLE)
    for candidate in (sibling, Path(found) if found else None):
        if candidate is not None and candidate.is_file():
            return candidate.absolute()
    raise RegistrationError(f"Cannot find the {name} executable next to {sys.executable}")


def sync_extension() -> Path:
    """Copy the packaged extension to ``data_dir()/browser-extension`` when its version differs (D10)."""

    destination = data_dir() / "browser-extension"
    version = read_extension_manifest()["version"]
    try:
        current = json.loads((destination / "manifest.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        current = None
    if current == version:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="browser-extension-", dir=destination.parent))
    try:
        shutil.copytree(EXTENSION_SOURCE, staging, dirs_exist_ok=True)
        old = destination.with_name(destination.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if destination.exists():
            destination.rename(old)
        # ponytail: two renames, not one atomic swap; a crash between them leaves
        # the previous copy in browser-extension.old and the next run recopies.
        staging.rename(destination)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination


def _targets() -> list[Target]:
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return [
            Target("Chrome", "chrome", (local / "Google/Chrome/User Data").is_dir(),
                   r"Software\Google\Chrome\NativeMessagingHosts"),
            Target("Chromium", "chrome", (local / "Chromium/User Data").is_dir(),
                   r"Software\Chromium\NativeMessagingHosts"),
            Target("Brave", "chrome", (local / "BraveSoftware/Brave-Browser/User Data").is_dir(),
                   r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"),
            Target("Edge", "chrome", (local / "Microsoft/Edge/User Data").is_dir(),
                   r"Software\Microsoft\Edge\NativeMessagingHosts"),
            Target("Firefox", "firefox", (roaming / "Mozilla/Firefox").is_dir(),
                   r"Software\Mozilla\NativeMessagingHosts"),
        ]
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support"
        return [
            Target(name, "chrome", (support / profile).is_dir(), support / profile / "NativeMessagingHosts")
            for name, profile in (
                ("Chrome", "Google/Chrome"), ("Chromium", "Chromium"),
                ("Brave", "BraveSoftware/Brave-Browser"), ("Edge", "Microsoft Edge"),
            )
        ] + [Target("Firefox", "firefox", (support / "Firefox").is_dir(),
                    support / "Mozilla" / "NativeMessagingHosts")]
    config = home / ".config"
    firefox_present = (home / ".mozilla/firefox").is_dir() or (config / "mozilla/firefox").is_dir()
    return [
        Target(name, "chrome", (config / profile).is_dir(), config / profile / "NativeMessagingHosts")
        for name, profile in (
            ("Chrome", "google-chrome"), ("Chromium", "chromium"),
            ("Brave", "BraveSoftware/Brave-Browser"), ("Edge", "microsoft-edge"),
        )
    ] + [
        # Firefox 147+ reads the XDG location, older ones the dotdir: write both.
        Target("Firefox", "firefox", firefox_present, home / ".mozilla" / "native-messaging-hosts"),
        Target("Firefox", "firefox", firefox_present, config / "mozilla" / "native-messaging-hosts"),
    ]


def _unsupported_notes() -> list[str]:
    if sys.platform in ("win32", "darwin"):
        return []
    home = Path.home()
    sandboxed = {
        "Firefox": (home / ".var/app/org.mozilla.firefox", home / "snap/firefox"),
        "Chrome": (home / ".var/app/com.google.Chrome",),
        "Chromium": (home / ".var/app/org.chromium.Chromium", home / "snap/chromium"),
        "Brave": (home / ".var/app/com.brave.Browser", home / "snap/brave"),
        "Edge": (home / ".var/app/com.microsoft.Edge",),
    }
    return [
        f"{name} installed as Flatpak/Snap is not supported (it cannot start the native host)"
        for name, paths in sandboxed.items() if any(p.exists() for p in paths)
    ]


def _manifest(kind: str, host: Path, manifest: dict) -> dict:
    chrome_id, gecko_id = extension_ids(manifest)
    body = {"name": HOST_NAME, "description": "dj-digger browser automation relay",
            "path": str(host), "type": "stdio"}
    if kind == "firefox":
        return {**body, "allowed_extensions": [gecko_id]}
    return {**body, "allowed_origins": [f"chrome-extension://{chrome_id}/"]}


def _windows_manifest_path(kind: str) -> Path:
    return data_dir() / "native-messaging" / f"{HOST_NAME}.{kind}.json"


def register() -> Registration:
    """Write host manifests for every detected browser and refresh the extension copy."""

    manifest = read_extension_manifest()
    extension_ids(manifest)  # fail before writing anything
    host = host_executable()
    result = Registration(extension_dir=sync_extension(), notes=_unsupported_notes())
    for target in _targets():
        if not target.present:
            continue
        body = _manifest(target.kind, host, manifest)
        if sys.platform == "win32":
            import winreg

            path = _windows_manifest_path(target.kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, body, private=False)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{target.where}\\{HOST_NAME}") as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(path))
            result.written.append(f"{target.name}: HKCU\\{target.where}\\{HOST_NAME}")
        else:
            path = Path(target.where) / f"{HOST_NAME}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, body, private=False)
            result.written.append(f"{target.name}: {path}")
    if not result.written:
        result.notes.append("No supported browser profile was found; start your browser once and retry")
    return result


def unregister() -> list[str]:
    """Remove every host manifest and registry key this module may have written."""

    removed: list[str] = []
    for target in _targets():
        if sys.platform == "win32":
            import winreg

            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{target.where}\\{HOST_NAME}")
                removed.append(f"HKCU\\{target.where}\\{HOST_NAME}")
            except OSError:
                pass
        else:
            path = Path(target.where) / f"{HOST_NAME}.json"
            if path.is_file():
                path.unlink()
                removed.append(str(path))
    if sys.platform == "win32":
        for kind in ("chrome", "firefox"):
            path = _windows_manifest_path(kind)
            if path.is_file():
                path.unlink()
                removed.append(str(path))
    return removed

"""Authentication module for SoundCloud accounts.

Handles saving/loading OAuth tokens, scanning browser cookies (supporting Linux,
macOS, and WSL/Windows paths), and verifying credentials with SoundCloud's /me API.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "dj-digger"
AUTH_FILE = CONFIG_DIR / "auth.json"
OAUTH_TOKEN_RE = re.compile(r'^[0-9]+-[0-9]+-[A-Za-z0-9_-]+')

LOGGER = logging.getLogger(__name__)


def auth_file_path() -> Path:
    return AUTH_FILE


def get_stored_token() -> Optional[str]:
    """Retrieve OAuth token from environment variable or saved config file."""
    env_token = os.environ.get("SOUNDCLOUD_OAUTH_TOKEN", "").strip()
    if env_token:
        return env_token

    if not AUTH_FILE.exists():
        return None

    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            token = data.get("oauth_token")
            if isinstance(token, str) and token.strip():
                return token.strip()
    except Exception as exc:
        LOGGER.debug("Failed to read auth.json: %s", exc)

    return None


def get_stored_auth_info() -> Dict[str, Any]:
    """Get full stored auth details (token, username, etc.)."""
    if not AUTH_FILE.exists():
        return {}
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_token(token: str, username: str = "", user_id: Optional[int] = None) -> None:
    """Save an OAuth token so that it is never readable by anyone else.

    Not ``write_text`` followed by a chmod: that creates the file with the umask
    default, usually 0644, writes a live token into it, and only then narrows
    the permissions - so there is a window where any other account on the
    machine can read it. ``mkstemp`` hands back a file that is 0600 before it
    holds a single byte, and ``os.replace`` moves it into place atomically,
    which is also how ``config``, ``state`` and ``library`` already write.
    """

    directory = AUTH_FILE.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError as exc:
        LOGGER.debug("Could not tighten permissions on %s: %s", directory, exc)

    payload = {
        "oauth_token": token.strip(),
        "username": username,
        "user_id": user_id,
    }
    descriptor, temporary = tempfile.mkstemp(dir=str(directory), prefix=".auth-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
        os.replace(temporary, AUTH_FILE)
    except BaseException:
        # Never leave a temporary holding the token behind on the way out.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def clear_token() -> None:
    """Delete saved authentication info."""
    if AUTH_FILE.exists():
        try:
            AUTH_FILE.unlink()
        except OSError as exc:
            LOGGER.debug("Could not remove auth.json: %s", exc)


def verify_token(token: str, client_id: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Test token against SoundCloud's /me endpoint.

    Returns user data dictionary if valid, None if invalid/unauthorized.
    """
    token = token.strip()
    if not token:
        return None

    url = f"https://api-v2.soundcloud.com/me?client_id={client_id}"
    headers = {
        "Authorization": f"OAuth {token}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }

    try:
        res = requests.get(url, headers=headers, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict) and "username" in data:
                return data
    except Exception as exc:
        LOGGER.debug("Token verification request failed: %s", exc)

    return None


def _extract_sqlite_cookies(db_path: str) -> List[str]:
    """Extract oauth_token values from unencrypted SQLite cookies database (e.g. Firefox)."""
    if not os.path.exists(db_path):
        return []

    tokens: List[str] = []
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        os.chmod(tmp_path, 0o600)
        shutil.copyfile(db_path, tmp_path)

        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        # Check moz_cookies (Firefox)
        try:
            cursor.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%soundcloud%' AND name = 'oauth_token'"
            )
            for row in cursor.fetchall():
                if row[1] and isinstance(row[1], str) and row[1].strip():
                    tokens.append(row[1].strip())
        except sqlite3.OperationalError:
            pass

        # Check cookies (Chromium)
        try:
            cursor.execute(
                "SELECT name, value FROM cookies WHERE host_key LIKE '%soundcloud%' AND name = 'oauth_token'"
            )
            for row in cursor.fetchall():
                if row[1] and isinstance(row[1], str) and row[1].strip():
                    tokens.append(row[1].strip())
        except sqlite3.OperationalError:
            pass

        conn.close()
    except Exception:
        pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return tokens


def find_browser_cookie_paths() -> List[str]:
    """Locate browser cookie database files on Linux, macOS, and WSL/Windows paths."""
    candidate_paths: List[str] = []

    # Linux / macOS standard paths
    home = Path.home()
    candidate_paths.extend(glob.glob(str(home / ".mozilla/firefox/*/cookies.sqlite")))
    candidate_paths.extend(glob.glob(str(home / ".config/google-chrome/*/Network/Cookies")))
    candidate_paths.extend(glob.glob(str(home / ".config/chromium/*/Network/Cookies")))

    # WSL Windows paths (/mnt/c/Users/<user>/...)
    if os.path.exists("/mnt/c/Users"):
        for win_user_dir in glob.glob("/mnt/c/Users/*"):
            # Firefox on Windows (unencrypted SQLite)
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite"))
            # Chrome / Edge / Brave on Windows
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Local/Google/Chrome/User Data/*/Network/Cookies"))
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Local/Microsoft/Edge/User Data/*/Network/Cookies"))
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Local/BraveSoftware/Brave-Browser/User Data/*/Network/Cookies"))

    return candidate_paths


def scan_browser_cookies() -> List[str]:
    """Find any plaintext oauth_token values stored in browser cookie stores."""
    found_tokens: List[str] = []
    for path in find_browser_cookie_paths():
        if "cookies.sqlite" in path or "Cookies" in path:
            found_tokens.extend(_extract_sqlite_cookies(path))

    # Deduplicate while preserving order
    seen = set()
    result = []
    for t in found_tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def auto_detect_and_verify(client_id: str) -> Optional[Tuple[str, str, int]]:
    """Scan available browser cookie stores for a valid SoundCloud OAuth token.

    Returns (token, username, user_id) if a working session is found, else None.
    """
    tokens = scan_browser_cookies()
    for token in tokens:
        user_data = verify_token(token, client_id)
        if user_data:
            username = user_data.get("username") or "User"
            user_id = user_data.get("id")
            save_token(token, username, user_id)
            return token, username, user_id
    return None

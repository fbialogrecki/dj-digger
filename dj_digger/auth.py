"""Authentication module for SoundCloud accounts.

Handles saving/loading OAuth tokens, scanning browser cookies (supporting Linux,
macOS, and WSL/Windows paths), and verifying credentials with SoundCloud's /me API.
"""

import glob
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from threading import Event, Lock
from typing import Any

import requests

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "dj-digger"
AUTH_FILE = CONFIG_DIR / "auth.json"
OAUTH_TOKEN_RE = re.compile(r'^[0-9]+-[0-9]+-[A-Za-z0-9_-]+')

LOGGER = logging.getLogger(__name__)
SOUNDCLOUD_SIGN_IN_URL = "https://soundcloud.com/signin"
BROWSER_PROFILE_LOCK = Lock()


class SoundCloudAuthError(RuntimeError):
    """SoundCloud login could not be completed safely."""


class SoundCloudAuthCancelled(SoundCloudAuthError):
    """The user cancelled an in-progress SoundCloud login."""


class SoundCloudAuthTimeout(SoundCloudAuthError):
    """The browser login did not yield a SoundCloud OAuth cookie in time."""


def auth_file_path() -> Path:
    return AUTH_FILE


def soundcloud_browser_profile_path() -> Path:
    """Return the private app-managed browser profile used only for SoundCloud."""

    data_home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    path = data_home / "dj-digger" / "soundcloud-browser"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def get_stored_token() -> str | None:
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


def get_stored_auth_info() -> dict[str, Any]:
    """Get full stored auth details (token, username, etc.)."""
    if not AUTH_FILE.exists():
        return {}
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        LOGGER.debug("Could not read %s: %s", AUTH_FILE, exc)
        return {}


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write credentials without making them broadly readable.

    Not ``write_text`` followed by a chmod: that creates the file with the umask
    default, usually 0644, writes a live token into it, and only then narrows
    the permissions - so there is a window where any other account on the
    machine can read it. ``mkstemp`` hands back a file that is 0600 before it
    holds a single byte, and ``os.replace`` moves it into place atomically,
    which is also how ``config``, ``state`` and ``library`` already write.
    """

    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError as exc:
        LOGGER.debug("Could not tighten permissions on %s: %s", directory, exc)

    descriptor, temporary = tempfile.mkstemp(dir=str(directory), prefix=".auth-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
        os.replace(temporary, path)
    except BaseException:
        # Never leave a temporary holding the token behind on the way out.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def save_token(token: str, username: str = "", user_id: int | None = None) -> None:
    """Save SoundCloud authentication in the private credential store."""

    write_private_json(
        AUTH_FILE,
        {
            "oauth_token": token.strip(),
            "username": username,
            "user_id": user_id,
        },
    )


def clear_token() -> None:
    """Delete saved authentication info."""
    if AUTH_FILE.exists():
        try:
            AUTH_FILE.unlink()
        except OSError as exc:
            LOGGER.debug("Could not remove auth.json: %s", exc)


def verify_token(token: str, client_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
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


def _extract_sqlite_cookies(db_path: str) -> list[str]:
    """Read oauth_token out of a Firefox cookie store.

    Firefox only. There was a second query here against Chromium's ``cookies``
    table, reading its ``value`` column - which in Chromium is always empty,
    because the cookie lives in ``encrypted_value`` behind a key in the system
    keyring. It could not ever have returned anything, so it has gone rather than
    stayed as a promise the code does not keep. On Chromium, paste the token in
    by hand or set SOUNDCLOUD_OAUTH_TOKEN.
    """

    if not os.path.exists(db_path):
        return []

    tokens: list[str] = []
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        os.chmod(tmp_path, 0o600)
        shutil.copyfile(db_path, tmp_path)

        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%soundcloud%' AND name = 'oauth_token'"
            )
            for row in cursor.fetchall():
                if row[1] and isinstance(row[1], str) and row[1].strip():
                    tokens.append(row[1].strip())
        except sqlite3.OperationalError as exc:
            # Not a Firefox store, or a schema we do not know.
            LOGGER.debug("No moz_cookies in %s: %s", db_path, exc)

        conn.close()
    except (OSError, sqlite3.Error) as exc:
        LOGGER.debug("Could not read cookies from %s: %s", db_path, exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return tokens


def find_browser_cookie_paths() -> list[str]:
    """Locate browser cookie database files on Linux, macOS, and WSL/Windows paths."""
    candidate_paths: list[str] = []

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


def scan_browser_cookies() -> list[str]:
    """Find any plaintext oauth_token values stored in browser cookie stores."""
    found_tokens: list[str] = []
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


def auto_detect_and_verify(client_id: str) -> tuple[str, str, int] | None:
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


def verify_and_save(token: str, client_id: str) -> tuple[str, str, int]:
    """Verify a token before replacing the currently working credentials."""

    user_data = verify_token(token, client_id)
    if not user_data:
        raise SoundCloudAuthError("SoundCloud rejected the login token.")
    username = str(user_data.get("username") or "User")
    user_id = user_data.get("id")
    save_token(token, username, user_id)
    return token, username, user_id


def login_with_chromium(
    client_id: str,
    *,
    timeout: float = 300.0,
    cancel: Event | None = None,
    context_factory=None,
    status=None,
) -> tuple[str, str, int]:
    """Open an isolated browser and persist only its verified ``oauth_token``.

    The persistent Chromium profile keeps the browser login across runs, while
    ``auth.json`` receives only the single cookie needed by the API. Passwords
    and unrelated cookies never cross this boundary.
    """

    cancel = cancel or Event()
    status = status or (lambda _message: None)
    if cancel.is_set():
        raise SoundCloudAuthCancelled("SoundCloud login was cancelled.")
    if not BROWSER_PROFILE_LOCK.acquire(blocking=False):
        raise SoundCloudAuthError("The private browser profile is already in use.")

    try:
        install_chromium = None
        chromium_missing = ()
        if context_factory is None:
            # The cart already owns the small Playwright lifecycle layer. Importing
            # lazily keeps Playwright optional for every non-browser command.
            from . import cart

            context_factory = cart._browser_context
            install_chromium = cart.install_chromium
            chromium_missing = (cart.ChromiumMissing,)

        status("Opening the private SoundCloud browser…")

        def wait_for_cookie(context) -> tuple[str, str, int]:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(SOUNDCLOUD_SIGN_IN_URL)
            deadline = time.monotonic() + timeout
            status("Log in to SoundCloud in Chromium. Waiting for completion…")
            rejected_tokens: set[str] = set()
            while time.monotonic() < deadline:
                if cancel.wait(0.25):
                    raise SoundCloudAuthCancelled("SoundCloud login was cancelled.")
                cookies = context.cookies(["https://soundcloud.com"])
                token = next(
                    (
                        str(cookie.get("value") or "").strip()
                        for cookie in cookies
                        if cookie.get("name") == "oauth_token"
                    ),
                    "",
                )
                if token and token not in rejected_tokens:
                    status("Verifying the SoundCloud login…")
                    try:
                        return verify_and_save(token, client_id)
                    except SoundCloudAuthError:
                        rejected_tokens.add(token)
                        status(
                            "That SoundCloud session is no longer valid. Log in again "
                            "in Chromium…"
                        )
            raise SoundCloudAuthTimeout(
                "SoundCloud login timed out after 5 minutes; no credentials were saved."
            )

        def run_browser(profile: Path):
            with context_factory(profile) as context:
                return wait_for_cookie(context)

        try:
            profile = soundcloud_browser_profile_path()
            try:
                return run_browser(profile)
            except chromium_missing:
                status("Installing the matching Playwright Chromium build…")
                install_chromium(cancel)
                return run_browser(profile)
        except (SoundCloudAuthError, KeyboardInterrupt):
            raise
        except Exception as exc:
            # Do not include the browser exception: Playwright diagnostics can echo
            # page state and must not become a path by which credentials reach logs.
            raise SoundCloudAuthError("Could not start the SoundCloud login browser.") from exc
    finally:
        BROWSER_PROFILE_LOCK.release()

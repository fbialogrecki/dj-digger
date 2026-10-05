"""Authentication module for SoundCloud accounts.

Handles saving/loading OAuth tokens, scanning browser cookies (supporting Linux,
macOS, and WSL/Windows paths), and verifying credentials with SoundCloud's /me API.
"""

import glob
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import Any

import requests

from dj_digger import automation_errors

from .http import USER_AGENT
from .paths import config_dir
from .private_json import write_private_json
from .webext import link
from .webext import page as webpage

CONFIG_DIR = config_dir()
AUTH_FILE = CONFIG_DIR / "auth.json"

LOGGER = logging.getLogger(__name__)
SOUNDCLOUD_URL = "https://soundcloud.com"
SOUNDCLOUD_SIGN_IN_URL = f"{SOUNDCLOUD_URL}/signin"


class SoundCloudAuthError(RuntimeError):
    """SoundCloud login could not be completed safely."""


class SoundCloudAuthCancelled(SoundCloudAuthError):
    """The user cancelled an in-progress SoundCloud login."""


def get_stored_token() -> str | None:
    """Retrieve OAuth token from environment variable or saved config file."""
    env_token = os.environ.get("SOUNDCLOUD_OAUTH_TOKEN", "").strip()
    if env_token:
        return env_token
    token = get_stored_auth_info().get("oauth_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
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
    headers = {"Authorization": f"OAuth {token}", "User-Agent": USER_AGENT}

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
    try:
        # A copy, because Firefox holds the live store locked; in a private
        # directory of its own that goes away with the copy.
        with tempfile.TemporaryDirectory() as scratch:
            copy = os.path.join(scratch, "cookies.sqlite")
            shutil.copyfile(db_path, copy)
            with closing(sqlite3.connect(copy)) as conn:
                try:
                    rows = conn.execute(
                        "SELECT name, value FROM moz_cookies "
                        "WHERE host LIKE '%soundcloud%' AND name = 'oauth_token'"
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    # Not a Firefox store, or a schema we do not know.
                    LOGGER.debug("No moz_cookies in %s: %s", db_path, exc)
                    rows = []
            for row in rows:
                if row[1] and isinstance(row[1], str) and row[1].strip():
                    tokens.append(row[1].strip())
    except (OSError, sqlite3.Error) as exc:
        LOGGER.debug("Could not read cookies from %s: %s", db_path, exc)

    return tokens


def find_browser_cookie_paths() -> list[str]:
    """Locate browser cookie database files on Linux, macOS, and WSL/Windows paths."""
    candidate_paths: list[str] = []

    # Linux / macOS standard paths
    home = Path.home()
    # Only Firefox stores: _extract_sqlite_cookies queries moz_cookies, and
    # Chromium-family value columns are encrypted/empty anyway, so probing those
    # stores only copied the user's cookie database to a temp file for nothing.
    candidate_paths.extend(glob.glob(str(home / ".mozilla/firefox/*/cookies.sqlite")))

    # WSL Windows paths (/mnt/c/Users/<user>/...): Firefox on Windows keeps an
    # unencrypted SQLite store too.
    for win_user_dir in glob.glob("/mnt/c/Users/*"):
        candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite"))

    return candidate_paths


def scan_browser_cookies() -> list[str]:
    """Find any plaintext oauth_token values stored in browser cookie stores."""
    found_tokens: list[str] = []
    for path in find_browser_cookie_paths():
        found_tokens.extend(_extract_sqlite_cookies(path))

    # Deduplicate while preserving order
    return list(dict.fromkeys(found_tokens))


def auto_detect_and_verify(client_id: str) -> tuple[str, str, int] | None:
    """Scan available browser cookie stores for a valid SoundCloud OAuth token.

    Returns (token, username, user_id) if a working session is found, else None.
    """
    for token in scan_browser_cookies():
        try:
            return verify_and_save(token, client_id)
        except SoundCloudAuthError:
            continue
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


def _oauth_cookie(cookies: list[dict[str, Any]]) -> str:
    """The one cookie the API needs, out of everything the browser holds."""

    return next(
        (
            str(cookie.get("value") or "").strip()
            for cookie in cookies
            if cookie.get("name") == "oauth_token"
        ),
        "",
    )


def _wait_for_oauth_cookie(
    context: Any,
    client_id: str,
    timeout: float,
    cancel: Event,
    status: Any,
    rejected_tokens: set[str] | None = None,
) -> tuple[str, str, int]:
    """Show the work window on the sign-in page until a verified ``oauth_token`` appears."""

    rejected_tokens = set() if rejected_tokens is None else rejected_tokens
    context.show()
    page = context.new_page()
    page.goto(SOUNDCLOUD_SIGN_IN_URL)
    deadline = time.monotonic() + timeout
    status("Log in to SoundCloud in your browser. Waiting for completion…")
    while time.monotonic() < deadline:
        if cancel.wait(0.25):
            raise SoundCloudAuthCancelled("SoundCloud login was cancelled.")
        token = _oauth_cookie(context.cookies([SOUNDCLOUD_URL]))
        if token and token not in rejected_tokens:
            status("Verifying the SoundCloud login…")
            try:
                return verify_and_save(token, client_id)
            except SoundCloudAuthError:
                rejected_tokens.add(token)
                status(
                    "That SoundCloud session is no longer valid. Log in again "
                    "in your browser…"
                )
        elif page.is_closed() or context.is_closed():
            # The cookie read above delivered the browser's events, so a closed tab or work window is seen here.
            raise SoundCloudAuthError(
                "The SoundCloud sign-in tab was closed; no credentials were saved."
            )
    raise SoundCloudAuthError(
        f"SoundCloud login timed out after {timeout / 60:g} minutes; "
        "no credentials were saved."
    )


def login_with_browser(
    client_id: str,
    *,
    timeout: float = 300.0,
    cancel: Event | None = None,
    status=None,
) -> tuple[str, str, int]:
    """Take the SoundCloud login from the user's own browser and persist only its
    verified ``oauth_token``.

    The browser extension reads the one cookie; if it is already there and valid
    no window is shown, otherwise the sign-in page opens in the work window.
    Passwords and unrelated cookies never cross this boundary.
    """

    cancel = cancel or Event()
    status = status or (lambda _message: None)
    if cancel.is_set():
        raise SoundCloudAuthCancelled("SoundCloud login was cancelled.")

    try:
        status("Looking for a SoundCloud login in your browser…")
        with webpage.session(visible=False) as context:
            rejected: set[str] = set()
            if token := _oauth_cookie(context.cookies([SOUNDCLOUD_URL])):
                try:
                    return verify_and_save(token, client_id)
                except SoundCloudAuthError:
                    rejected.add(token)
            return _wait_for_oauth_cookie(context, client_id, timeout, cancel, status, rejected)
    except (SoundCloudAuthError, KeyboardInterrupt):
        raise
    except link.ExtensionMissing as exc:
        raise SoundCloudAuthError(
            "Connect the dj-digger browser extension to your browser first "
            "(desktop: Connect browser; terminal: 'dj-digger browser register'), "
            "then log in again."
        ) from exc
    except automation_errors.AutomationError as exc:
        # Written by this application (browser or extension restarted, ...).
        raise SoundCloudAuthError(str(exc)) from exc
    except Exception as exc:
        # Do not include the browser exception: page diagnostics can echo
        # page state and must not become a path by which credentials reach logs.
        LOGGER.warning("SoundCloud login browser failed: %s", type(exc).__name__)
        raise SoundCloudAuthError("Could not use your browser for the SoundCloud login.") from exc

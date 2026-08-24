"""Spotify credentials and the one library action used by Hypeddit gates."""

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import requests

from . import browser as browser_module
from .auth import CONFIG_DIR, write_private_json

AUTH_FILE = CONFIG_DIR / "spotify.json"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_ROOT = "https://api.spotify.com/v1"
SCOPE = "user-follow-modify"


class SpotifyError(RuntimeError):
    """A safe, user-facing Spotify authentication or API failure."""


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def login(
    client_id: str,
    *,
    browser: str = "",
    timeout: float = 180,
    session=requests,
    opener: Callable[[str, str], bool] = browser_module.open_url,
) -> None:
    """Complete one Authorization Code with PKCE loopback login."""

    client_id = client_id.strip()
    if not client_id:
        raise SpotifyError("Spotify client ID is required")

    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            result["path"] = parsed.path
            result["state"] = query.get("state", [""])[0]
            result["code"] = query.get("code", [""])[0]
            result["error"] = query.get("error", [""])[0]
            body = b"Spotify login received. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = timeout
    redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPE,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": _challenge(verifier),
        }
    )
    try:
        if not opener(f"{AUTHORIZE_URL}?{query}", browser):
            raise SpotifyError("Could not open the Spotify login page")
        server.handle_request()
    finally:
        server.server_close()

    if result.get("path") != "/callback" or result.get("state") != state:
        raise SpotifyError("Spotify login returned an invalid OAuth state")
    if result.get("error"):
        raise SpotifyError(f"Spotify login was denied: {result['error']}")
    code = result.get("code")
    if not code:
        raise SpotifyError("Spotify login timed out before the callback arrived")
    try:
        response = session.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SpotifyError(f"Spotify token exchange failed: {exc}") from exc
    if response.status_code != 200:
        raise SpotifyError(
            f"Spotify token exchange returned HTTP {response.status_code}"
        )
    payload = response.json()
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    if not access or not refresh:
        raise SpotifyError("Spotify token exchange returned incomplete credentials")
    save_credentials(
        {
            "client_id": client_id,
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": time.time() + int(payload.get("expires_in") or 3600),
            "scope": payload.get("scope") or SCOPE,
        }
    )


def load_credentials() -> dict[str, Any]:
    try:
        payload = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SpotifyError("Could not read the saved Spotify login") from exc
    return payload if isinstance(payload, dict) else {}


def save_credentials(payload: dict[str, Any]) -> None:
    write_private_json(AUTH_FILE, payload)


def clear_credentials() -> None:
    try:
        AUTH_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SpotifyError("Could not remove the saved Spotify login") from exc


def access_token(*, session=requests, now: float | None = None) -> str:
    credentials = load_credentials()
    current_time = time.time() if now is None else now
    token = str(credentials.get("access_token") or "")
    if token and float(credentials.get("expires_at") or 0) > current_time + 30:
        return token

    client_id = str(credentials.get("client_id") or "")
    refresh_token = str(credentials.get("refresh_token") or "")
    if not client_id or not refresh_token:
        raise SpotifyError(
            "Spotify login required; run "
            "'dj-digger auth spotify login --client-id ...'"
        )
    try:
        response = session.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SpotifyError(f"Could not refresh Spotify login: {exc}") from exc
    if response.status_code != 200:
        raise SpotifyError(
            f"Spotify token refresh returned HTTP {response.status_code}"
        )
    payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise SpotifyError("Spotify token refresh returned no access token")
    credentials.update(
        access_token=token,
        refresh_token=payload.get("refresh_token") or refresh_token,
        expires_at=current_time + int(payload.get("expires_in") or 3600),
        scope=payload.get("scope") or credentials.get("scope") or SCOPE,
    )
    save_credentials(credentials)
    return token


def save_uris(uris: list[str], *, session=requests) -> None:
    if not uris or any(not uri.startswith("spotify:artist:") for uri in uris):
        raise SpotifyError("Hypeddit requested an unsupported Spotify action")
    token = access_token(session=session)
    try:
        response = session.put(
            f"{API_ROOT}/me/library",
            params={"uris": ",".join(uris)},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise SpotifyError(f"Spotify library request failed: {exc}") from exc
    if response.status_code not in (200, 204):
        raise SpotifyError(
            f"Spotify library request returned HTTP {response.status_code}"
        )

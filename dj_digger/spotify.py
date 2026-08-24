"""Spotify credentials and the one library action used by Hypeddit gates."""

import json
import time
from typing import Any

import requests

from .auth import CONFIG_DIR, write_private_json

AUTH_FILE = CONFIG_DIR / "spotify.json"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_ROOT = "https://api.spotify.com/v1"
SCOPE = "user-follow-modify"


class SpotifyError(RuntimeError):
    """A safe, user-facing Spotify authentication or API failure."""


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

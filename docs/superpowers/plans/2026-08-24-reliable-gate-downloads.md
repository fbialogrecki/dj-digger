# Reliable Gate Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the TUI error banner and honest progress reporting, make SoundCloud failures actionable, add Spotify PKCE login, and complete or clearly reject the reported Hypeddit gate shapes.

**Architecture:** Keep every existing download entry point and fix the shared functions they already use. Add one focused Spotify module using PKCE, owner-only JSON persistence, token refresh, and Spotify's current generic library endpoint; let the existing Hypeddit resolver invoke it only for declared Spotify artist actions.

**Tech Stack:** Python 3.12 standard library, `requests`, BeautifulSoup, Textual, Rich, pytest.

---

## File map

- Create `dj_digger/spotify.py`: Spotify PKCE login, credential persistence, refresh, and `PUT /me/library`.
- Create `tests/test_spotify.py`: offline Spotify OAuth, refresh, persistence, and API tests.
- Modify `dj_digger/auth.py`: expose the existing secure atomic JSON writer for both auth modules.
- Modify `dj_digger/cli.py`: preserve SoundCloud auth syntax and add nested Spotify login/status/logout commands.
- Modify `dj_digger/gates.py`: validate Hypeddit prerequisites, execute Spotify artist actions, mirror the current minimal download payload, and report rejections.
- Modify `dj_digger/soundcloud.py`: distinguish missing, rejected, and failed authenticated download resolution.
- Modify `dj_digger/tui/widgets.py`: render literal error text and a visible close label.
- Modify `dj_digger/tui/downloads.py`: publish `0%` before resolution.
- Modify `dj_digger/tui/__init__.py`: stop the application logger from writing behind the live TUI and restore it on exit.
- Modify `tests/test_auth.py`, `tests/test_cli.py`, `tests/test_gates.py`, `tests/test_soundcloud.py`, and `tests/test_tui.py`: regression coverage at each shared boundary.
- Modify `README.md`: document Spotify setup, scopes, limitations, and failure behavior.

### Task 1: Make the error banner literal and keep logs out of the live terminal

**Files:**
- Modify: `tests/test_tui.py:1-20`
- Modify: `tests/test_tui.py:103-132`
- Modify: `tests/test_cli.py:293-320`
- Modify: `dj_digger/tui/widgets.py:122-205`
- Modify: `dj_digger/tui/__init__.py:16-95`

- [ ] **Step 1: Write failing banner and logger tests**

Add `ErrorBanner` to the explicit TUI imports and add these tests:

```python
from dj_digger.tui import (
    AskLinkScreen,
    ConfirmScreen,
    DiggerApp,
    ErrorBanner,
    HelpScreen,
    SettingsScreen,
)


def test_error_banner_keeps_messages_literal_and_has_a_working_x(state):
    app = make_app([], state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            banner = app.query_one(ErrorBanner)
            banner.add_error("Batch failed [Artist - Track]: bad [response]")
            for index in range(12):
                banner.add_error(f"Failure {index}: " + "long message " * 8)
            await pilot.pause()

            close = app.query_one("#error-close", Button)
            message = app.query_one("#error-text", Static)
            assert str(close.label) == "X"
            assert "[Artist - Track]" in str(message.render())
            assert banner.size.height <= 12

            await pilot.click("#error-close")
            await pilot.pause()
            assert banner.errors == []
            assert not banner.has_class("visible")

    run(scenario)
```

Add this test to `tests/test_cli.py`:

```python
def test_the_tui_mutes_our_stream_logger_and_restores_it(monkeypatch):
    from dj_digger import tui

    logger = logging.getLogger("dj_digger")
    original = logger.level
    seen = []
    monkeypatch.setattr(tui.DiggerApp, "run", lambda self: seen.append(logger.level))

    tui.run_tui()

    assert seen == [logging.CRITICAL + 1]
    assert logger.level == original
```

- [ ] **Step 2: Run the tests and verify both failures**

Run:

```bash
uv run pytest tests/test_tui.py::test_error_banner_keeps_messages_literal_and_has_a_working_x tests/test_cli.py::test_the_tui_mutes_our_stream_logger_and_restores_it -q
```

Expected: the banner test reports an empty button label, and the logger test sees its original level inside `DiggerApp.run()`.

- [ ] **Step 3: Render Rich `Text`, use a literal label, and bracket `app.run()` with logger restoration**

Replace the button construction and `_update_display()` content in `ErrorBanner`:

```python
def compose(self) -> ComposeResult:
    with Horizontal(id="error-container"):
        with VerticalScroll(id="error-scroll"):
            yield Static("", id="error-text")
        yield Button("X", id="error-close", tooltip="Close error banner (clear all errors)")


def _update_display(self) -> None:
    try:
        msg_widget = self.query_one("#error-text", Static)
    except Exception:
        return
    if not self.errors:
        self.remove_class("visible")
        msg_widget.update("")
        return

    self.add_class("visible")
    content = Text(
        f"Errors / Debug Log ({len(self.errors)} total, scrollable):\n",
        style="bold yellow",
    )
    for index, message in enumerate(self.errors):
        if index:
            content.append("\n")
        content.append(f"• {message}")
    msg_widget.update(content)
```

Add `import logging` to `dj_digger/tui/__init__.py` and bracket the existing run/finally path without changing its cleanup:

```python
def run_tui(
    records: Sequence[LinkRecord] = (),
    *,
    state: TrackState | None = None,
    crate_title: str = "",
    export_format: str = "json",
    export_path: Path | None = None,
    dig_options: dig_module.DigOptions | None = None,
    crate_record: CrateRecord | None = None,
) -> None:
    app = DiggerApp(
        records,
        state=state,
        crate_title=crate_title,
        export_format=export_format,
        export_path=export_path,
        dig_options=dig_options,
        crate_record=crate_record,
    )
    logger = logging.getLogger("dj_digger")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL + 1)
    try:
        app.run()
    finally:
        logger.setLevel(previous_level)
        player = getattr(app, "player", None)
        if player is not None:
            player.close()
        client = getattr(app, "_client", None)
        if client is not None:
            client.close()
```

- [ ] **Step 4: Run focused and existing logging tests**

Run:

```bash
uv run pytest tests/test_tui.py::test_error_banner_keeps_messages_literal_and_has_a_working_x tests/test_cli.py -k 'logger or logging or stream or tui_mutes' -q
```

Expected: all selected tests pass, with no terminal output from `dj_digger` while the fake TUI runs.

- [ ] **Step 5: Commit the banner fix**

```bash
git add dj_digger/tui/widgets.py dj_digger/tui/__init__.py tests/test_tui.py tests/test_cli.py
git commit -m "fix(tui): keep batch errors readable"
```

### Task 2: Start download progress at zero

**Files:**
- Modify: `tests/test_tui.py:1783-1825`
- Modify: `dj_digger/tui/downloads.py:39-58`
- Modify: `dj_digger/tui/downloads.py:120-145`

- [ ] **Step 1: Write failing worker tests for single and batch starts**

Add the following fake and test next to the current download progress tests:

```python
class ProgressProbeClient:
    def __init__(self, app, seen, output):
        self.app = app
        self.seen = seen
        self.output = output

    def download_track(self, track, directory, **kwargs):
        self.seen.append(self.app.download_progress[track.key])
        return self.output

    def close(self):
        pass


@pytest.mark.parametrize("batch", [False, True])
def test_download_stays_at_zero_while_the_link_is_being_resolved(
    state, monkeypatch, tmp_path, batch
):
    record = LinkRecord(
        category="soundcloud",
        track=Track(
            title="Free",
            permalink_url="https://soundcloud.com/a/free",
            id=7,
            downloadable=True,
            has_downloads_left=True,
        ),
        link_url="https://soundcloud.com/a/free",
        link_text=links.FREE_DOWNLOAD,
    )
    app = make_app([record], state)
    seen = []
    app._client = ProgressProbeClient(app, seen, tmp_path / "free.mp3")

    class Session:
        def close(self):
            pass

    monkeypatch.setattr("dj_digger.tui.downloads.soundcloud.create_requests_session", Session)

    async def scenario():
        async with app.run_test():
            row = app.visible_rows[0]
            worker = (
                app.batch_download_in_background([(row, None)])
                if batch
                else app.download_track_in_background(row.track)
            )
            await worker.wait()

    run(scenario)
    assert seen == [0.0]
```

- [ ] **Step 2: Verify the tests see the hard-coded 5%**

Run:

```bash
uv run pytest tests/test_tui.py::test_download_stays_at_zero_while_the_link_is_being_resolved -q
```

Expected: both parameter cases fail with `seen == [0.05]`.

- [ ] **Step 3: Replace both initial progress values**

Change the two worker calls, and nothing else:

```python
self.call_from_thread(self._update_track_progress, key, 0.0)
```

- [ ] **Step 4: Run the progress tests**

```bash
uv run pytest tests/test_tui.py -k 'download_progress or download_stays_at_zero or batch_progress or download_results' -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit honest initial progress**

```bash
git add dj_digger/tui/downloads.py tests/test_tui.py
git commit -m "fix(tui): start downloads at zero percent"
```

### Task 3: Report the real SoundCloud direct-download failure

**Files:**
- Modify: `tests/test_soundcloud.py:10-60`
- Modify: `tests/test_soundcloud.py:63-108`
- Modify: `dj_digger/soundcloud.py:296-353`

- [ ] **Step 1: Add failing tests for the Medusa-shaped track**

Add a sequential session and three tests:

```python
class SequenceSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, dict(params or {}), kwargs))
        return self.responses.pop(0)

    def close(self):
        pass


def medusa_track():
    return Track(
        id=343075936,
        title="DLR & Safire - Medusa [FREE DOWNLOAD]",
        artist="Ant TC1",
        permalink_url="https://soundcloud.com/anttc1/dlr-safire-medusa-free-download",
        downloadable=True,
        has_downloads_left=True,
    )


def test_a_free_download_without_a_url_explains_that_login_is_required(tmp_path):
    client = SoundCloudClient(
        session=SequenceSession([]), client_id=DUMMY_CLIENT_ID, oauth_token=""
    )

    with pytest.raises(SoundCloudError, match="SoundCloud login is required"):
        client.download_track(medusa_track(), tmp_path)


def test_a_rejected_download_endpoint_explains_that_login_expired(tmp_path):
    client = SoundCloudClient(
        session=SequenceSession([FakeResponse(status_code=401)]),
        client_id=DUMMY_CLIENT_ID,
        oauth_token="expired",
    )

    with pytest.raises(SoundCloudError, match="expired or was rejected"):
        client.download_track(medusa_track(), tmp_path)


def test_a_free_download_without_a_url_uses_the_authenticated_endpoint(tmp_path):
    endpoint = FakeResponse(payload={"redirectUri": "https://cdn.example/medusa.wav"})
    audio = DownloadResponse([b"RIFF"], headers={"Content-Type": "audio/wav"})
    session = SequenceSession([endpoint, audio])
    client = SoundCloudClient(
        session=session, client_id=DUMMY_CLIENT_ID, oauth_token="valid"
    )

    path = client.download_track(medusa_track(), tmp_path)

    assert path.suffix == ".wav"
    assert path.read_bytes() == b"RIFF"
    assert session.calls[0][0].endswith("/tracks/343075936/download")
```

Give `FakeResponse` and `DownloadResponse` the attributes used by production code:

```python
class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="{}", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class DownloadResponse:
    status_code = 200

    def __init__(self, chunks, headers=None, url="https://cdn.example/file"):
        self.chunks = chunks
        self.headers = headers or {}
        self.url = url

    def iter_content(self, chunk_size):
        return iter(self.chunks)
```

- [ ] **Step 2: Verify the errors are currently generic**

```bash
uv run pytest tests/test_soundcloud.py -k 'free_download_without_a_url or rejected_download_endpoint' -q
```

Expected: the first two tests fail on the generic no-active-download message; the success test fails because the response sequence is not resolved as specified.

- [ ] **Step 3: Make authenticated resolution explicit and preserve concrete URL fallback**

Replace the resolution section before URL validation with:

```python
download_url: str | None = None

if gate_url:
    download_url = gates.resolve_gate_download_url(
        gate_url, session, timeout=self._timeout, config=self.config
    )

if not download_url and track.has_direct_download and track.download_url:
    download_url = track.download_url

if not download_url and track.free_download and track.id:
    if not self._oauth_token:
        raise SoundCloudError(
            "SoundCloud login is required for this artist-provided download; "
            "run 'dj-digger auth login'"
        )
    try:
        response = session.get(
            f"{API_ROOT}/tracks/{track.id}/download",
            params={"client_id": self.client_id},
            headers={"Authorization": f"OAuth {self._oauth_token}"},
            timeout=self._timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise SoundCloudError(f"SoundCloud download resolution failed: {exc}") from exc
    if response.status_code in (401, 403):
        raise SoundCloudError(
            "The saved SoundCloud login expired or was rejected; "
            "run 'dj-digger auth login' again"
        )
    if response.status_code not in (200, 302):
        raise SoundCloudError(
            f"SoundCloud returned HTTP {response.status_code} while resolving the download"
        )
    if response.status_code == 302:
        download_url = response.headers.get("Location")
    else:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SoundCloudError("SoundCloud returned an unreadable download reply") from exc
        download_url = payload.get("redirectUri") or payload.get("url")

if not download_url:
    if gate_url:
        raise SoundCloudError(
            f"Gate link requires browser completion ({gate_url}) - press 'o' to open"
        )
    raise SoundCloudError("This track has no active direct download or resolved gate link")
```

- [ ] **Step 4: Run every SoundCloud download test**

```bash
uv run pytest tests/test_soundcloud.py -k download -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the SoundCloud diagnosis fix**

```bash
git add dj_digger/soundcloud.py tests/test_soundcloud.py
git commit -m "fix(download): explain SoundCloud authentication failures"
```

### Task 4: Add secure Spotify token persistence, refresh, and library actions

**Files:**
- Modify: `dj_digger/auth.py:64-99`
- Modify: `tests/test_auth.py:65-94`
- Create: `dj_digger/spotify.py`
- Create: `tests/test_spotify.py`

- [ ] **Step 1: Write failing tests for reusable private JSON persistence**

Add this test to `tests/test_auth.py`:

```python
def test_private_json_writer_is_atomic_and_owner_only(tmp_path):
    target = tmp_path / "credentials" / "spotify.json"

    auth.write_private_json(target, {"refresh_token": "secret"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"refresh_token": "secret"}
    assert list(target.parent.glob(".auth-*")) == []
    if os.name == "posix":
        assert os.stat(target).st_mode & 0o777 == 0o600
        assert os.stat(target.parent).st_mode & 0o777 == 0o700
```

Add `import json` to the test module.

- [ ] **Step 2: Verify the shared writer is missing**

```bash
uv run pytest tests/test_auth.py::test_private_json_writer_is_atomic_and_owner_only -q
```

Expected: failure because `auth.write_private_json` does not exist.

- [ ] **Step 3: Extract the existing secure write path and reuse it for SoundCloud**

Add this function and reduce `save_token()` to one call:

```python
def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError as exc:
        LOGGER.debug("Could not tighten permissions on %s: %s", directory, exc)

    descriptor, temporary = tempfile.mkstemp(
        dir=str(directory), prefix=".auth-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def save_token(token: str, username: str = "", user_id: int | None = None) -> None:
    write_private_json(
        AUTH_FILE,
        {
            "oauth_token": token.strip(),
            "username": username,
            "user_id": user_id,
        },
    )
```

- [ ] **Step 4: Write failing Spotify credential and API tests**

Create `tests/test_spotify.py`:

```python
import json
import os
import time

import pytest

from dj_digger import spotify


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class Session:
    def __init__(self, post=None, put=None):
        self.post_response = post
        self.put_response = put
        self.posts = []
        self.puts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.post_response

    def put(self, url, **kwargs):
        self.puts.append((url, kwargs))
        return self.put_response


def test_spotify_credentials_are_owner_only(tmp_path, monkeypatch):
    path = tmp_path / "spotify.json"
    monkeypatch.setattr(spotify, "AUTH_FILE", path)

    spotify.save_credentials({"client_id": "client", "refresh_token": "refresh"})

    assert spotify.load_credentials()["refresh_token"] == "refresh"
    if os.name == "posix":
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_an_expired_access_token_is_refreshed_and_saved(tmp_path, monkeypatch):
    path = tmp_path / "spotify.json"
    monkeypatch.setattr(spotify, "AUTH_FILE", path)
    spotify.save_credentials(
        {
            "client_id": "client",
            "refresh_token": "refresh",
            "access_token": "old",
            "expires_at": 1,
            "scope": spotify.SCOPE,
        }
    )
    session = Session(
        post=Response(
            payload={"access_token": "new", "expires_in": 3600, "scope": spotify.SCOPE}
        )
    )

    assert spotify.access_token(session=session) == "new"
    assert spotify.load_credentials()["access_token"] == "new"
    assert session.posts[0][1]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh",
        "client_id": "client",
    }


def test_artist_uris_are_saved_with_the_minimum_scope(tmp_path, monkeypatch):
    path = tmp_path / "spotify.json"
    monkeypatch.setattr(spotify, "AUTH_FILE", path)
    spotify.save_credentials(
        {
            "client_id": "client",
            "refresh_token": "refresh",
            "access_token": "access",
            "expires_at": time.time() + 3600,
            "scope": spotify.SCOPE,
        }
    )
    session = Session(put=Response(status_code=200))

    spotify.save_uris(["spotify:artist:0oVDzp5DK2caqb6FuL2mhp"], session=session)

    url, kwargs = session.puts[0]
    assert url == "https://api.spotify.com/v1/me/library"
    assert kwargs["params"] == {"uris": "spotify:artist:0oVDzp5DK2caqb6FuL2mhp"}
    assert kwargs["headers"] == {"Authorization": "Bearer access"}


def test_spotify_failures_never_include_the_response_body(tmp_path, monkeypatch):
    path = tmp_path / "spotify.json"
    monkeypatch.setattr(spotify, "AUTH_FILE", path)
    spotify.save_credentials(
        {
            "client_id": "client",
            "refresh_token": "refresh",
            "access_token": "access",
            "expires_at": time.time() + 3600,
            "scope": spotify.SCOPE,
        }
    )
    session = Session(put=Response(status_code=403, payload={"token": "do-not-print"}))

    with pytest.raises(spotify.SpotifyError, match="HTTP 403") as error:
        spotify.save_uris(["spotify:artist:0oVDzp5DK2caqb6FuL2mhp"], session=session)

    assert "do-not-print" not in str(error.value)
```

- [ ] **Step 5: Verify the Spotify module is absent**

```bash
uv run pytest tests/test_spotify.py -q
```

Expected: collection fails because `dj_digger.spotify` does not exist.

- [ ] **Step 6: Implement the minimal credential, refresh, and library client**

Create `dj_digger/spotify.py` with:

```python
"""Spotify PKCE credentials and the one library action used by Hypeddit gates."""

import json
import time
from pathlib import Path
from typing import Any

import requests

from .auth import CONFIG_DIR, write_private_json

AUTH_FILE = CONFIG_DIR / "spotify.json"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_ROOT = "https://api.spotify.com/v1"
SCOPE = "user-follow-modify"


class SpotifyError(RuntimeError):
    pass


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
        raise SpotifyError("Spotify login required; run 'dj-digger auth spotify login --client-id ...'")
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
        raise SpotifyError(f"Spotify token refresh returned HTTP {response.status_code}")
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
    if response.status_code != 200:
        raise SpotifyError(f"Spotify library request returned HTTP {response.status_code}")
```

- [ ] **Step 7: Run auth and Spotify tests**

```bash
uv run pytest tests/test_auth.py tests/test_spotify.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit the Spotify API boundary**

```bash
git add dj_digger/auth.py dj_digger/spotify.py tests/test_auth.py tests/test_spotify.py
git commit -m "feat(spotify): add secure token refresh and library actions"
```

### Task 5: Add Spotify PKCE login and CLI commands

**Files:**
- Modify: `tests/test_spotify.py`
- Modify: `tests/test_cli.py:14-99`
- Modify: `dj_digger/spotify.py`
- Modify: `dj_digger/cli.py:26-35`
- Modify: `dj_digger/cli.py:151-167`
- Modify: `dj_digger/cli.py:379-437`

- [ ] **Step 1: Write failing PKCE integration and parser tests**

Append to `tests/test_spotify.py`:

```python
import threading
import urllib.parse
import urllib.request


def test_pkce_login_opens_the_browser_validates_state_and_saves_tokens(
    tmp_path, monkeypatch
):
    path = tmp_path / "spotify.json"
    monkeypatch.setattr(spotify, "AUTH_FILE", path)
    session = Session(
        post=Response(
            payload={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "scope": spotify.SCOPE,
            }
        )
    )
    opened = []

    def opener(url, browser):
        opened.append((url, browser))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        callback = query["redirect_uri"][0]
        state = query["state"][0]

        def answer():
            urllib.request.urlopen(
                f"{callback}?code=authorization-code&state={urllib.parse.quote(state)}",
                timeout=5,
            ).read()

        threading.Thread(target=answer, daemon=True).start()
        return True

    spotify.login("client-id", browser="firefox", session=session, opener=opener)

    query = urllib.parse.parse_qs(urllib.parse.urlparse(opened[0][0]).query)
    assert opened[0][1] == "firefox"
    assert query["scope"] == [spotify.SCOPE]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"][0].startswith("http://127.0.0.1:")
    exchange = session.posts[0][1]["data"]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "authorization-code"
    assert exchange["code_verifier"]
    assert spotify.load_credentials()["refresh_token"] == "refresh"


def test_pkce_login_rejects_the_wrong_state(tmp_path, monkeypatch):
    monkeypatch.setattr(spotify, "AUTH_FILE", tmp_path / "spotify.json")
    session = Session(post=Response(payload={"access_token": "must-not-save"}))

    def opener(url, browser):
        callback = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["redirect_uri"][0]
        threading.Thread(
            target=lambda: urllib.request.urlopen(
                f"{callback}?code=authorization-code&state=wrong", timeout=5
            ).read(),
            daemon=True,
        ).start()
        return True

    with pytest.raises(spotify.SpotifyError, match="state"):
        spotify.login("client-id", session=session, opener=opener)

    assert session.posts == []
    assert spotify.load_credentials() == {}
```

Extend `tests/test_cli.py::test_auth_subcommand_parsing`:

```python
args = cli.parse_cli_args(["auth", "spotify", "login", "--client-id", "client"])
assert args.auth_action == "spotify"
assert args.spotify_action == "login"
assert args.client_id == "client"

args = cli.parse_cli_args(["auth", "spotify", "status"])
assert args.spotify_action == "status"

args = cli.parse_cli_args(["auth", "spotify", "logout"])
assert args.spotify_action == "logout"
```

Add a CLI behavior test so status and logout use the same credential store:

```python
def test_spotify_auth_status_and_logout(tmp_path, monkeypatch, capsys):
    from dj_digger import spotify

    monkeypatch.setattr(spotify, "AUTH_FILE", tmp_path / "spotify.json")
    spotify.save_credentials({"client_id": "client", "refresh_token": "refresh"})

    assert cli.handle_auth(
        argparse.Namespace(auth_action="spotify", spotify_action="status")
    ) == 0
    assert "configured" in capsys.readouterr().out

    assert cli.handle_auth(
        argparse.Namespace(auth_action="spotify", spotify_action="logout")
    ) == 0
    assert spotify.load_credentials() == {}
```

- [ ] **Step 2: Verify PKCE and nested CLI parsing are absent**

```bash
uv run pytest tests/test_spotify.py -k pkce tests/test_cli.py::test_auth_subcommand_parsing -q
```

Expected: failures because `spotify.login` and the nested `spotify` parser do not exist.

- [ ] **Step 3: Implement PKCE with one loopback callback**

Add these imports and functions to `dj_digger/spotify.py`:

```python
import base64
import hashlib
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections.abc import Callable

from . import browser as browser_module

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"


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

        def log_message(self, format: str, *args: object) -> None:
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
        raise SpotifyError(f"Spotify token exchange returned HTTP {response.status_code}")
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
```

- [ ] **Step 4: Add nested Spotify commands without breaking SoundCloud commands**

Import the module in `dj_digger/cli.py`:

```python
from . import __version__, library, links, soundcloud, spotify
```

Add the nested parser after the existing SoundCloud auth parsers:

```python
spotify_auth = auth_sub.add_parser("spotify", help="Manage Spotify gate authentication.")
spotify_sub = spotify_auth.add_subparsers(dest="spotify_action", required=True)
spotify_login = spotify_sub.add_parser("login", help="Log in to Spotify with PKCE.")
spotify_login.add_argument("--client-id", required=True, help="Spotify developer app client ID.")
spotify_sub.add_parser("status", help="Show Spotify authentication status.")
spotify_sub.add_parser("logout", help="Remove saved Spotify credentials.")
```

Put this branch first in `handle_auth()`:

```python
if action == "spotify":
    spotify_action = args.spotify_action
    if spotify_action == "login":
        spotify.login(args.client_id, browser=AppConfig().browser)
        console.print("[green]Spotify login saved.[/green]")
        return 0
    if spotify_action == "logout":
        spotify.clear_credentials()
        console.print("[green]Spotify credentials removed.[/green]")
        return 0
    credentials = spotify.load_credentials()
    if credentials.get("refresh_token"):
        console.print("[green]Spotify authentication: configured.[/green]")
    else:
        console.print("[yellow]Spotify authentication: not configured.[/yellow]")
    return 0
```

- [ ] **Step 5: Run Spotify and CLI tests**

```bash
uv run pytest tests/test_spotify.py tests/test_cli.py -k 'spotify or auth_subcommand' -q
```

Expected: all selected tests pass, and existing `auth login/status/logout` parsing remains unchanged.

- [ ] **Step 6: Commit Spotify login**

```bash
git add dj_digger/spotify.py dj_digger/cli.py tests/test_spotify.py tests/test_cli.py
git commit -m "feat(auth): add Spotify PKCE login"
```

### Task 6: Make Hypeddit prerequisites and failures explicit

**Files:**
- Modify: `tests/test_gates.py:65-220`
- Modify: `dj_digger/gates.py:7-18`
- Modify: `dj_digger/gates.py:69-231`

- [ ] **Step 1: Write failing tests for placeholder email and Spotify artist actions**

Add `pytest`, change the existing module import to
`from dj_digger import gates, spotify`, then add:

```python
import pytest


def gate_html(*, steps, spotify_value=""):
    spotify_input = (
        f'<input name="additional_sp_user_id[]" value="{spotify_value}">'
        if spotify_value
        else ""
    )
    return (
        '<html><head><meta name="csrf-token" content="tok123"></head><body>'
        '<input name="fan_gate_id" value="42">'
        '<input name="current_download_file_listner" value="gate-file">'
        f'<input name="nwSteps" value="{steps}">'
        '<input name="wrndk" value="42x9">'
        '<input name="gvf" value="0">'
        f"{spotify_input}</body></html>"
    )


def session_for_gate(page, download_payload=None):
    session = MagicMock(spec=requests.Session)
    page_response = MagicMock(status_code=200, text=page)
    session.get.return_value = page_response
    success = MagicMock(status_code=200)
    success.json.return_value = {"status": "T"}
    download = MagicMock(status_code=200)
    download.json.return_value = download_payload or {
        "download_status": True,
        "URL": "https://hypeddit.com/download/file.wav",
    }
    session.post.side_effect = [success] * 12 + [download]
    return session


def test_placeholder_email_stops_before_hypeddit_receives_any_post():
    session = session_for_gate(gate_html(steps="email,sc"))

    with pytest.raises(RuntimeError, match="real email"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/zrw7vu",
            session,
            config=StubConfig("dj-digger@example.invalid"),
        )

    session.post.assert_not_called()


def test_spotify_artist_is_saved_before_hypeddit_download(monkeypatch):
    session = session_for_gate(
        gate_html(steps="sc,sp", spotify_value="ART|0oVDzp5DK2caqb6FuL2mhp")
    )
    saved = []
    monkeypatch.setattr(spotify, "save_uris", lambda uris: saved.extend(uris))

    result = resolve_hypeddit_download_url(
        "https://hypeddit.com/track/xngfus",
        session,
        config=StubConfig("dj@example.com", gate_social_actions=True),
    )

    assert saved == ["spotify:artist:0oVDzp5DK2caqb6FuL2mhp"]
    assert result == "https://hypeddit.com/download/file.wav"


def test_spotify_step_respects_the_social_actions_switch(monkeypatch):
    session = session_for_gate(
        gate_html(steps="sc,sp", spotify_value="ART|0oVDzp5DK2caqb6FuL2mhp")
    )
    monkeypatch.setattr(
        spotify,
        "save_uris",
        lambda uris: pytest.fail("Spotify must not be changed"),
    )

    with pytest.raises(RuntimeError, match="social actions"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/xngfus",
            session,
            config=StubConfig("dj@example.com", gate_social_actions=False),
        )

    session.post.assert_not_called()


def test_unknown_spotify_gate_action_stays_manual(monkeypatch):
    session = session_for_gate(gate_html(steps="sp", spotify_value="PLAYLIST|abc"))

    with pytest.raises(RuntimeError, match="unsupported Spotify action"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/unknown",
            session,
            config=StubConfig("dj@example.com"),
        )

    session.post.assert_not_called()
```

- [ ] **Step 2: Verify the resolver currently posts the placeholder and ignores Spotify**

```bash
uv run pytest tests/test_gates.py -k 'placeholder_email_stops or spotify_artist or spotify_step_respects or unknown_spotify' -q
```

Expected: all new tests fail for the missing prerequisite checks and Spotify call.

- [ ] **Step 3: Parse prerequisites once and perform the declared Spotify action**

Import the module:

```python
from . import spotify
```

Immediately after building `inputs`, add:

```python
steps = [
    step.strip()
    for step in inputs.get("nwSteps", "email,sc").split(",")
    if step.strip()
]
if "email" in steps and not config.has_real_email():
    raise RuntimeError(
        "Hypeddit requires a real email; set it in Settings before downloading"
    )
if "sp" in steps:
    if not getattr(config, "gate_social_actions", True):
        raise RuntimeError(
            "This Hypeddit gate requires Spotify social actions, but they are disabled"
        )
    spotify_uris = []
    for tag in soup.find_all("input", attrs={"name": "additional_sp_user_id[]"}):
        raw = str(tag.get("value") or "")
        kind, separator, spotify_id = raw.partition("|")
        if kind != "ART" or not separator or not re.fullmatch(r"[A-Za-z0-9]{22}", spotify_id):
            raise RuntimeError("Hypeddit requested an unsupported Spotify action")
        spotify_uris.append(f"spotify:artist:{spotify_id}")
    if not spotify_uris:
        raise RuntimeError("Hypeddit declared Spotify without an artist to follow")
    spotify.save_uris(spotify_uris)
```

Build only the completion calls declared by the page. This avoids submitting an
email to `xngfus`, whose `sc,sp` gate does not ask for one, and keeps the page's
step order:

```python
step_calls = []
if "email" in steps:
    step_calls.append(
        (
            "https://hypeddit.com/verifyEmailAddress",
            {
                "_token": csrf_token,
                "validateEmailAddress": email,
                "fan_gate_id": fan_gate_id,
                "email_name": name,
            },
        )
    )
if "sc" in steps:
    step_calls.append(
        (
            "https://hypeddit.com/setSC",
            {
                "_token": csrf_token,
                "fan_gate_id": fan_gate_id,
                "comment_sc": sc_comment,
                "is_repost": int(social),
                "is_subscribe": int(social),
            },
        )
    )
if "yt" in steps:
    step_calls.append(
        (
            "https://hypeddit.com/setYT",
            {
                "_token": csrf_token,
                "fan_gate_id": fan_gate_id,
                "comment_yt": sc_comment,
            },
        )
    )
for step in steps:
    step_calls.append(
        (
            "https://hypeddit.com/setGatePathway",
            {"_token": csrf_token, "fan_gate_id": fan_gate_id, "stepName": step},
        )
    )
    step_calls.append(
        (
            "https://hypeddit.com/setGatePathwayOr",
            {
                "_token": csrf_token,
                "fan_gate_id": fan_gate_id,
                "skipSteps": "",
                "selectedStep": step,
            },
        )
    )
```

- [ ] **Step 4: Validate email replies and use the current minimal Hypeddit payload**

Replace the ignored step response loop with:

```python
for endpoint, payload in step_calls:
    try:
        step_response = session.post(
            endpoint, data=payload, headers=ajax_headers, timeout=timeout
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Hypeddit step request failed: {exc}") from exc
    if step_response.status_code >= 400:
        raise RuntimeError(
            f"Hypeddit step returned HTTP {step_response.status_code}"
        )
    if endpoint.endswith("verifyEmailAddress"):
        try:
            accepted = step_response.json().get("status") == "T"
        except (ValueError, AttributeError):
            accepted = False
        if not accepted:
            raise RuntimeError("Hypeddit rejected the configured email address")
```

Use this download request body, matching the fields read by Hypeddit's current
`gate-ul-preview.js` while omitting empty analytics and unrelated platform arrays:

```python
download_response = session.post(
    "https://hypeddit.com/gate/download/ul",
    data={
        "_token": csrf_token,
        "file": download_key,
        "download_visit": "true",
        "profile_downloads": "true",
        "time": 0,
        "sc_comment_text": sc_comment,
        "yt_comment_text": sc_comment,
        "page": "nonsingle",
        "is_skippable": inputs.get("is_skippable", "0"),
        "steps": inputs.get("nwSteps", ""),
        "email": email,
        "download_action": "DOWNLOAD",
        "skip_gate_steps": [],
        "wrndk": inputs.get("wrndk", ""),
        "is_mobile": inputs.get("is_mobile", ""),
        "additional_sp_user_id": [
            tag.get("value", "")
            for tag in soup.find_all(
                "input", attrs={"name": "additional_sp_user_id[]"}
            )
        ],
        "external_id": extern_id,
        "hypesource": inputs.get("hypesource", ""),
        "adcode": inputs.get("adcode", ""),
        "gvf": inputs.get("gvf", "0"),
    },
    headers=ajax_headers,
    timeout=timeout,
)
if download_response.status_code != 200:
    raise RuntimeError(
        f"Hypeddit download returned HTTP {download_response.status_code}"
    )
try:
    result = download_response.json()
except ValueError as exc:
    raise RuntimeError("Hypeddit returned an unreadable download reply") from exc
if not isinstance(result, dict) or not result.get("download_status"):
    raise RuntimeError("Hypeddit did not unlock the download")
cleaned = _clean_url(result.get("URL"))
if not cleaned:
    raise RuntimeError("Hypeddit unlocked the gate without a safe download URL")
return cleaned
```

- [ ] **Step 5: Adjust the fake post sequence and run all gate tests**

Make `session_for_gate()` dispatch by endpoint instead of relying on call count:

```python
def session_for_gate(page, download_payload=None):
    session = MagicMock(spec=requests.Session)
    session.get.return_value = MagicMock(status_code=200, text=page)

    def post(url, **kwargs):
        response = MagicMock(status_code=200)
        if url.endswith("verifyEmailAddress"):
            response.json.return_value = {"status": "T"}
        elif url.endswith("/gate/download/ul"):
            response.json.return_value = download_payload or {
                "download_status": True,
                "URL": "https://hypeddit.com/download/file.wav",
            }
        else:
            response.json.return_value = {}
        return response

    session.post.side_effect = post
    return session
```

Run:

```bash
uv run pytest tests/test_gates.py -q
```

Expected: all tests pass. Existing social-action tests remain green after their
stub responses return valid JSON for an email step.

- [ ] **Step 6: Commit Hypeddit prerequisite handling**

```bash
git add dj_digger/gates.py tests/test_gates.py
git commit -m "fix(gates): complete declared Hypeddit prerequisites"
```

### Task 7: Lock in the Hypeddit router behavior

**Files:**
- Modify: `tests/test_gates.py:223-327`

- [ ] **Step 1: Add the exact `l87679`-shaped regression fixture**

Add:

```python
def test_hypeddit_smart_link_keeps_only_its_beatport_destination():
    page = """
    <html><head><title>Whiplash EP by Sota</title></head><body>
      <a class="hype-btn" href="https://www.beatport.com/release/whiplash/3629013">Buy</a>
      <a href="https://open.spotify.com/album/stream-only">Listen</a>
    </body></html>
    """

    found = store_links_on_page(
        "https://hypeddit.com/l87679",
        HubSession(page, landed="https://hypeddit.com/l87679"),
    )

    assert found == [
        ("https://www.beatport.com/release/whiplash/3629013", "Buy")
    ]
```

- [ ] **Step 2: Run the regression test and confirm production already passes**

```bash
uv run pytest tests/test_gates.py::test_hypeddit_smart_link_keeps_only_its_beatport_destination -q
```

Expected: PASS without a production change. This records the already-correct
behavior observed against the live page instead of rewriting it.

- [ ] **Step 3: Commit only the regression test**

```bash
git add tests/test_gates.py
git commit -m "test(gates): cover Hypeddit Beatport routers"
```

### Task 8: Document setup and run the release gate

**Files:**
- Modify: `README.md:20-26`
- Modify: `README.md:90-110`
- Modify: `README.md:140-154`

- [ ] **Step 1: Add concise Spotify setup documentation**

Add this section after the SoundCloud authentication instructions:

````markdown
### Spotify-backed download gates

Some Hypeddit gates require following an artist on Spotify. Register a Spotify
developer application, add a loopback redirect URI for `http://127.0.0.1/callback`,
then log in once:

```bash
dj-digger auth spotify login --client-id YOUR_CLIENT_ID
dj-digger auth spotify status
```

The login uses Authorization Code with PKCE and requests only
`user-follow-modify`. Credentials stay in an owner-only local file. Disable
**gate social actions** in Settings to prevent the program from changing Spotify
or SoundCloud; gates requiring those actions will then remain manual.

Spotify development applications currently require the app owner to have
Premium and are limited to five allowlisted users. Each dj-digger user should
therefore supply their own developer client ID. The redirect must use the
literal loopback address `127.0.0.1`, not `localhost`.

Remove the login with:

```bash
dj-digger auth spotify logout
```
````

- [ ] **Step 2: Run formatting and focused suites**

```bash
uv run ruff check dj_digger tests
uv run pytest tests/test_auth.py tests/test_cli.py tests/test_gates.py tests/test_soundcloud.py tests/test_spotify.py tests/test_tui.py -q
```

Expected: Ruff reports no errors and all selected offline tests pass.

- [ ] **Step 3: Run the complete offline suite**

```bash
uv run pytest
```

Expected: the complete offline suite passes without network access or writes to
`~/.local/share/dj-digger`.

- [ ] **Step 4: Inspect the final diff and secret handling**

```bash
git diff --check
git diff --stat HEAD~8
rg -n "access_token|refresh_token|code_verifier" dj_digger tests README.md
```

Expected: no whitespace errors; token values are written only to the owner-only
credential file or sent in authorization headers/form bodies, and no error path
formats a token or response body into user-visible text.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md
git commit -m "docs: explain Spotify-backed download gates"
```

- [ ] **Step 6: Record final verification**

```bash
git status --short
git log -10 --oneline
```

Expected: the worktree is clean and the eight implementation commits follow the
already-committed design and plan documents.

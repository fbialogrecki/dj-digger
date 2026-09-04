import json
import os
from contextlib import contextmanager
from threading import Event

import pytest
from conftest import FakeResponse

from dj_digger import auth, browser_session, private_json


def _browser(monkeypatch, context):
    """The private browser opens on ``context``; the profile path is ignored."""

    @contextmanager
    def sync_browser_context(_profile, **_kwargs):
        yield context

    monkeypatch.setattr(browser_session, "sync_browser_context", sync_browser_context)


def _no_browser(monkeypatch):
    monkeypatch.setattr(
        browser_session,
        "sync_browser_context",
        lambda *_args, **_kwargs: pytest.fail("browser was opened"),
    )


def test_private_json_writer_is_atomic_and_owner_only(tmp_path):
    target = tmp_path / "credentials" / "auth.json"

    private_json.write_private_json(target, {"refresh_token": "secret"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "refresh_token": "secret"
    }
    assert list(target.parent.glob(".auth-*")) == []
    if os.name == "posix":
        assert os.stat(target).st_mode & 0o777 == 0o600
        assert os.stat(target.parent).st_mode & 0o777 == 0o700


def test_chromium_login_refuses_a_busy_private_profile(monkeypatch):
    _no_browser(monkeypatch)
    assert auth.BROWSER_PROFILE_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(auth.SoundCloudAuthError, match="profile is already in use"):
            auth.login_with_chromium("client")
    finally:
        auth.BROWSER_PROFILE_LOCK.release()


def test_get_stored_token_from_env(monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_OAUTH_TOKEN", "env-token-123")
    assert auth.get_stored_token() == "env-token-123"


def test_save_and_get_stored_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_OAUTH_TOKEN", "")
    auth_file = tmp_path / "auth.json"
    monkeypatch.setattr(auth, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path)

    assert auth.get_stored_token() is None

    auth.save_token("my-oauth-token", username="testuser", user_id=999)
    assert auth.get_stored_token() == "my-oauth-token"

    info = auth.get_stored_auth_info()
    assert info.get("username") == "testuser"
    assert info.get("user_id") == 999

    # Check 0600 file permissions on Unix
    if os.name == "posix":
        mode = os.stat(auth_file).st_mode & 0o777
        assert mode == 0o600

    auth.clear_token()
    assert auth.get_stored_token() is None
    assert not auth_file.exists()


def test_verify_token_valid(monkeypatch):
    monkeypatch.setattr(
        auth.requests,
        "get",
        lambda url, headers=None, timeout=10.0: FakeResponse(200, {"username": "valid_user", "id": 123}),
    )
    user = auth.verify_token("token_abc", "dummy_client")
    assert user is not None
    assert user["username"] == "valid_user"


def test_verify_token_invalid(monkeypatch):
    monkeypatch.setattr(
        auth.requests,
        "get",
        lambda url, headers=None, timeout=10.0: FakeResponse(401),
    )
    assert auth.verify_token("invalid_token", "dummy_client") is None


def test_the_token_file_is_owner_only_without_relying_on_a_later_chmod(tmp_path, monkeypatch):
    """The old code chmod'd after writing, so the token was briefly world readable.

    Neutering ``os.chmod`` proves the 0600 comes from how the file is created
    rather than from a narrowing that happens once the secret is already on disk.
    """

    monkeypatch.setenv("SOUNDCLOUD_OAUTH_TOKEN", "")
    auth_file = tmp_path / "cfg" / "auth.json"
    monkeypatch.setattr(auth, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth, "CONFIG_DIR", auth_file.parent)
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)

    auth.save_token("secret-token")

    assert auth.get_stored_token() == "secret-token"
    if os.name == "posix":
        assert os.stat(auth_file).st_mode & 0o777 == 0o600


def test_saving_a_token_leaves_no_temporary_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUNDCLOUD_OAUTH_TOKEN", "")
    auth_file = tmp_path / "cfg" / "auth.json"
    monkeypatch.setattr(auth, "AUTH_FILE", auth_file)
    monkeypatch.setattr(auth, "CONFIG_DIR", auth_file.parent)

    auth.save_token("secret-token")

    assert list(auth_file.parent.glob(".auth-*")) == []
    if os.name == "posix":
        assert os.stat(auth_file.parent).st_mode & 0o777 == 0o700


def test_soundcloud_browser_profile_is_private_and_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    profile = auth.soundcloud_browser_profile_path()

    assert profile == tmp_path / "dj-digger" / "soundcloud-browser"
    if os.name == "posix":
        assert os.stat(profile).st_mode & 0o777 == 0o700


def test_browser_login_reads_only_oauth_token_and_saves_after_verification(monkeypatch):
    saved = []

    class Page:
        def goto(self, url, **_kwargs):
            assert url == auth.SOUNDCLOUD_SIGN_IN_URL

    class Context:
        pages = [Page()]

        def cookies(self, urls):
            assert urls == ["https://soundcloud.com"]
            return [
                {"name": "session", "value": "do-not-read"},
                {"name": "oauth_token", "value": "secret-token"},
            ]

    _browser(monkeypatch, Context())
    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token, client_id: {"username": "DJ", "id": 7}
        if (token, client_id) == ("secret-token", "client")
        else None,
    )
    monkeypatch.setattr(auth, "save_token", lambda *args: saved.append(args))

    result = auth.login_with_chromium("client", cancel=Event())

    assert result == ("secret-token", "DJ", 7)
    assert saved == [("secret-token", "DJ", 7)]


def test_rejected_browser_cookie_is_ignored_until_login_changes_it(monkeypatch):
    cookies = iter(["rejected", "working"])
    saved = []

    class Context:
        pages = [type("Page", (), {"goto": lambda self, *_args, **_kwargs: None})()]

        def cookies(self, _urls):
            return [{"name": "oauth_token", "value": next(cookies)}]

    _browser(monkeypatch, Context())
    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token, _client_id: {"username": "DJ", "id": 4}
        if token == "working"
        else None,
    )
    monkeypatch.setattr(auth, "save_token", lambda *args: saved.append(args))

    result = auth.login_with_chromium("client", cancel=Event())

    assert result == ("working", "DJ", 4)
    assert saved == [("working", "DJ", 4)]


def test_browser_login_times_out_without_saving_any_cookie(monkeypatch):
    class Context:
        pages = [type("Page", (), {"goto": lambda self, *_args, **_kwargs: None})()]

        def cookies(self, _urls):
            return []

    _browser(monkeypatch, Context())
    monkeypatch.setattr(
        auth, "save_token", lambda *_args: pytest.fail("missing token was saved")
    )

    with pytest.raises(auth.SoundCloudAuthError, match="timed out"):
        auth.login_with_chromium("client", timeout=0)


def test_browser_login_honours_cancellation_before_opening_browser(monkeypatch):
    _no_browser(monkeypatch)
    cancel = Event()
    cancel.set()

    with pytest.raises(auth.SoundCloudAuthCancelled):
        auth.login_with_chromium("client", cancel=cancel)


def test_browser_profile_permission_failure_is_a_safe_auth_error(monkeypatch):
    monkeypatch.setattr(
        auth,
        "soundcloud_browser_profile_path",
        lambda: (_ for _ in ()).throw(PermissionError("private filesystem detail")),
    )

    _no_browser(monkeypatch)
    with pytest.raises(auth.SoundCloudAuthError, match="Could not start") as caught:
        auth.login_with_chromium("client")

    assert "private filesystem detail" not in str(caught.value)


def test_browser_login_installs_missing_playwright_chromium_once(monkeypatch):
    attempts = []
    installs = []

    class Context:
        pages = [type("Page", (), {"goto": lambda self, *_args, **_kwargs: None})()]

        def cookies(self, _urls):
            return [{"name": "oauth_token", "value": "token"}]

    @contextmanager
    def browser_context(_profile):
        attempts.append(True)
        if len(attempts) == 1:
            raise browser_session.ChromiumMissing("missing")
        yield Context()

    monkeypatch.setattr(browser_session, "sync_browser_context", browser_context)
    monkeypatch.setattr(browser_session, "install_chromium", lambda cancel: installs.append(cancel))
    monkeypatch.setattr(
        auth, "verify_and_save", lambda token, client_id: (token, "DJ", 1)
    )

    assert auth.login_with_chromium("client") == ("token", "DJ", 1)
    assert len(attempts) == 2
    assert len(installs) == 1

import json
import os
from contextlib import contextmanager
from threading import Event

import pytest
from conftest import FakeResponse

from dj_digger import auth, automation_errors, private_json


class _Page:
    def __init__(self):
        self.visited = []
        self.closed = False

    def goto(self, url, **_kwargs):
        self.visited.append(url)

    def is_closed(self):
        return self.closed


class _Context:
    """The work window of ``webpage.session``: cookies, new_page, show."""

    def __init__(self, tokens=()):
        self.tokens = iter(tokens)
        self.last = None
        self.shown = False
        self.closed = False
        self.page = _Page()

    def is_closed(self):
        return self.closed

    def cookies(self, urls):
        assert urls == ["https://soundcloud.com"]
        self.last = next(self.tokens, self.last)
        return [{"name": "oauth_token", "value": self.last}] if self.last else []

    def new_page(self):
        assert self.shown, "the window must be shown before the sign-in page opens"
        return self.page

    def show(self):
        self.shown = True


def _browser(monkeypatch, context):
    """``webpage.session`` yields ``context``; the opened visibility is recorded."""

    opened = []

    @contextmanager
    def session(visible=False, listener=None):
        opened.append(visible)
        yield context

    monkeypatch.setattr(auth.webpage, "session", session)
    return opened


def _no_browser(monkeypatch):
    monkeypatch.setattr(
        auth.webpage,
        "session",
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


def _verify(monkeypatch, accepted, saved):
    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token, _client_id: {"username": "DJ", "id": 7} if token in accepted else None,
    )
    monkeypatch.setattr(auth, "save_token", lambda *args: saved.append(args))


def test_a_token_already_in_the_browser_is_saved_without_showing_a_window(monkeypatch):
    saved = []
    context = _Context(["secret-token"])
    opened = _browser(monkeypatch, context)
    _verify(monkeypatch, {"secret-token"}, saved)

    result = auth.login_with_browser("client", cancel=Event())

    assert result == ("secret-token", "DJ", 7)
    assert saved == [("secret-token", "DJ", 7)]
    assert opened == [False] and not context.shown and context.page.visited == []


def test_browser_login_reads_only_oauth_token_after_the_sign_in_page(monkeypatch):
    saved = []

    class Context(_Context):
        def cookies(self, urls):
            super().cookies(urls)
            return [
                {"name": "session", "value": "do-not-read"},
                *([{"name": "oauth_token", "value": "secret-token"}] if self.shown else []),
            ]

    context = Context()
    _browser(monkeypatch, context)
    _verify(monkeypatch, {"secret-token"}, saved)

    result = auth.login_with_browser("client", cancel=Event())

    assert result == ("secret-token", "DJ", 7)
    assert saved == [("secret-token", "DJ", 7)]
    assert context.page.visited == [auth.SOUNDCLOUD_SIGN_IN_URL]


def test_a_rejected_browser_token_is_ignored_until_login_changes_it(monkeypatch):
    saved, messages = [], []
    context = _Context(["stale", "stale", "working"])
    _browser(monkeypatch, context)
    _verify(monkeypatch, {"working"}, saved)

    result = auth.login_with_browser("client", cancel=Event(), status=messages.append)

    assert result == ("working", "DJ", 7)
    assert saved == [("working", "DJ", 7)]
    assert context.shown
    assert not any("no longer valid" in message for message in messages)  # the stale one was only skipped
    assert all("Chromium" not in message for message in messages)


def test_a_token_rejected_after_sign_in_is_reported_and_not_retried(monkeypatch):
    saved, messages = [], []
    context = _Context([None, "bad", "bad", "working"])
    _browser(monkeypatch, context)
    _verify(monkeypatch, {"working"}, saved)

    result = auth.login_with_browser("client", cancel=Event(), status=messages.append)

    assert result[0] == "working"
    assert sum("no longer valid" in message for message in messages) == 1


def test_browser_login_times_out_without_saving_any_cookie(monkeypatch):
    _browser(monkeypatch, _Context())
    monkeypatch.setattr(
        auth, "save_token", lambda *_args: pytest.fail("missing token was saved")
    )

    with pytest.raises(auth.SoundCloudAuthError, match="timed out"):
        auth.login_with_browser("client", timeout=0)


@pytest.mark.parametrize("what", ["page", "context"])
def test_closing_the_sign_in_tab_or_window_stops_the_wait(monkeypatch, what):
    class Context(_Context):
        def cookies(self, urls):
            result = super().cookies(urls)
            if self.shown:  # the user closes the sign-in tab (or its window) while the wait runs
                (self.page if what == "page" else self).closed = True
            return result

    _browser(monkeypatch, Context())
    monkeypatch.setattr(auth, "save_token", lambda *_args: pytest.fail("nothing to save"))
    started = auth.time.monotonic()

    with pytest.raises(auth.SoundCloudAuthError, match="sign-in tab was closed; no credentials were saved"):
        auth.login_with_browser("client", timeout=300)

    assert auth.time.monotonic() - started < 5  # not the five-minute timeout


def test_a_token_that_appears_as_the_tab_closes_is_still_used(monkeypatch):
    saved = []

    class Context(_Context):
        def cookies(self, urls):
            if self.shown:
                self.page.closed = True
            return [{"name": "oauth_token", "value": "secret-token"}] if self.shown else []

    _browser(monkeypatch, Context())
    _verify(monkeypatch, {"secret-token"}, saved)

    assert auth.login_with_browser("client")[0] == "secret-token"


def test_browser_login_honours_cancellation_before_opening_browser(monkeypatch):
    _no_browser(monkeypatch)
    cancel = Event()
    cancel.set()

    with pytest.raises(auth.SoundCloudAuthCancelled):
        auth.login_with_browser("client", cancel=cancel)


def test_cancelling_while_waiting_for_the_sign_in_raises_cancelled(monkeypatch):
    _browser(monkeypatch, _Context())

    class Cancel(Event):
        def wait(self, _timeout=None):
            self.set()
            return True

    cancel = Cancel()

    with pytest.raises(auth.SoundCloudAuthCancelled):
        auth.login_with_browser("client", cancel=cancel)


def test_a_missing_extension_tells_the_user_to_connect_it(monkeypatch):
    @contextmanager
    def session(**_kwargs):
        raise auth.link.ExtensionMissing("not connected")
        yield

    monkeypatch.setattr(auth.webpage, "session", session)

    with pytest.raises(auth.SoundCloudAuthError, match="Connect the dj-digger browser extension"):
        auth.login_with_browser("client")


def test_an_application_diagnosed_failure_is_reported_with_its_reason(monkeypatch):
    @contextmanager
    def session(**_kwargs):
        raise automation_errors.AutomationError("browser extension restarted")
        yield

    monkeypatch.setattr(auth.webpage, "session", session)

    with pytest.raises(auth.SoundCloudAuthError, match="browser extension restarted"):
        auth.login_with_browser("client")


def test_browser_login_hides_unexpected_browser_text(monkeypatch, caplog):
    @contextmanager
    def crashed(**_kwargs):
        raise RuntimeError("page secret")
        yield

    monkeypatch.setattr(auth.webpage, "session", crashed)
    with pytest.raises(auth.SoundCloudAuthError, match="Could not use your browser") as caught:
        auth.login_with_browser("client")
    assert "page secret" not in str(caught.value) and "page secret" not in caplog.text
    assert "RuntimeError" in caplog.text

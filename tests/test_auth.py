import json
import os

from dj_digger import auth


def test_private_json_writer_is_atomic_and_owner_only(tmp_path):
    target = tmp_path / "credentials" / "spotify.json"

    auth.write_private_json(target, {"refresh_token": "secret"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "refresh_token": "secret"
    }
    assert list(target.parent.glob(".auth-*")) == []
    if os.name == "posix":
        assert os.stat(target).st_mode & 0o777 == 0o600
        assert os.stat(target.parent).st_mode & 0o777 == 0o700


class DummyResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


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
        lambda url, headers=None, timeout=10.0: DummyResponse(200, {"username": "valid_user", "id": 123}),
    )
    user = auth.verify_token("token_abc", "dummy_client")
    assert user is not None
    assert user["username"] == "valid_user"


def test_verify_token_invalid(monkeypatch):
    monkeypatch.setattr(
        auth.requests,
        "get",
        lambda url, headers=None, timeout=10.0: DummyResponse(401),
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

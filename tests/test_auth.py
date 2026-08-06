from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from dj_digger import auth


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

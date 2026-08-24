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

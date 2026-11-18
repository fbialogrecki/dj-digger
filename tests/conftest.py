import json
from pathlib import Path
from typing import Any

import pytest

from dj_digger import auth, config, db
from dj_digger.models import Track

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    """A requests-shaped reply: status, headers, text, and a json() that can fail."""

    def __init__(self, status_code=200, payload=None, text="{}", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def close(self):
        pass

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

class MemoryKeyring:
    """A system keyring stand-in: the real one holds the developer's own login."""

    priority = 1

    def __init__(self):
        self.items = {}

    def get_password(self, service, entry):
        return self.items.get((service, entry))

    def set_password(self, service, entry, secret):
        self.items[(service, entry)] = secret

    def delete_password(self, service, entry):
        del self.items[(service, entry)]


@pytest.fixture
def system_keyring(monkeypatch):
    """The keyring every test gets (set up by isolate_user_data), for tests that look inside."""
    return auth._keyring()


@pytest.fixture(autouse=True)
def isolate_user_data(tmp_path, monkeypatch):
    """Never let a test read or write the real crate library or status file.

    Config and auth are in here too: ``AppConfig()`` writes a default file the
    first time it cannot find one, and ``SoundCloudClient`` reads auth.json on
    construction - so without this a test run edits the developer's own settings
    and can pick up their live OAuth token.

    And the scan folders: left alone they default to ~/Music and ~/Downloads, so
    a test that scans would walk the developer's actual music collection.
    """

    # Not "music": macOS and Windows have case-insensitive filesystems, so this
    # directory and a test's own tmp_path/"Music" are the same one there, and
    # whichever ran second failed with FileExistsError. A name no test would
    # reach for by accident keeps that from happening again.
    scan_dir = tmp_path / "isolated-scan-root"
    scan_dir.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"scan_directories": [str(scan_dir)]}), encoding="utf-8"
    )

    # Everything that goes through paths.data_dir() / config_dir() - the store
    # browser profile, cart diagnostics - lands under tmp_path as well; a test
    # run once left eight diagnostics folders in the developer's real data dir.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    from dj_digger import paths
    from dj_digger.logging_setup import close_logging
    monkeypatch.setattr(paths, 'log_dir', lambda: tmp_path / 'logs')
    # logging_setup imports the function directly; cover every platform here.
    monkeypatch.setattr('dj_digger.logging_setup.log_dir', lambda: tmp_path / 'logs')
    monkeypatch.setattr(config, "default_config_path", lambda: config_path)
    monkeypatch.setattr(db, "default_db_path", lambda: tmp_path / "digger.db")
    monkeypatch.setattr(auth, "CONFIG_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth, "AUTH_FILE", tmp_path / "auth" / "auth.json")
    memory = MemoryKeyring()
    monkeypatch.setattr(auth, "_keyring", lambda: memory)
    monkeypatch.setattr(auth, "_keyring_cache", None)
    monkeypatch.setattr(auth, "_migration_tried", False)
    yield
    close_logging()
    for instance in list(db._DATABASES):
        instance.close()
    db._INSTANCES.clear()


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def track_payloads() -> list[dict[str, Any]]:
    """Four real tracks, trimmed, covering every categorisation branch."""

    return load_fixture("tracks.json")


@pytest.fixture
def tracks(track_payloads: list[dict[str, Any]]) -> list[Track]:
    return [Track.from_api(payload) for payload in track_payloads]


@pytest.fixture
def tracks_by_id(tracks: list[Track]) -> dict[int, Track]:
    return {track.id: track for track in tracks if track.id}


@pytest.fixture
def playlist_payload() -> dict[str, Any]:
    """A real /resolve reply: full envelope, id-only track stubs."""

    return load_fixture("playlist_resolve.json")


@pytest.fixture(autouse=True)
def offline_requests_only(request, monkeypatch):
    """An omitted fake must fail locally instead of reaching a provider."""
    if any(request.node.get_closest_marker(name) for name in ("live", "shop_live", "hypeddit_live")):
        return
    import requests
    def blocked(*args, **kwargs):
        raise AssertionError("Offline test attempted an unfaked HTTP request")
    monkeypatch.setattr(requests.Session, "send", blocked)

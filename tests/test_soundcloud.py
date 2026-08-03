from __future__ import annotations

import pytest

from dj_digger import soundcloud
from dj_digger.models import Track
from dj_digger.soundcloud import SoundCloudClient, SoundCloudError

DUMMY_CLIENT_ID = "0" * 32


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="{}"):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)

    def close(self):
        pass


class DownloadResponse:
    status_code = 200

    def __init__(self, chunks):
        self.chunks = chunks

    def iter_content(self, chunk_size):
        return iter(self.chunks)


class DownloadSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls.append((url, dict(params or {}), timeout, stream))
        return self.response

    def close(self):
        pass


def make_client(session=None):
    return SoundCloudClient(session=session or FakeSession([]), client_id=DUMMY_CLIENT_ID)


def test_download_uses_only_the_artist_provided_download_url(tmp_path):
    session = DownloadSession(DownloadResponse([b"first", b"", b"second"]))
    client = make_client(session)
    track = Track(
        title="A track / live",
        artist="Artist",
        permalink_url="https://soundcloud.com/artist/track",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://api-v2.soundcloud.com/tracks/1/download",
    )

    path = client.download_track(track, tmp_path)

    assert path.name == "Artist - A track live.mp3"
    assert path.read_bytes() == b"firstsecond"
    assert session.calls == [
        (
            track.download_url,
            {"client_id": DUMMY_CLIENT_ID},
            20.0,
            True,
        )
    ]


def track_payload(track_id):
    return {
        "kind": "track",
        "id": track_id,
        "title": f"Track {track_id}",
        "permalink_url": f"https://soundcloud.com/artist/{track_id}",
        "user": {"username": "Artist"},
    }


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://soundcloud.com/a/sets/b", True),
        ("https://www.soundcloud.com/a", True),
        ("http://soundcloud.com/a", True),
        ("https://example.com/a", False),
        ("soundcloud.com/a", False),
        ("playlist.html", False),
        ("https://notsoundcloud.com/a", False),
    ],
)
def test_is_soundcloud_url(url, expected):
    assert soundcloud.is_soundcloud_url(url) is expected


@pytest.mark.parametrize(
    "url,collection,base",
    [
        ("https://soundcloud.com/someone/likes", "likes", "https://soundcloud.com/someone"),
        ("https://soundcloud.com/someone/tracks", "tracks", "https://soundcloud.com/someone"),
        ("https://soundcloud.com/someone/reposts", "reposts", "https://soundcloud.com/someone"),
        # A playlist has three segments and must be left alone.
        (
            "https://soundcloud.com/someone/sets/a-set",
            None,
            "https://soundcloud.com/someone/sets/a-set",
        ),
        ("https://soundcloud.com/someone", None, "https://soundcloud.com/someone"),
    ],
)
def test_split_user_collection(url, collection, base):
    assert soundcloud.split_user_collection(url) == (collection, base)


def test_hydrate_batches_at_fifty(monkeypatch):
    """The /tracks endpoint answers 400 above 50 ids, so chunking is load-bearing."""

    client = make_client()
    chunks = []

    def fake_get(path, **params):
        assert path == "/tracks"
        ids = [int(value) for value in params["ids"].split(",")]
        chunks.append(ids)
        return [track_payload(track_id) for track_id in ids]

    monkeypatch.setattr(client, "_get", fake_get)
    client.hydrate_tracks(list(range(1, 121)))

    assert [len(chunk) for chunk in chunks] == [50, 50, 20]


def test_hydrate_restores_playlist_order(monkeypatch):
    client = make_client()

    def fake_get(path, **params):
        ids = [int(value) for value in params["ids"].split(",")]
        # The real endpoint returns tracks in its own order.
        return [track_payload(track_id) for track_id in reversed(ids)]

    monkeypatch.setattr(client, "_get", fake_get)
    tracks = client.hydrate_tracks([9, 4, 7, 1])

    assert [track.id for track in tracks] == [9, 4, 7, 1]


def test_hydrate_tolerates_tracks_the_api_drops(monkeypatch):
    """Deleted or geo-blocked tracks simply do not come back."""

    client = make_client()

    def fake_get(path, **params):
        ids = [int(value) for value in params["ids"].split(",")]
        return [track_payload(track_id) for track_id in ids if track_id != 2]

    monkeypatch.setattr(client, "_get", fake_get)
    tracks = client.hydrate_tracks([1, 2, 3])

    assert [track.id for track in tracks] == [1, 3]


def test_hydrate_reports_progress(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client,
        "_get",
        lambda path, **params: [
            track_payload(int(value)) for value in params["ids"].split(",")
        ],
    )

    seen = []
    client.hydrate_tracks(list(range(1, 61)), on_progress=lambda done, total: seen.append((done, total)))

    assert seen == [(50, 60), (60, 60)]


def test_hydrate_of_nothing_makes_no_requests(monkeypatch):
    client = make_client()
    monkeypatch.setattr(client, "_get", lambda *a, **k: pytest.fail("should not request"))
    assert client.hydrate_tracks([]) == []


def test_collect_playlist_hydrates_every_stub(monkeypatch, playlist_payload):
    client = make_client()
    monkeypatch.setattr(client, "resolve", lambda url: playlist_payload)

    requested = []

    def fake_hydrate(ids, on_progress=None):
        requested.extend(ids)
        return [Track.from_api(track_payload(track_id)) for track_id in ids]

    monkeypatch.setattr(client, "hydrate_tracks", fake_hydrate)
    crate = client.collect("https://soundcloud.com/a/sets/b")

    assert len(requested) == len(playlist_payload["tracks"])
    assert len(crate.tracks) == len(requested)
    assert crate.declared_count == playlist_payload["track_count"]
    assert crate.title == playlist_payload["title"]


def test_collect_playlist_honours_limit(monkeypatch, playlist_payload):
    client = make_client()
    monkeypatch.setattr(client, "resolve", lambda url: playlist_payload)
    monkeypatch.setattr(client, "hydrate_tracks", lambda ids, on_progress=None: list(ids))

    crate = client.collect("https://soundcloud.com/a/sets/b", limit=7)
    assert len(crate.tracks) == 7


def test_collect_single_track(monkeypatch):
    client = make_client()
    monkeypatch.setattr(client, "resolve", lambda url: track_payload(42))

    crate = client.collect("https://soundcloud.com/artist/42")
    assert [track.id for track in crate.tracks] == [42]


def test_collect_user_likes_unwraps_and_paginates(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client, "resolve", lambda url: {"kind": "user", "id": 7, "username": "Someone"}
    )

    pages = {
        "/users/7/likes": {
            "collection": [{"kind": "like", "track": track_payload(1)}],
            "next_href": "https://api-v2.soundcloud.com/users/7/likes?offset=1",
        },
        "https://api-v2.soundcloud.com/users/7/likes?offset=1": {
            "collection": [{"kind": "like", "track": track_payload(2)}],
            "next_href": None,
        },
    }
    monkeypatch.setattr(client, "_get", lambda path, **params: pages[path])
    monkeypatch.setattr(client, "_request", lambda url, params=None: pages[url])

    crate = client.collect("https://soundcloud.com/someone/likes")

    assert [track.id for track in crate.tracks] == [1, 2]
    assert crate.title == "Someone - likes"


def test_collect_user_profile_defaults_to_their_tracks(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client, "resolve", lambda url: {"kind": "user", "id": 7, "username": "Someone"}
    )
    requested = []

    def fake_get(path, **params):
        requested.append(path)
        return {"collection": [track_payload(1)], "next_href": None}

    monkeypatch.setattr(client, "_get", fake_get)
    crate = client.collect("https://soundcloud.com/someone")

    assert requested == ["/users/7/tracks"]
    assert [track.id for track in crate.tracks] == [1]


def test_collect_rejects_things_that_are_not_diggable(monkeypatch):
    client = make_client()
    monkeypatch.setattr(client, "resolve", lambda url: {"kind": "group"})

    with pytest.raises(SoundCloudError, match="group"):
        client.collect("https://soundcloud.com/groups/x")


def test_request_refreshes_a_rejected_client_id(monkeypatch):
    session = FakeSession(
        [FakeResponse(401), FakeResponse(200, payload={"kind": "playlist"})]
    )
    client = make_client(session)
    monkeypatch.setattr(client, "_discover_client_id", lambda force=False: "1" * 32)

    assert client.resolve("https://soundcloud.com/a/sets/b") == {"kind": "playlist"}
    assert session.calls[0][1]["client_id"] == DUMMY_CLIENT_ID
    assert session.calls[1][1]["client_id"] == "1" * 32


def test_request_gives_a_useful_message_on_404():
    client = make_client(FakeSession([FakeResponse(404)]))
    with pytest.raises(SoundCloudError, match="private"):
        client.resolve("https://soundcloud.com/a/sets/missing")


def test_request_reports_other_http_errors():
    client = make_client(FakeSession([FakeResponse(500)]))
    with pytest.raises(SoundCloudError, match="500"):
        client.resolve("https://soundcloud.com/a/sets/b")


def test_client_id_regex_matches_both_bundle_styles():
    assert soundcloud.CLIENT_ID_RE.search('client_id:"' + "a" * 32 + '"').group(1) == "a" * 32
    assert soundcloud.CLIENT_ID_RE.search("client_id=" + "b" * 32).group(1) == "b" * 32

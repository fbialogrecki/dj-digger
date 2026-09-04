import threading

import pytest
from conftest import FakeResponse

from dj_digger import gates, soundcloud
from dj_digger.models import Cancelled, Track
from dj_digger.soundcloud import (
    SoundCloudClient,
    SoundCloudError,
    SoundCloudLoginRequired,
    SoundCloudTokenRejected,
)

DUMMY_CLIENT_ID = "0" * 32


class FakeSession:
    """Answers queued responses in order, recording (url, params, kwargs)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, dict(params or {}), kwargs))
        return self.responses.pop(0)

    def close(self):
        pass


class DownloadResponse:
    status_code = 200

    def __init__(self, chunks, headers=None):
        self.chunks = chunks
        self.headers = headers or {}

    def iter_content(self, chunk_size):
        return iter(self.chunks)

    def close(self):
        pass


class DownloadSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False, **kwargs):
        self.calls.append((url, dict(params or {}), timeout, stream))
        return self.response

    def close(self):
        pass


def make_client(session=None):
    return SoundCloudClient(session=session or FakeSession([]), client_id=DUMMY_CLIENT_ID)


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
        session=FakeSession([]), client_id=DUMMY_CLIENT_ID, oauth_token=""
    )

    with pytest.raises(SoundCloudLoginRequired, match="SoundCloud login is required"):
        client.download_track(medusa_track(), tmp_path)


def test_a_rejected_download_endpoint_explains_that_login_expired(tmp_path):
    client = SoundCloudClient(
        session=FakeSession([FakeResponse(status_code=401)]),
        client_id=DUMMY_CLIENT_ID,
        oauth_token="expired",
    )

    with pytest.raises(SoundCloudTokenRejected, match="expired or was rejected"):
        client.download_track(medusa_track(), tmp_path)


def test_a_free_download_without_a_url_uses_the_authenticated_endpoint(tmp_path):
    endpoint = FakeResponse(payload={"redirectUri": "https://cdn.example/medusa.wav"})
    audio = DownloadResponse([b"RIFF"], headers={"Content-Type": "audio/wav"})
    session = FakeSession([endpoint, audio])
    client = SoundCloudClient(
        session=session, client_id=DUMMY_CLIENT_ID, oauth_token="valid"
    )

    path = client.download_track(medusa_track(), tmp_path)

    assert path.suffix == ".wav"
    assert path.read_bytes() == b"RIFF"
    assert session.calls[0][0].endswith("/tracks/343075936/download")


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
            (20.0, 20.0),
            True,
        )
    ]


def test_a_lookalike_host_is_not_handed_our_client_id(tmp_path):
    """'soundcloud.com' in host is also true of soundcloud.com.attacker.net."""

    session = DownloadSession(DownloadResponse([b"x"]))
    client = make_client(session)
    track = Track(
        title="A track",
        artist="Artist",
        permalink_url="https://soundcloud.com/artist/track",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://evil-soundcloud.com.attacker.example/file.mp3",
    )

    client.download_track(track, tmp_path)

    _url, params, _timeout, _stream = session.calls[0]
    assert params == {}


@pytest.mark.parametrize("name", ["CON", "aux", "Com1", "LPT9", "NUL"])
def test_a_track_named_after_a_windows_device_still_gets_a_filename(name):
    """CON.mp3 is as reserved as CON, and the OSError lands after the download."""

    cleaned = soundcloud._download_stem(
        Track(title=name, permalink_url="https://soundcloud.com/a/t")
    )
    assert cleaned.upper() not in soundcloud.WINDOWS_RESERVED
    assert name.lower() in cleaned.lower()


def test_a_gate_answering_with_a_web_page_is_not_saved_as_a_track(tmp_path):
    """A 200 full of HTML used to become a .mp3 that no player could open."""

    session = DownloadSession(
        DownloadResponse([b"<html>complete the steps</html>"], headers={"Content-Type": "text/html"})
    )
    client = make_client(session)
    track = Track(
        title="T",
        artist="A",
        permalink_url="https://soundcloud.com/a/t",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://gate.example/file",
    )
    destination = tmp_path / "downloads"

    with pytest.raises(soundcloud.SoundCloudError, match="web page"):
        client.download_track(track, destination)

    assert list(destination.iterdir()) == []


def test_html_is_rejected_even_when_the_gate_lies_about_its_content_type(tmp_path):
    session = DownloadSession(
        DownloadResponse(
            [b"  <!doctype html><html>not unlocked</html>"],
            headers={"Content-Type": "audio/mpeg"},
        )
    )
    client = make_client(session)
    track = Track(
        title="T",
        artist="A",
        permalink_url="https://soundcloud.com/a/t",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://gate.example/file",
    )

    with pytest.raises(soundcloud.SoundCloudError, match="web page"):
        client.download_track(track, tmp_path / "downloads")


def test_gate_derived_html_keeps_a_typed_protocol_failure(tmp_path, monkeypatch):
    session = DownloadSession(
        DownloadResponse(
            [b"<html>foreign recommendation</html>"],
            headers={"Content-Type": "text/html"},
        )
    )
    client = make_client(session)
    track = Track(title="T", artist="A", permalink_url="https://soundcloud.com/a/t")
    monkeypatch.setattr(
        "dj_digger.gates.resolve_gate_download_url",
        lambda *_args, **_kwargs: "https://www.dropbox.com/foreign.wav?dl=0",
    )

    with pytest.raises(gates.GateProtocolChanged, match="web page"):
        client.download_track(
            track,
            tmp_path / "downloads",
            gate_url="https://hypeddit.com/track/current",
        )

    assert list((tmp_path / "downloads").iterdir()) == []


def test_a_public_download_cannot_redirect_to_localhost(tmp_path):
    redirect = DownloadResponse([], headers={"Location": "http://127.0.0.1/private"})
    redirect.status_code = 302
    session = DownloadSession(redirect)
    client = make_client(session)
    track = Track(
        title="T",
        artist="A",
        permalink_url="https://soundcloud.com/a/t",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://cdn.example/file.mp3",
    )

    with pytest.raises(soundcloud.SoundCloudError, match="unsafe address"):
        client.download_track(track, tmp_path / "downloads")

    assert len(session.calls) == 1


def test_a_download_runs_on_the_session_it_was_given(tmp_path, monkeypatch):
    """A batch download hands each of its four threads one of these.

    A gate is a multi-step flow held together by its own cookies, so sharing the
    client's single session between them lets four flows overwrite each other.
    """

    seen = []

    def fake_resolve(url, session, **kwargs):
        seen.append(session)
        return "https://cdn.example/file.mp3"

    monkeypatch.setattr("dj_digger.gates.resolve_gate_download_url", fake_resolve)

    # The client's own session raises if anything reaches for it: it has no
    # queued responses. That is the point of the test.
    client = make_client(FakeSession([]))
    mine = DownloadSession(DownloadResponse([b"x"]))
    track = Track(title="T", artist="A", permalink_url="https://soundcloud.com/a/t")

    client.download_track(
        track, tmp_path / "downloads", gate_url="https://hypeddit.com/track/x", session=mine
    )

    assert seen == [mine], "the gate flow has to run on the caller's session"
    assert mine.calls, "and so does the fetch that follows it"


def test_a_download_that_never_ends_is_stopped(tmp_path, monkeypatch):
    """Content-Length is a claim; without a ceiling the loop ends when the server says so."""

    monkeypatch.setattr(soundcloud, "MAX_DOWNLOAD_BYTES", 1024)
    endless = DownloadResponse([b"a" * 512] * 10)
    session = DownloadSession(endless)
    client = make_client(session)
    track = Track(
        title="Huge",
        artist="Artist",
        permalink_url="https://soundcloud.com/artist/track",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://cdn.example/file.mp3",
    )

    # Its own directory: tmp_path also holds the isolated config from conftest.
    destination = tmp_path / "downloads"

    with pytest.raises(soundcloud.SoundCloudError, match="exceeded"):
        client.download_track(track, destination)

    # And nothing half-written is left behind.
    assert list(destination.iterdir()) == []


def test_a_download_declaring_more_than_the_limit_is_refused_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(soundcloud, "MAX_DOWNLOAD_BYTES", 1024)
    session = DownloadSession(
        DownloadResponse([b"a"], headers={"Content-Length": str(50 * 1024 * 1024)})
    )
    client = make_client(session)
    track = Track(
        title="Huge",
        artist="Artist",
        permalink_url="https://soundcloud.com/artist/track",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://cdn.example/file.mp3",
    )

    destination = tmp_path / "downloads"

    with pytest.raises(soundcloud.SoundCloudError, match="Refusing"):
        client.download_track(track, destination)

    assert list(destination.iterdir()) == []


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

    def fake_hydrate(ids, on_progress=None, cancel=None):
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
    monkeypatch.setattr(client, "hydrate_tracks", lambda ids, on_progress=None, cancel=None: list(ids))

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


def test_pagination_stops_when_cancelled(monkeypatch):
    client = make_client()
    monkeypatch.setattr(
        client, "resolve", lambda url: {"kind": "user", "id": 7, "username": "Someone"}
    )
    cancel = threading.Event()
    requested = []

    def page(path, **params):
        requested.append(path)
        return {"collection": [track_payload(len(requested))], "next_href": f"next-{len(requested)}"}

    monkeypatch.setattr(client, "_get", page)
    monkeypatch.setattr(client, "_request", lambda url, params=None: page(url))

    def stop_after_first_page(done, total):
        cancel.set()

    with pytest.raises(Cancelled):
        client.collect(
            "https://soundcloud.com/someone/likes", on_progress=stop_after_first_page, cancel=cancel
        )
    assert requested == ["/users/7/likes"], "the next page is never asked for"


def test_a_cancelled_download_leaves_no_part_file(tmp_path):
    cancel = threading.Event()
    session = DownloadSession(
        DownloadResponse([b"\xff\xfb" + b"a" * 100, b"b" * 100], headers={"Content-Type": "audio/mpeg"})
    )
    client = make_client(session)
    track = Track(
        title="T",
        artist="A",
        permalink_url="https://soundcloud.com/a/t",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://gate.example/file",
    )
    destination = tmp_path / "downloads"

    def stop_after_first_chunk(downloaded, total):
        cancel.set()

    with pytest.raises(Cancelled):
        client.download_track(track, destination, on_progress=stop_after_first_chunk, cancel=cancel)

    assert list(destination.iterdir()) == []


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

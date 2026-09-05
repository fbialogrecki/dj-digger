"""Opt-in checks against the real api-v2.

Deselected by default (see ``addopts`` in pyproject.toml). Run them with
``pytest -m live`` when you want to know whether SoundCloud changed something
under us - that is the failure mode this whole client is exposed to.

Point them at a different playlist with DJ_DIGGER_LIVE_URL if the default one
disappears.
"""

import os

import pytest

from dj_digger import links, player, soundcloud

LIVE_URL = os.environ.get(
    "DJ_DIGGER_LIVE_URL", "https://soundcloud.com/antarcticae/sets/techno-vinyl"
)

pytestmark = pytest.mark.live


def test_client_id_can_still_be_discovered():
    client = soundcloud.SoundCloudClient()
    try:
        assert len(client.client_id) == 32
    finally:
        client.close()


def test_a_long_playlist_arrives_complete_without_scrolling():
    """The reason the saved-HTML step is gone: /resolve holds every track id."""

    client = soundcloud.SoundCloudClient()
    try:
        payload = client.resolve(LIVE_URL)
        stubs = [item for item in payload["tracks"] if isinstance(item, dict)]
        assert len(stubs) == payload["track_count"]
        assert payload["track_count"] > 50, "pick a longer playlist to make this meaningful"
    finally:
        client.close()


def test_batch_hydration_still_caps_at_fifty():
    client = soundcloud.SoundCloudClient()
    try:
        ids = [item["id"] for item in client.resolve(LIVE_URL)["tracks"][:51]]
        assert len(client.hydrate_tracks(ids[:50])) > 0
        with pytest.raises(soundcloud.SoundCloudError):
            client._get("/tracks", ids=",".join(str(i) for i in ids))
    finally:
        client.close()


def test_a_track_still_offers_a_plain_mp3_and_a_waveform():
    """Audio preview rests on both of these; SoundCloud could drop either."""

    client = soundcloud.SoundCloudClient()
    try:
        track_id = client.resolve(LIVE_URL)["tracks"][0]["id"]
        payload = client.fetch_track(track_id)

        protocols = {
            (item.get("format") or {}).get("protocol")
            for item in payload["media"]["transcodings"]
        }
        assert "progressive" in protocols, f"only got {protocols}"
        assert payload.get("track_authorization")
        assert payload.get("waveform_url", "").startswith("https://")

        stream = player.resolve_stream(client, track_id)
        assert stream.url.startswith("https://")
        assert len(player.fetch_waveform(client, stream.waveform_url)) > 100
    finally:
        client.close()


def test_audio_decodes_straight_off_the_socket():
    """No file is written, so this is the whole playback path bar the sound card."""

    import miniaudio

    client = soundcloud.SoundCloudClient()
    try:
        track_id = client.resolve(LIVE_URL)["tracks"][0]["id"]
        stream = player.resolve_stream(client, track_id)
        assert stream.duration > 30

        source = player.http_source_type(miniaudio)(client.session, stream.url)
        try:
            audio = miniaudio.stream_any(
                source, source_format=miniaudio.FileFormat.MP3, frames_to_read=1024
            )
            chunk = next(audio)
            assert len(chunk) > 0
            assert max(abs(sample) for sample in chunk) > 0, "decoded silence"

            # And a Range seek lands on real audio further in.
            source.seek(0, 0)
            seeked = miniaudio.stream_any(
                source,
                source_format=miniaudio.FileFormat.MP3,
                frames_to_read=1024,
                seek_frame=int(30 * player.SAMPLE_RATE),
            )
            assert max(abs(sample) for sample in next(seeked)) > 0
        finally:
            source.close()
    finally:
        client.close()


def test_a_full_dig_produces_store_links():
    crate = soundcloud.collect_tracks(LIVE_URL, limit=50)
    assert len(crate.tracks) == 50

    records = links.categorise_all(crate.tracks)
    counts = links.count_by_category(records)
    assert sum(counts.values()) >= len(crate.tracks)
    assert counts["bandcamp"] > 0, "expected at least one bandcamp link in a vinyl playlist"


def test_a_link_hub_still_gives_up_its_shops():
    """The whole feature rests on these pages staying readable without a browser.

    ampsuite wraps every shop in its own redirect, so this covers both halves:
    reading the anchors, and following the wrappers to where they land.
    """

    from dj_digger.models import Track
    from dj_digger.services import collection as dig

    track = Track(
        title="Know Your Place",
        permalink_url="https://soundcloud.com/sonaxx/know-your-place",
        purchase_url="https://sonaxx.ampsuite.com/releases/links?id=447",
    )

    assert dig.expand_link_hubs([track]) == 1
    assert track.purchase_url is None
    categories = {record.category for record in links.categorise(track)}
    assert {"bandcamp", "beatport"} <= categories
    assert "gate" not in categories and "others" not in categories

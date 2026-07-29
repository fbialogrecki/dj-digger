"""Opt-in checks against the real api-v2.

Deselected by default (see ``addopts`` in pyproject.toml). Run them with
``pytest -m live`` when you want to know whether SoundCloud changed something
under us - that is the failure mode this whole client is exposed to.

Point them at a different playlist with DJ_DIGGER_LIVE_URL if the default one
disappears.
"""

from __future__ import annotations

import os

import pytest

from dj_digger import links, soundcloud

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


def test_a_full_dig_produces_store_links():
    crate = soundcloud.collect_tracks(LIVE_URL, limit=50)
    assert len(crate.tracks) == 50

    records = links.categorise_all(crate.tracks)
    counts = links.count_by_category(records)
    assert sum(counts.values()) >= len(crate.tracks)
    assert counts["bandcamp"] > 0, "expected at least one bandcamp link in a vinyl playlist"

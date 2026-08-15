import json

import pytest

from dj_digger.models import Track
from dj_digger.state import GOT, NEW, SKIP, TrackState


def test_unknown_tracks_start_as_new(tmp_path):
    state = TrackState(tmp_path / "state.json")
    assert state.get("12345") == NEW


def test_status_survives_a_reload(tmp_path):
    path = tmp_path / "state.json"
    TrackState(path).set("12345", GOT)
    assert TrackState(path).get("12345") == GOT


def test_marking_new_again_forgets_the_track(tmp_path):
    path = tmp_path / "state.json"
    state = TrackState(path)
    state.set("1", SKIP)
    state.set("1", NEW)

    assert state.get("1") == NEW
    assert json.loads(path.read_text(encoding="utf-8"))["tracks"] == {}


def test_rejects_a_status_it_does_not_know(tmp_path):
    with pytest.raises(ValueError, match="Unknown status"):
        TrackState(tmp_path / "state.json").set("1", "purchased")


def test_a_corrupt_state_file_is_ignored_rather_than_fatal(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    state = TrackState(path)
    assert state.get("1") == NEW

    state.set("1", GOT)
    assert TrackState(path).get("1") == GOT


def test_state_is_keyed_by_track_id_so_it_crosses_playlists(tmp_path):
    """The same track in someone else's set should read as already handled."""

    path = tmp_path / "state.json"
    in_playlist_a = Track(title="X", permalink_url="https://soundcloud.com/a/x?in=a/sets/one", id=555)
    in_playlist_b = Track(title="X", permalink_url="https://soundcloud.com/a/x?in=b/sets/two", id=555)

    state = TrackState(path)
    state.set(in_playlist_a.key, GOT)
    assert state.get(in_playlist_b.key) == GOT


def test_tracks_without_an_id_fall_back_to_their_url(tmp_path):
    track = Track(title="X", permalink_url="https://soundcloud.com/a/x")
    state = TrackState(tmp_path / "state.json")
    state.set(track.key, SKIP)
    assert state.get("https://soundcloud.com/a/x") == SKIP


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


def test_file_backed_got_remembers_its_path_and_manual_status_forgets_it(tmp_path):
    path = tmp_path / "state.json"
    audio = tmp_path / "track.wav"
    state = TrackState(path)

    state.set_local_file("1", audio)
    assert state.get("1") == GOT
    assert TrackState(path).local_file("1") == str(audio)

    state.set("1", GOT)
    assert state.get("1") == GOT
    assert state.local_file("1") is None


def test_marking_new_again_forgets_the_track(tmp_path):
    path = tmp_path / "state.json"
    state = TrackState(path)
    state.set("1", SKIP)
    state.set("1", NEW)

    assert state.get("1") == NEW
    # Read back through a second instance: the row is gone, not merely masked.
    assert TrackState(path).get("1") == NEW


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


def test_marking_a_whole_scan_writes_no_json_at_all(tmp_path):
    """This is what batched() was for: each mark rewrote the whole of state.json.

    Since 0.9 a mark is one row in SQLite, so there is nothing to hold back.
    """

    path = tmp_path / "state.json"
    state = TrackState(path)

    for index in range(20):
        state.set(str(index), GOT)

    assert not path.exists(), "state.json is not written any more"
    assert TrackState(path).get("19") == GOT


def test_reads_after_the_first_hit_no_sqlite(tmp_path, monkeypatch):
    """A repaint asks for every row's status; only the first ask may cost a query."""

    state = TrackState(tmp_path / "state.json")
    state.set("1", GOT)

    def boom(_key):
        raise AssertionError("status read went to SQLite")

    monkeypatch.setattr(state.db, "get_track_status", boom)
    monkeypatch.setattr(state.db, "get_track_local_file", boom)
    assert state.get("1") == GOT
    assert state.get("2") == NEW
    assert state.local_file("1") is None


def test_a_set_updates_the_cache_and_the_database(tmp_path):
    path = tmp_path / "state.json"
    state = TrackState(path)
    assert state.get("1") == NEW, "the mirror is loaded before the first write"

    state.set("1", SKIP)
    state.set_local_file("2", tmp_path / "two.wav")

    assert state.get("1") == SKIP
    assert state.get("2") == GOT
    fresh = TrackState(path)
    assert fresh.get("1") == SKIP
    assert fresh.local_file("2") == str(tmp_path / "two.wav")

    assert state.clear_local_file("2") is True
    assert state.get("2") == NEW
    assert state.clear_local_file("2") is False

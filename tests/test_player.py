from __future__ import annotations

import pytest

from dj_digger import player
from dj_digger.models import Track
from dj_digger.soundcloud import SoundCloudError

PROGRESSIVE = {"format": {"protocol": "progressive"}, "url": "https://api/media/prog"}
HLS = {"format": {"protocol": "hls"}, "url": "https://api/media/hls"}


class FakeClient:
    """Stands in for SoundCloudClient without touching the network."""

    def __init__(self, payload=None, authorized=None):
        self.payload = payload if payload is not None else playable_payload()
        self.authorized = authorized if authorized is not None else {"url": "https://cdn/a.mp3"}
        self.authorize_calls = []

    def fetch_track(self, track_id):
        return self.payload

    def authorize(self, url, **params):
        self.authorize_calls.append((url, params))
        return self.authorized


def playable_payload(**overrides):
    payload = {
        "id": 1,
        "policy": "ALLOW",
        "streamable": True,
        "track_authorization": "token-123",
        "waveform_url": "https://wave.sndcdn.com/x.json",
        "media": {"transcodings": [HLS, PROGRESSIVE]},
    }
    payload.update(overrides)
    return payload


# Which tracks we can preview


def test_a_playable_track_has_no_complaint():
    assert player.unplayable_reason(playable_payload()) is None


def test_a_snipped_track_is_reported():
    assert "snippet" in player.unplayable_reason(playable_payload(policy="SNIP"))


def test_an_unstreamable_track_is_reported():
    assert "not streamable" in player.unplayable_reason(playable_payload(streamable=False))


def test_hls_only_is_reported():
    payload = playable_payload(media={"transcodings": [HLS]})
    assert "plain MP3" in player.unplayable_reason(payload)


# Resolving the stream


def test_resolve_picks_progressive_and_passes_the_authorisation():
    client = FakeClient()
    url, waveform_url = player.resolve_stream(client, 1)

    assert url == "https://cdn/a.mp3"
    assert waveform_url == "https://wave.sndcdn.com/x.json"
    assert client.authorize_calls == [
        ("https://api/media/prog", {"track_authorization": "token-123"})
    ]


def test_resolve_refuses_a_snipped_track():
    with pytest.raises(SoundCloudError, match="snippet"):
        player.resolve_stream(FakeClient(playable_payload(policy="SNIP")), 1)


def test_resolve_complains_when_no_url_comes_back():
    with pytest.raises(SoundCloudError, match="stream URL"):
        player.resolve_stream(FakeClient(authorized={}), 1)


# Waveform


def test_the_waveform_is_squashed_to_the_asked_width():
    samples = list(range(100))
    assert len(str(player.render_waveform(samples, 20))) == 20
    assert len(str(player.render_waveform(samples, 7))) == 7


def test_a_missing_waveform_draws_a_flat_line():
    assert str(player.render_waveform([], 5)) == "\u2500" * 5


def test_the_played_part_is_styled_differently():
    rendered = player.render_waveform([100] * 10, 10, played_fraction=0.5)
    played = [span for span in rendered.spans if span.style == "cyan"]
    todo = [span for span in rendered.spans if span.style == "bright_black"]

    assert len(played) == 5
    assert len(todo) == 5
    assert max(span.end for span in played) <= min(span.start for span in todo)


@pytest.mark.parametrize("fraction,expected_played", [(0.0, 0), (0.5, 5), (1.0, 10)])
def test_the_progress_boundary_follows_the_fraction(fraction, expected_played):
    rendered = player.render_waveform([100] * 10, 10, played_fraction=fraction)
    assert len([span for span in rendered.spans if span.style == "cyan"]) == expected_played


def test_a_fraction_outside_the_range_is_clamped():
    for fraction in (-5.0, 7.0):
        rendered = player.render_waveform([100] * 4, 4, played_fraction=fraction)
        assert len(str(rendered)) == 4


def test_loud_and_quiet_samples_map_to_different_glyphs():
    rendered = str(player.render_waveform([1, 140], 2))
    assert rendered[0] != rendered[1]


def test_zero_width_is_not_a_crash():
    assert str(player.render_waveform([1, 2, 3], 0)) == ""


def test_fetch_waveform_of_nothing_is_empty():
    assert player.fetch_waveform(FakeClient(), "") == []


# Clock


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0:00"), (5, "0:05"), (65, "1:05"), (432, "7:12"), (-3, "0:00")],
)
def test_format_time(seconds, expected):
    assert player.format_time(seconds) == expected


# Player state without an audio device


def test_a_fresh_player_reports_nothing_loaded():
    subject = player.Player()
    assert subject.loaded is None
    assert subject.position == 0.0
    assert subject.duration == 0.0
    assert subject.fraction == 0.0
    assert subject.playing is False


def test_controls_on_an_empty_player_do_nothing():
    subject = player.Player()
    subject.play()
    subject.seek(30)
    subject.nudge(10)
    subject.toggle()
    assert subject.playing is False


def test_volume_is_clamped_and_mute_is_reversible():
    subject = player.Player()
    subject.set_volume(2.0)
    assert subject.volume == 1.0
    subject.set_volume(-1.0)
    assert subject.volume == 0.0

    subject.set_volume(0.5)
    subject.toggle_mute()
    assert subject.volume == 0.0
    subject.toggle_mute()
    assert subject.volume == 0.5


def test_changing_volume_unmutes():
    subject = player.Player()
    subject.toggle_mute()
    subject.set_volume(0.4)
    assert subject.volume == 0.4


def test_a_missing_miniaudio_is_reported_not_raised_raw(monkeypatch):
    def no_miniaudio():
        raise player.PlaybackUnavailable("Audio preview needs miniaudio")

    monkeypatch.setattr(player, "_import_miniaudio", no_miniaudio)
    with pytest.raises(player.PlaybackUnavailable, match="miniaudio"):
        player.Player().load(Track(title="t", permalink_url="u"), player.Path("x.mp3"))


def test_download_reports_a_bad_response(tmp_path):
    class Failing:
        status_code = 500

    class Client:
        session = type("S", (), {"get": staticmethod(lambda url, timeout=None: Failing())})()

    with pytest.raises(SoundCloudError, match="500"):
        player.download_stream(Client(), "https://cdn/x.mp3", tmp_path / "x.mp3")


def test_download_writes_the_bytes(tmp_path):
    class Ok:
        status_code = 200
        content = b"id3-and-some-audio"

    class Client:
        session = type("S", (), {"get": staticmethod(lambda url, timeout=None: Ok())})()

    path = player.download_stream(Client(), "https://cdn/x.mp3", tmp_path / "deep" / "x.mp3")
    assert path.read_bytes() == b"id3-and-some-audio"

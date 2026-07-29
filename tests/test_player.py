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
    stream = player.resolve_stream(client, 1)

    assert stream.url == "https://cdn/a.mp3"
    assert stream.waveform_url == "https://wave.sndcdn.com/x.json"
    assert client.authorize_calls == [
        ("https://api/media/prog", {"track_authorization": "token-123"})
    ]


def test_resolve_reads_the_duration_off_the_payload():
    """Nothing is written to disk, so duration cannot be measured from a file."""

    client = FakeClient(playable_payload(full_duration=273000, duration=270000))
    assert player.resolve_stream(client, 1).duration == pytest.approx(273.0)


def test_duration_falls_back_when_there_is_no_full_duration():
    client = FakeClient(playable_payload(duration=200500))
    assert player.resolve_stream(client, 1).duration == pytest.approx(200.5)


def test_resolve_refuses_a_snipped_track():
    with pytest.raises(SoundCloudError, match="snippet"):
        player.resolve_stream(FakeClient(playable_payload(policy="SNIP")), 1)


def test_resolve_complains_when_no_url_comes_back():
    with pytest.raises(SoundCloudError, match="stream URL"):
        player.resolve_stream(FakeClient(authorized={}), 1)


# Waveform


def test_the_waveform_is_squashed_to_the_asked_width():
    samples = list(range(100))
    for width in (20, 7):
        rows = str(player.render_waveform(samples, width, rows=1)).split("\n")
        assert [len(row) for row in rows] == [width]


def test_the_waveform_is_two_rows_by_default():
    """One row of eight blocks is what made a loud master look like a rectangle."""

    rows = str(player.render_waveform(list(range(50)), 12)).split("\n")
    assert len(rows) == 2
    assert all(len(row) == 12 for row in rows)


def test_the_bottom_row_fills_before_the_top():
    rendered = str(player.render_waveform([0, 140], 2)).split("\n")
    top, bottom = rendered
    # Quiet column: nothing on top, nothing much at the bottom.
    assert top[0] == " "
    # Loud column: both rows full.
    assert top[1] == "\u2588" and bottom[1] == "\u2588"


def test_a_missing_waveform_draws_flat_lines():
    rows = str(player.render_waveform([], 5)).split("\n")
    assert rows == ["\u2500" * 5, "\u2500" * 5]


def test_the_played_part_is_styled_differently():
    rendered = player.render_waveform([100] * 10, 10, played_fraction=0.5, rows=1)
    played = [span for span in rendered.spans if span.style == "cyan"]
    todo = [span for span in rendered.spans if span.style == "bright_black"]

    assert len(played) == 5
    assert len(todo) == 5
    assert max(span.end for span in played) <= min(span.start for span in todo)


@pytest.mark.parametrize("fraction,expected_played", [(0.0, 0), (0.5, 5), (1.0, 10)])
def test_the_progress_boundary_follows_the_fraction(fraction, expected_played):
    rendered = player.render_waveform([100] * 10, 10, played_fraction=fraction, rows=1)
    assert len([span for span in rendered.spans if span.style == "cyan"]) == expected_played


def test_a_fraction_outside_the_range_is_clamped():
    for fraction in (-5.0, 7.0):
        rendered = player.render_waveform([100] * 4, 4, played_fraction=fraction, rows=1)
        assert len(str(rendered)) == 4


def test_loud_and_quiet_samples_map_to_different_glyphs():
    rendered = str(player.render_waveform([1, 140], 2, rows=1))
    assert rendered[0] != rendered[1]


def test_zero_width_is_not_a_crash():
    assert str(player.render_waveform([1, 2, 3], 0)) == ""


def test_levels_are_measured_against_the_peak():
    assert player.column_levels([140, 140], 2) == [1.0, 1.0]
    assert player.column_levels([0, 140], 2)[0] == 0.0


def test_a_loud_master_still_shows_shape():
    """Real samples for a loud track sit at 120-140 out of 140."""

    loud = [138, 140, 122, 139, 128, 140, 131, 137]
    levels = player.column_levels(loud, 8)
    # The power curve has to spread the top of the range enough to see.
    assert max(levels) - min(levels) > 0.2
    assert len(set(str(player.render_waveform(loud, 8, rows=1)))) > 2


def test_a_track_with_no_dynamics_is_not_faked_into_having_some():
    """Stretching min to max made the flattest track look the most dynamic."""

    flat = [130, 131, 130, 132, 131, 130]
    levels = player.column_levels(flat, 6)
    assert max(levels) - min(levels) < 0.1
    assert all(level > 0.8 for level in levels), "a loud flat track must still read loud"


def test_columns_average_rather_than_peak():
    """At ~16 samples per column, taking the peak pins everything to the ceiling."""

    samples = [0, 100, 0, 100]
    assert player.column_levels(samples, 2) == [0.5**player.WAVEFORM_GAMMA] * 2


def test_a_flat_waveform_does_not_divide_by_zero():
    assert player.column_levels([50, 50, 50], 3) == [1.0, 1.0, 1.0]
    assert player.column_levels([0, 0], 2) == [0.0, 0.0]


def test_column_levels_of_nothing():
    assert player.column_levels([], 5) == []
    assert player.column_levels([1, 2], 0) == []


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
        player.Player().load(
            Track(title="t", permalink_url="u"), player.Stream(url="https://cdn/x.mp3"), None
        )


# Streaming source


class FakeRaw:
    def __init__(self, data, chunk_size=None):
        self.data = data
        self.position = 0
        self.chunk_size = chunk_size

    def read(self, num_bytes):
        if self.chunk_size is not None:
            num_bytes = min(num_bytes, self.chunk_size)
        chunk = self.data[self.position : self.position + num_bytes]
        self.position += len(chunk)
        return chunk


class FakeResponse:
    def __init__(self, data, chunk_size=None):
        self.raw = FakeRaw(data, chunk_size)
        self.headers = {"Content-Length": str(len(data))}
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, data, chunk_size=None):
        self.data = data
        self.chunk_size = chunk_size
        self.requests = []

    def get(self, url, headers=None, stream=False, timeout=None):
        self.requests.append((url, dict(headers or {})))
        start = 0
        if headers and "Range" in headers:
            start = int(headers["Range"].split("=")[1].rstrip("-"))
        return FakeResponse(self.data[start:], self.chunk_size)


def test_the_source_reads_the_bytes_in_order():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, "https://cdn/x.mp3")

    assert source.read(4) == b"0123"
    assert source.read(4) == b"4567"
    assert source.offset == 8


def test_the_source_keeps_pulling_on_a_short_read():
    """A socket answers short; the decoder reads a short answer as end of file."""

    session = FakeSession(b"0123456789", chunk_size=3)
    source = player.HttpSourceMixin(session, "https://cdn/x.mp3")

    assert source.read(8) == b"01234567"


def test_the_source_reports_the_length_from_the_first_response():
    session = FakeSession(b"0123456789")
    assert player.HttpSourceMixin(session, "https://cdn/x.mp3").length == 10


def test_seeking_reissues_a_range_request():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, "https://cdn/x.mp3")
    source.read(2)

    assert source.seek(6, 0) is True  # SeekOrigin.START
    assert source.read(2) == b"67"
    assert session.requests[-1][1] == {"Range": "bytes=6-"}


def test_seeking_relative_to_the_current_position():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, "https://cdn/x.mp3")
    source.read(3)

    assert source.seek(2, 1) is True  # SeekOrigin.CURRENT
    assert source.read(1) == b"5"


def test_seeking_from_the_end():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, "https://cdn/x.mp3")

    assert source.seek(-2, 2) is True  # SeekOrigin.END
    assert source.read(2) == b"89"


def test_a_failed_seek_is_reported_not_raised():
    class Broken(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            if headers:
                raise OSError("connection reset")
            return super().get(url)

    source = player.HttpSourceMixin(Broken(b"0123456789"), "https://cdn/x.mp3")
    assert source.seek(5, 0) is False


def test_a_read_error_ends_the_stream_quietly():
    class Exploding(FakeRaw):
        def read(self, num_bytes):
            raise OSError("connection reset")

    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, "https://cdn/x.mp3")
    source._response.raw = Exploding(b"")

    assert source.read(4) == b""


def test_closing_the_source_closes_the_response():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, "https://cdn/x.mp3")
    response = source._response

    source.close()
    assert response.closed is True
    assert source._response is None

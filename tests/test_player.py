import array
import threading

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



def render_waveform(samples, width, played_fraction=0.0, rows=player.WAVEFORM_ROWS, level=0.0):
    """Compose the two real drawing stages the way the app does."""

    return player.paint_waveform(
        player.waveform_rows(samples, width, rows), played_fraction, level
    )

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
        rows = str(render_waveform(samples, width, rows=1)).split("\n")
        assert [len(row) for row in rows] == [width]


def test_the_waveform_fills_every_row_of_the_bar():
    """One row of eight blocks is what made a loud master look like a rectangle."""

    rows = str(render_waveform(list(range(50)), 12)).split("\n")
    assert len(rows) == player.WAVEFORM_ROWS
    assert all(len(row) == 12 for row in rows)


def test_the_bottom_row_fills_before_the_top():
    rendered = str(render_waveform([0, 140], 2)).split("\n")
    top, bottom = rendered[0], rendered[-1]
    # Quiet column: nothing on top, nothing much at the bottom.
    assert top[0] == " "
    # Loud column: both rows full.
    assert top[1] == "\u2588" and bottom[1] == "\u2588"


def test_a_missing_waveform_draws_flat_lines():
    rows = str(render_waveform([], 5)).split("\n")
    assert rows == ["\u2500" * 5] * player.WAVEFORM_ROWS


def styled_width(text, style):
    """How many characters carry a style, whatever it took to say so."""

    return sum(span.end - span.start for span in text.spans if span.style == style)


def test_the_played_part_is_styled_differently():
    rendered = render_waveform([100] * 10, 10, played_fraction=0.5, rows=1)

    assert styled_width(rendered, player.PLAYED_STYLE) == 5
    assert styled_width(rendered, player.UNPLAYED_STYLE) == 5
    played = [span for span in rendered.spans if span.style == player.PLAYED_STYLE]
    todo = [span for span in rendered.spans if span.style == player.UNPLAYED_STYLE]
    assert max(span.end for span in played) <= min(span.start for span in todo)


@pytest.mark.parametrize("fraction,expected_played", [(0.0, 0), (0.5, 5), (1.0, 10)])
def test_the_progress_boundary_follows_the_fraction(fraction, expected_played):
    rendered = render_waveform([100] * 10, 10, played_fraction=fraction, rows=1)
    assert styled_width(rendered, player.PLAYED_STYLE) == expected_played


def test_a_frame_costs_a_handful_of_spans_not_one_per_column():
    """Thirty frames a second is only affordable because of this."""

    rendered = render_waveform([100] * 400, 400, played_fraction=0.5, level=1.0)
    assert len(rendered.spans) <= 3 * player.WAVEFORM_ROWS


def test_the_leading_edge_brightens_with_the_level():
    quiet = render_waveform([100] * 60, 60, played_fraction=0.5, rows=1, level=0.0)
    loud = render_waveform([100] * 60, 60, played_fraction=0.5, rows=1, level=1.0)

    assert styled_width(quiet, player.GLOW_STYLES[-1]) == 0
    assert styled_width(loud, player.GLOW_STYLES[-1]) == player.GLOW_COLUMNS


def test_the_played_history_does_not_flicker_with_it():
    """Only the columns behind the playhead move; the rest is a record."""

    loud = render_waveform([100] * 60, 60, played_fraction=0.5, rows=1, level=1.0)
    assert styled_width(loud, player.PLAYED_STYLE) == 30 - player.GLOW_COLUMNS


def test_the_shape_of_a_track_is_the_same_however_it_is_coloured():
    rows = player.waveform_rows([100, 20, 140, 60], 4, rows=1)
    for level in (0.0, 0.5, 1.0):
        painted = player.paint_waveform(rows, 0.5, level)
        assert str(painted) == rows[0]


# Reading the level as a pulse


def settled(meter, level=0.3, frames=30):
    """Let the meter learn the room before anything is asked of it."""

    for _ in range(frames):
        meter.feed(level)
    return meter


def test_a_steady_sound_does_not_flicker():
    """Left to decay below what is arriving, the release halves it every frame."""

    shown = [player.LevelMeter().feed(0.435) for _ in range(8)]
    assert len(set(shown)) == 1


def test_a_hit_shows_at_once_and_falls_away_afterwards():
    meter = settled(player.LevelMeter())
    struck = meter.feed(1.0)
    after = [meter.feed(0.3) for _ in range(4)]

    assert struck == pytest.approx(1.0)
    assert after == sorted(after, reverse=True)
    assert after[-1] < struck / 2


def test_a_brickwalled_master_still_moves():
    """Measured on a real one: it lives between 0.92 and 1.00 the whole way."""

    meter = player.LevelMeter()
    shown = []
    for _ in range(40):
        shown.append(meter.feed(1.0))  # the kick
        shown += [meter.feed(0.93) for _ in range(6)]  # between kicks

    beat = shown[-14:]
    assert max(beat) - min(beat) > 0.5


def test_one_stray_transient_does_not_black_out_the_next_second():
    meter = settled(player.LevelMeter())
    meter.feed(1.0)
    recovered = [meter.feed(0.3 if index % 7 else 0.6) for index in range(60)]

    assert max(recovered[-20:]) > 0.5


def test_silence_reads_as_silence_rather_than_amplified_hiss():
    meter = player.LevelMeter()
    assert meter.feed(0.0) == 0.0
    assert max(meter.feed(0.001) for _ in range(20)) == 0.0


def test_a_new_track_starts_the_meter_again():
    meter = settled(player.LevelMeter())
    meter.feed(1.0)
    meter.reset()
    assert meter.feed(0.0) == 0.0


@pytest.mark.parametrize("level", [-1.0, 0.0, 0.4, 1.0, 5.0])
def test_the_glow_never_falls_off_the_end_of_the_palette(level):
    assert player.glow_style(level) in player.GLOW_STYLES


def test_a_fraction_outside_the_range_is_clamped():
    for fraction in (-5.0, 7.0):
        rendered = render_waveform([100] * 4, 4, played_fraction=fraction, rows=1)
        assert len(str(rendered)) == 4


def test_loud_and_quiet_samples_map_to_different_glyphs():
    rendered = str(render_waveform([1, 140], 2, rows=1))
    assert rendered[0] != rendered[1]


def test_zero_width_is_not_a_crash():
    assert str(render_waveform([1, 2, 3], 0)) == ""


def test_levels_are_measured_against_the_peak():
    assert player.column_levels([140, 140], 2) == [1.0, 1.0]
    assert player.column_levels([0, 140], 2)[0] == 0.0


def test_a_loud_master_still_shows_shape():
    """Real samples for a loud track sit at 120-140 out of 140."""

    loud = [138, 140, 122, 139, 128, 140, 131, 137]
    levels = player.column_levels(loud, 8)
    # The power curve has to spread the top of the range enough to see.
    assert max(levels) - min(levels) > 0.2
    assert len(set(str(render_waveform(loud, 8, rows=1)))) > 2


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


# Feeding the device


class FakeDevice:
    def __init__(self):
        self.started_with = None
        self.stops = 0

    def start(self, generator):
        self.started_with = generator

    def stop(self):
        self.stops += 1

    def close(self):
        pass


def test_a_device_that_will_not_start_degrades_instead_of_crashing(monkeypatch):
    """Pressing play twice in quick succession was enough, and it took the app down.

    miniaudio raises its own numbered error out of ``device.start``, which the
    interface catches nowhere - so it came out through Textual's message pump.
    """

    subject, device = loaded_player(monkeypatch)
    closed = []

    def refuse(generator):
        raise RuntimeError("failed to start audio device")

    monkeypatch.setattr(device, "start", refuse)
    monkeypatch.setattr(device, "close", lambda: closed.append(True))

    with pytest.raises(player.PlaybackUnavailable, match="would not start"):
        subject.play()

    assert subject.playing is False
    # Dropped rather than kept: the next attempt builds a fresh device instead
    # of asking the broken one again, and nothing is disabled for good.
    assert subject._device is None
    assert subject.unavailable_reason is None
    assert closed == [True]


def loaded_player(monkeypatch, chunks=None):
    """A Player wired to a fake device and a fake decoder."""

    subject = player.Player()
    device = FakeDevice()
    subject._device = device
    monkeypatch.setattr(subject, "_device_for", lambda rate, channels: device)

    def fake_inner():
        # Like miniaudio's stream_any, the first next() already yields audio.
        requested = yield array.array("h", [100] * 2048)
        while True:
            assert requested, "asked the decoder for zero frames"
            requested = yield array.array("h", [100] * 2048)

    monkeypatch.setattr(subject, "_open_stream", lambda seek_frame: fake_inner())
    subject._loaded = player.Loaded(
        track=Track(title="t", permalink_url="u"),
        stream=player.Stream(url="https://cdn/x.mp3", duration=300.0),
    )
    subject._miniaudio = object()
    return subject, device


def test_the_generator_is_primed_before_the_device_gets_it(monkeypatch):
    """miniaudio sends into the callback without starting it; its docs say we must."""

    subject, device = loaded_player(monkeypatch)
    subject.play()

    assert device.started_with is not None
    # This is the exact call miniaudio makes; on an unprimed generator it raises
    # TypeError: can't send non-None value to a just-started generator.
    chunk = device.started_with.send(1024)
    assert len(chunk) > 0


def test_a_zero_frame_request_does_not_end_playback(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.play()

    assert len(device.started_with.send(0)) > 0


def test_frames_fed_move_the_position(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.play()
    device.started_with.send(1024)

    assert subject.position > 0


def test_volume_scales_the_samples_that_reach_the_device(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.set_volume(0.25)
    subject.play()

    chunk = device.started_with.send(1024)
    assert max(chunk) == 25  # the fake decoder yields 100


def test_pausing_holds_the_position(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.play()
    device.started_with.send(1024)
    held = subject.position

    subject.pause()
    assert subject.playing is False
    assert subject.position == held
    assert device.stops == 1


def test_resuming_reuses_the_open_socket(monkeypatch):
    """Reopening costs a network round trip, so only a seek should do it."""

    subject, device = loaded_player(monkeypatch)
    subject.play()
    first = device.started_with
    subject.pause()
    subject.play()

    assert device.started_with is first


def test_seeking_drops_the_generator_so_the_next_play_reopens(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.play()
    first = device.started_with

    subject.seek(120.0)
    assert subject.position == pytest.approx(120.0)
    assert device.started_with is not first


def test_seeking_keeps_the_source_and_the_track_it_is_holding(monkeypatch):
    """Replacing it would throw the buffer away, which is the cost we removed."""

    subject, _device = loaded_player(monkeypatch)
    source = object()
    subject._source = source
    subject.play()

    subject.seek(120.0)
    assert subject._source is source


def test_the_level_follows_the_loudest_sample_going_out(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    assert subject.take_level() == 0.0

    subject.play()
    device.started_with.send(1024)
    assert subject.take_level() == pytest.approx(100 / player.FULL_SCALE)


def test_the_level_ignores_the_volume_knob(monkeypatch):
    """It is the music that should pulse, not the fader."""

    subject, device = loaded_player(monkeypatch)
    subject.set_volume(0.1)
    subject.play()
    device.started_with.send(1024)

    assert subject.take_level() == pytest.approx(100 / player.FULL_SCALE)


def test_a_chunk_is_measured_once_per_frame_not_once_per_callback(monkeypatch):
    """A callback covers a tenth of a second, which in techno always holds a kick."""

    subject, device = loaded_player(monkeypatch)
    subject.play()
    chunk = device.started_with.send(1024)  # the fake decoder yields 2048 samples

    assert len(subject._levels) == -(-len(chunk) // player.LEVEL_WINDOW)


def test_readings_come_back_oldest_first(monkeypatch):
    subject, _device = loaded_player(monkeypatch)
    subject._levels.extend([0.2, 0.9])
    assert [subject.take_level() for _ in range(2)] == [0.2, 0.9]


def test_the_last_reading_stands_until_another_arrives(monkeypatch):
    """Dropping to silence between callbacks would be a flicker, not a pulse."""

    subject, _device = loaded_player(monkeypatch)
    subject._playing = True
    subject._levels.append(0.7)

    assert subject.take_level() == 0.7
    assert subject.take_level() == 0.7


def test_pausing_flattens_the_level(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.play()
    device.started_with.send(1024)

    subject.pause()
    assert subject.take_level() == 0.0
    assert not subject._levels


def test_seeking_is_clamped_inside_the_track(monkeypatch):
    subject, _device = loaded_player(monkeypatch)
    subject.seek(9999.0)
    assert subject.position <= subject.duration
    subject.seek(-50.0)
    assert subject.position == 0.0


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
    def __init__(self, raw, declare_length=True):
        self.raw = raw
        self.headers = {"Content-Length": str(len(raw.data))} if declare_length else {}
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, data, chunk_size=None):
        self.data = data
        self.chunk_size = chunk_size
        self.declare_length = True
        self.requests = []

    def get(self, url, headers=None, stream=False, timeout=None):
        self.requests.append((url, dict(headers or {})))
        return FakeResponse(self._raw(self._from(headers)), self.declare_length)

    def _from(self, headers):
        if headers and "Range" in headers:
            return int(headers["Range"].split("=")[1].rstrip("-"))
        return 0

    def _raw(self, start):
        return FakeRaw(self.data[start:], self.chunk_size)


class SlowRaw(FakeRaw):
    """Hands over the first few bytes, then waits to be let go."""

    def __init__(self, data, hands_over, gate):
        super().__init__(data, chunk_size=hands_over)
        self.hands_over = hands_over
        self.gate = gate

    def read(self, num_bytes):
        if self.position >= self.hands_over:
            self.gate.wait(5.0)
        return super().read(num_bytes)


class SlowSession(FakeSession):
    def __init__(self, data, hands_over):
        super().__init__(data)
        self.hands_over = hands_over
        self.gate = threading.Event()

    def _raw(self, start):
        return SlowRaw(self.data[start:], self.hands_over, self.gate)


class HalfDeadRaw(FakeRaw):
    """Hands over a few bytes, then the connection drops under it."""

    def __init__(self, data, dies_after):
        super().__init__(data, chunk_size=dies_after)
        self.dies_after = dies_after

    def read(self, num_bytes):
        if self.position >= self.dies_after:
            raise OSError("connection reset")
        return super().read(num_bytes)


class HalfDeadSession(FakeSession):
    def __init__(self, data, dies_after):
        super().__init__(data)
        self.dies_after = dies_after

    def _raw(self, start):
        return HalfDeadRaw(self.data[start:], self.dies_after)


URL = "https://cdn/x.mp3"


def unbuffered(session):
    """A source for a response too big to hold, which reads over the socket."""

    return player.HttpSourceMixin(session, URL, max_buffer=0)


def test_the_source_reads_the_bytes_in_order():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, URL)

    assert source.read(4) == b"0123"
    assert source.read(4) == b"4567"
    assert source.offset == 8


def test_the_source_keeps_pulling_on_a_short_read():
    """A socket answers short; the decoder reads a short answer as end of file."""

    session = FakeSession(b"0123456789", chunk_size=3)

    for source in (player.HttpSourceMixin(session, URL), unbuffered(session)):
        assert source.read(8) == b"01234567"


def test_the_source_reports_the_length_from_the_first_response():
    session = FakeSession(b"0123456789")
    assert player.HttpSourceMixin(session, URL).length == 10


def test_seeking_relative_to_the_current_position():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, URL)
    source.read(3)

    assert source.seek(2, 1) is True  # SeekOrigin.CURRENT
    assert source.read(1) == b"5"


def test_seeking_from_the_end():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, URL)

    assert source.seek(-2, 2) is True  # SeekOrigin.END
    assert source.read(2) == b"89"


def test_closing_the_source_closes_the_response():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, URL)
    response = source._response

    source.close()
    assert response.closed is True
    assert source._response is None


# Buffering the track as it plays


def test_seeking_back_into_played_audio_costs_no_connection():
    """The whole point: `[` should not mean half a second of silence."""

    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, URL)
    source.read(10)
    requests = len(session.requests)

    assert source.seek(2, 0) is True  # SeekOrigin.START
    assert source.read(3) == b"234"
    assert len(session.requests) == requests


def test_a_read_waits_for_the_download_rather_than_reporting_the_end():
    session = SlowSession(b"0123456789", hands_over=4)
    source = player.HttpSourceMixin(session, URL)
    threading.Timer(0.05, session.gate.set).start()

    # Only four bytes existed when this was asked for.
    assert source.read(10) == b"0123456789"


def test_seeking_past_what_has_arrived_goes_and_gets_it():
    session = SlowSession(b"0123456789", hands_over=4)
    source = player.HttpSourceMixin(session, URL)
    try:
        assert source.read(4) == b"0123"

        source.seek(8, 0)
        assert source.read(2) == b"89"
        assert session.requests[-1][1] == {"Range": "bytes=8-"}
    finally:
        session.gate.set()


def test_a_download_that_dies_goes_back_to_the_socket():
    session = HalfDeadSession(b"0123456789", dies_after=4)
    source = player.HttpSourceMixin(session, URL)

    assert source.read(4) == b"0123"
    # The buffering thread is gone, so this can only come off a new connection.
    assert source.read(4) == b"4567"
    assert session.requests[-1][1] == {"Range": "bytes=4-"}


def test_a_response_too_big_to_hold_seeks_the_old_way():
    session = FakeSession(b"0123456789")
    source = player.HttpSourceMixin(session, URL, max_buffer=4)
    source.read(2)

    assert source.seek(6, 0) is True
    assert source.read(2) == b"67"
    assert session.requests[-1][1] == {"Range": "bytes=6-"}


def test_a_response_that_will_not_say_its_size_is_not_buffered():
    session = FakeSession(b"0123456789")
    session.declare_length = False
    source = player.HttpSourceMixin(session, URL)

    assert source.seek(6, 0) is True
    assert session.requests[-1][1] == {"Range": "bytes=6-"}


def test_a_failed_seek_is_reported_not_raised():
    class Broken(FakeSession):
        def get(self, url, headers=None, stream=False, timeout=None):
            if headers:
                raise OSError("connection reset")
            return super().get(url)

    assert unbuffered(Broken(b"0123456789")).seek(5, 0) is False


def test_a_read_error_ends_the_stream_quietly():
    class Exploding(FakeRaw):
        def read(self, num_bytes):
            raise OSError("connection reset")

    source = unbuffered(FakeSession(b"0123456789"))
    source._response.raw = Exploding(b"")

    assert source.read(4) == b""

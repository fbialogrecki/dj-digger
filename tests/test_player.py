import array
import threading

import pytest
from helpers import drums

from dj_digger import player, waveform
from dj_digger.beats import KickDetector
from dj_digger.models import Track
from dj_digger.services import playback
from dj_digger.soundcloud import SoundCloudError

PROGRESSIVE = {"format": {"protocol": "progressive", "mime_type": "audio/mpeg"}, "url": "https://api/media/prog"}
HLS = {"format": {"protocol": "hls", "mime_type": "audio/mpeg"}, "url": "https://api/media/hls"}


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
    assert playback.unplayable_reason(playable_payload()) is None


def test_a_snipped_track_is_reported():
    assert "snippet" in playback.unplayable_reason(playable_payload(policy="SNIP"))


def test_an_unstreamable_track_is_reported():
    assert "not streamable" in playback.unplayable_reason(playable_payload(streamable=False))


@pytest.mark.parametrize("track_id", [408281022, 88124909])
def test_blocked_tracks_from_user_logs_report_access_restriction(track_id, caplog):
    # Both returned BLOCK + streamable=True + no transcodings on 2026-09-08.
    client = FakeClient(playable_payload(id=track_id, policy="BLOCK", media={"transcodings": []}))
    with pytest.raises(SoundCloudError, match="account or region") as exc:
        playback.resolve_stream(client, track_id)
    assert "MP3" not in str(exc.value)
    assert client.authorize_calls == []
    assert f"track {track_id} (policy=BLOCK, streams=0)" in caplog.text
    assert "token-123" not in caplog.text


def test_block_policy_takes_precedence_over_stale_transcodings():
    client = FakeClient(playable_payload(policy="BLOCK"))
    with pytest.raises(SoundCloudError, match="blocks playback"):
        playback.resolve_stream(client, 1)
    assert client.authorize_calls == []


def test_missing_streams_are_distinct_from_unsupported_formats():
    assert "did not provide any audio streams" in playback.unplayable_reason(
        playable_payload(media={"transcodings": []})
    )


def test_hls_mp3_is_supported_and_resolved():
    payload = playable_payload(media={"transcodings": [HLS]})
    assert playback.unplayable_reason(payload) is None
    client = FakeClient(payload)
    assert playback.resolve_stream(client, 1).protocol == "hls"
    assert client.authorize_calls[0][0] == HLS["url"]


def test_unsupported_codec_is_reported_with_track_id_without_authorization(caplog):
    payload = playable_payload(media={"transcodings": [
        {"format": {"protocol": "hls", "mime_type": "audio/ogg"}}
    ]})
    with pytest.raises(SoundCloudError, match="supported MP3"):
        playback.resolve_stream(FakeClient(payload), 123)
    assert "track 123" in caplog.text
    assert "token-123" not in caplog.text


# Resolving the stream


def test_resolve_picks_progressive_and_passes_the_authorisation():
    client = FakeClient()
    stream = playback.resolve_stream(client, 1)

    assert stream.url == "https://cdn/a.mp3"
    assert stream.waveform_url == "https://wave.sndcdn.com/x.json"
    assert client.authorize_calls == [
        ("https://api/media/prog", {"track_authorization": "token-123"})
    ]


def test_resolve_reads_the_duration_off_the_payload():
    """Nothing is written to disk, so duration cannot be measured from a file."""

    client = FakeClient(playable_payload(full_duration=273000, duration=270000))
    assert playback.resolve_stream(client, 1).duration == pytest.approx(273.0)


def test_duration_falls_back_when_there_is_no_full_duration():
    client = FakeClient(playable_payload(duration=200500))
    assert playback.resolve_stream(client, 1).duration == pytest.approx(200.5)


def test_resolve_refuses_a_snipped_track():
    with pytest.raises(SoundCloudError, match="snippet"):
        playback.resolve_stream(FakeClient(playable_payload(policy="SNIP")), 1)


def test_resolve_complains_when_no_url_comes_back():
    with pytest.raises(SoundCloudError, match="stream URL"):
        playback.resolve_stream(FakeClient(authorized={}), 1)


# Waveform






































def test_levels_are_measured_against_the_peak():
    assert waveform.column_levels([140, 140], 2) == [1.0, 1.0]
    assert waveform.column_levels([0, 140], 2)[0] == 0.0


def test_a_loud_master_still_shows_shape():
    """Real samples for a loud track sit at 120-140 out of 140."""

    loud = [138, 140, 122, 139, 128, 140, 131, 137]
    levels = waveform.column_levels(loud, 8)
    # The power curve has to spread the top of the range enough to see.
    assert max(levels) - min(levels) > 0.2


def test_a_track_with_no_dynamics_is_not_faked_into_having_some():
    """Stretching min to max made the flattest track look the most dynamic."""

    flat = [130, 131, 130, 132, 131, 130]
    levels = waveform.column_levels(flat, 6)
    assert max(levels) - min(levels) < 0.1
    assert all(level > 0.8 for level in levels), "a loud flat track must still read loud"


def test_columns_average_rather_than_peak():
    """At ~16 samples per column, taking the peak pins everything to the ceiling."""

    samples = [0, 100, 0, 100]
    assert waveform.column_levels(samples, 2) == [0.5**waveform.WAVEFORM_GAMMA] * 2


def test_a_flat_waveform_does_not_divide_by_zero():
    assert waveform.column_levels([50, 50, 50], 3) == [1.0, 1.0, 1.0]
    assert waveform.column_levels([0, 0], 2) == [0.0, 0.0]


def test_column_levels_of_nothing():
    assert waveform.column_levels([], 5) == []
    assert waveform.column_levels([1, 2], 0) == []


def test_fetch_waveform_of_nothing_is_empty():
    assert playback.fetch_waveform(FakeClient(), "") == []


# Player state without an audio device


def test_a_fresh_player_reports_nothing_loaded():
    subject = player.Player()
    assert subject.loaded is None
    assert subject.position == 0.0
    assert subject.duration == 0.0
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
            Track(title="t", permalink_url="u"), playback.Stream(url="https://cdn/x.mp3"), None
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
    interface catches nowhere.
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
        stream=playback.Stream(url="https://cdn/x.mp3", duration=300.0),
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


def test_a_decoder_eof_is_a_finished_event_not_a_callback_runtime_error(monkeypatch):
    subject, device = loaded_player(monkeypatch)

    def finite_inner():
        requested = yield array.array("h", [100] * 2048)
        assert requested
        return

    monkeypatch.setattr(subject, "_open_stream", lambda _seek_frame: finite_inner())
    subject.play()

    assert len(device.started_with.send(1024)) > 0
    with pytest.raises(StopIteration):
        device.started_with.send(1024)

    event = subject.take_event()
    assert event is not None and event.kind == "finished"
    assert subject.playing is False


def test_a_decoder_failure_becomes_an_event_without_crossing_the_callback(monkeypatch):
    subject, device = loaded_player(monkeypatch)

    def broken_inner():
        yield array.array("h", [100] * 2048)
        raise RuntimeError("bad media")

    monkeypatch.setattr(subject, "_open_stream", lambda _seek_frame: broken_inner())
    subject.play()
    device.started_with.send(1024)
    with pytest.raises(StopIteration):
        device.started_with.send(1024)

    event = subject.take_event()
    assert event is not None and event.kind == "error"
    assert event.message == "bad media"


def test_an_old_eof_after_seek_cannot_finish_the_new_generation(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.play()
    old_generation = subject._generation

    subject.seek(20.0)
    subject._events.put(player.PlaybackEvent("finished", old_generation))

    assert subject.take_event() is None


def test_play_after_a_finished_last_track_restarts_from_zero(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject._ended = True
    subject._offset = subject.duration
    subject._frames = 0
    seeks = []
    original_open = subject._open_stream

    def record_open(seek_frame):
        seeks.append(seek_frame)
        return original_open(seek_frame)

    monkeypatch.setattr(subject, "_open_stream", record_open)
    subject.play()

    assert seeks == [0]


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


# Seeking without stale audio or a long gap, and detected hits


def test_seeking_and_stopping_start_a_fresh_output(monkeypatch):
    """A stopped device only pauses: what it had queued played again after the seek."""

    subject, device = loaded_player(monkeypatch)
    closes = []
    monkeypatch.setattr(device, "close", lambda: closes.append(True))
    # Like the real one: the device made for a play is the one later dropped.
    monkeypatch.setattr(subject, "_device_for", lambda rate, channels: setattr(subject, "_device", device) or device)
    subject.play()
    subject.pause()
    assert closes == []  # Pause keeps the queue, so resuming continues seamlessly.
    subject.play()
    subject.seek(120.0)
    assert closes == [True] and subject.playing
    subject.stop()
    assert closes == [True, True]


def test_audio_fades_in_after_every_start(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.set_volume(1.0)
    subject.play()
    chunk = device.started_with.send(1024)
    assert chunk[0] == 0 and 0 < chunk[player.FADE_SAMPLES // 2] < 100 and chunk[-1] == 100
    assert device.started_with.send(1024)[0] == 100
    subject.seek(30.0)
    assert device.started_with.send(1024)[0] == 0


def detect(audio, start=0.0):
    detector = KickDetector()
    return [kick for offset in range(0, len(audio), 4410)
            for kick in detector.feed(audio[offset:offset + 4410], start + offset / 2 / 44100)]


def test_kicks_are_found_on_time_through_a_roll():
    beat = 60 / 128
    four = [i * beat for i in range(8)]
    eighths = [four[-1] + beat + i * beat / 2 for i in range(8)]
    sixteenths = [eighths[-1] + beat / 2 + i * beat / 4 for i in range(16)]
    truth = four + eighths + sixteenths
    kicks = detect(drums(truth, truth[-1] + .5), start=30.0)
    # Every hit once, within 10 ms, timed on the track rather than from the start of playback.
    assert len(kicks) == len(truth)
    assert all(abs(kick.time - 30.0 - t) < .01 for kick, t in zip(kicks, truth))
    assert all(kick.strength > .5 for kick in kicks)


def test_a_held_bass_or_silence_is_not_a_kick():
    # Starting a held tone can produce one onset, but never repeated pulses.
    held = detect(drums([], 3, bass=.5))
    assert len(held) <= 1 and all(k.time <= .015 for k in held)
    assert detect(array.array("h", bytes(44100 * 4))) == []
    # A quiet kick after a loud drop still shows once the drop has been gone a few seconds.
    loud, quiet = drums([0.0], 6), drums([0.0], 1)
    quiet = array.array("h", (sample // 6 for sample in quiet))
    assert len(detect(loud + quiet)) == 2


def test_kicks_keep_time_with_the_start_each_chunk_is_given():
    """A source can report fewer frames than it sent; the start of each chunk resyncs the clock."""
    audio = drums([0.25, 1.25], 1.5)
    detector = KickDetector()
    kicks = detector.feed(audio[:44100], 10.0)
    # The second second is said to start 0.5 s later than its samples would put it.
    kicks += detector.feed(audio[44100:], 11.0)
    assert [kick.time for kick in kicks] == pytest.approx([10.25, 11.75], abs=.015)


def test_the_player_reads_detected_hits_around_its_position(monkeypatch):
    subject, device = loaded_player(monkeypatch)
    subject.set_volume(1.0)
    beat = 60 / 128
    audio = drums([i * beat for i in range(12)], 12 * beat)

    def decoder(frame):
        for offset in range(0, len(audio), 4410):
            yield audio[offset:offset + 4410]

    monkeypatch.setattr(subject, "_open_stream", decoder)
    subject.play()
    for _ in range(len(audio) // 4410 - 1):
        device.started_with.send(2205)
    pulses, period = subject.beats()
    # From half a second behind the decoded position, which is fed but not yet heard.
    assert pulses and pulses[0][0] >= subject.position - player.BEATS_BEHIND
    assert all(abs(time / beat - round(time / beat)) * beat < .015 for time, _ in pulses)
    assert period == pytest.approx(beat, abs=.02)


def test_the_byte_of_a_position_skips_the_id3_tag():
    class Source:
        def __init__(self, data):
            self.data, self.offset = data, 0
        def seek(self, offset, origin):
            self.offset = offset
            return True
        def read(self, count):
            return self.data[self.offset:self.offset + count]

    tagged = b"ID3\x03\x00\x00\x00\x00\x01\x00" + bytes(1000)  # A 128-byte tag after the header.
    assert player.mp3_start(Source(tagged), 50.0, 100.0, 1138) == 138 + 500
    assert player.mp3_start(Source(bytes(1000)), 25.0, 100.0, 1000) == 250
    assert player.mp3_start(Source(bytes(1000)), 500.0, 100.0, 1000) == 1000


def test_a_seek_lands_on_time_at_any_position(monkeypatch, tmp_path):
    """Opening at the byte takes a millisecond anywhere; decoding up to the target grew with it."""

    import shutil
    import subprocess
    import time

    miniaudio = pytest.importorskip("miniaudio")
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    path = tmp_path / "marker.mp3"
    made = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=150",
                           "-f", "lavfi", "-i", "sine=f=1000:d=2:r=44100", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=48",
                           "-filter_complex", "[1]aformat=channel_layouts=stereo[t];[0][t][2]concat=n=3:v=0:a=1",
                           "-c:a", "libmp3lame", "-b:a", "128k", str(path)], capture_output=True)
    if made.returncode:
        pytest.skip("FFmpeg has no MP3 encoder")
    subject = player.Player()
    device = FakeDevice()
    monkeypatch.setattr(subject, "_device_for", lambda rate, channels: device)
    source = player.http_source_type(miniaudio)(FakeSession(path.read_bytes()), URL)
    subject.load(Track(title="t", permalink_url="u"), playback.Stream(url=URL, duration=200.0), None, source)
    subject.play()
    started = time.perf_counter()
    subject.seek(149.5)
    assert time.perf_counter() - started < 0.05
    heard = 0
    while heard < 44100:
        chunk = device.started_with.send(441)
        if max(chunk, default=0) > 2000:
            break
        heard += len(chunk) // 2
    assert abs(149.5 + heard / 44100 - 150.0) < 0.1


def test_volume_scaling_matches_the_per_sample_rescale():
    chunk = array.array("h", [-32768, 32767, 0, -1, 1, 12345, -12345] * 50)
    for volume in (0.0, 0.1, 0.33, 0.8, 0.998):
        assert player.scale_volume(chunk, volume) == array.array("h", [int(s * volume) for s in chunk])

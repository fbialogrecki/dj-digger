import threading

import pytest
import requests

from dj_digger import hls_audio
from dj_digger.soundcloud_errors import SoundCloudError

BASE = "https://cf-hls-media.sndcdn.com/"
MANIFEST = b"#EXTM3U\n#EXTINF:2,\na.mp3\n#EXTINF:2,\nb.mp3\n#EXT-X-ENDLIST\n"


class Response:
    def __init__(self, body=b"", status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}
        self.closed = False

    def iter_content(self, size):
        yield self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, manifest=MANIFEST):
        self.responses = {
            BASE + "track.m3u8": Response(manifest),
            BASE + "a.mp3": Response(b"abc"),
            BASE + "b.mp3": Response(b"def"),
        }
        self.calls = []

    def get(self, url, **kwargs):
        assert kwargs == {"stream": True, "timeout": hls_audio.TIMEOUT, "allow_redirects": False}
        self.calls.append(url)
        return self.responses[url]


def test_segments_are_ordered_seekable_and_closed():
    session = Session()
    source = hls_audio.HlsSourceMixin(session, BASE + "track.m3u8")
    try:
        assert source.read(4) == b"abcd"
        assert source.seek(1, 0)
        assert source.read(5) == b"bcdef"
        assert source.read(2) == b""
        assert source.seek(-2, 2)
        assert source.read(2) == b"ef"
        assert not source.seek(-1, 0)
    finally:
        source.close()
        source._thread.join(2)
    assert all(r.closed for r in session.responses.values())


@pytest.mark.parametrize("target", ["http://cf-hls-media.sndcdn.com/a", "https://sndcdn.com.evil.test/a", "https://127.0.0.1/a", "file:///etc/passwd", "https://user:secret@cf-hls-media.sndcdn.com/a"])
def test_untrusted_segment_and_redirect_targets_are_never_requested(target):
    session = Session(MANIFEST.replace(b"a.mp3", target.encode()))
    with pytest.raises(SoundCloudError, match="untrusted"):
        hls_audio.HlsSourceMixin(session, BASE + "track.m3u8")
    assert session.calls == [BASE + "track.m3u8"]
    session = Session()
    session.responses[BASE + "track.m3u8"] = Response(status=302, headers={"Location": target})
    with pytest.raises(SoundCloudError, match="untrusted"):
        hls_audio.HlsSourceMixin(session, BASE + "track.m3u8")
    assert session.calls == [BASE + "track.m3u8"]


@pytest.mark.parametrize("tag", [b"#EXT-X-KEY:METHOD=AES-128", b"#EXT-X-MAP:URI=init.mp4", b"#EXT-X-BYTERANGE:20@0", b"#EXT-X-STREAM-INF:BANDWIDTH=128000"])
def test_other_hls_containers_are_not_misread_as_mp3(tag):
    with pytest.raises(SoundCloudError, match="not supported"):
        hls_audio.HlsSourceMixin(Session(MANIFEST.replace(b"#EXTINF:2,", tag, 1)), BASE + "track.m3u8")


def test_incomplete_playlist_and_oversized_manifest_fail(monkeypatch):
    with pytest.raises(SoundCloudError, match="complete HLS"):
        hls_audio.HlsSourceMixin(Session(MANIFEST.replace(b"#EXT-X-ENDLIST", b"")), BASE + "track.m3u8")
    monkeypatch.setattr(hls_audio, "MAX_MANIFEST_BYTES", 4)
    with pytest.raises(SoundCloudError, match="too large"):
        hls_audio.HlsSourceMixin(Session(), BASE + "track.m3u8")


@pytest.mark.parametrize("too_large", [False, True])
def test_transfer_failure_is_not_a_successful_eof(monkeypatch, too_large):
    session = Session()
    if too_large:
        monkeypatch.setattr(hls_audio, "MAX_AUDIO_BYTES", 4)
    else:
        session.responses[BASE + "b.mp3"] = Response(status=403)
    source = hls_audio.HlsSourceMixin(session, BASE + "track.m3u8")
    try:
        with pytest.raises(SoundCloudError, match="memory limit|HTTP 403"):
            source.read(20)
    finally:
        source.close()
        source._thread.join(2)
    assert len(source._buffer) <= hls_audio.MAX_AUDIO_BYTES


def test_close_unblocks_reader_and_stops_before_next_segment():
    session = Session()
    started, release = threading.Event(), threading.Event()

    def delayed(_size):
        started.set()
        assert release.wait(2)
        yield b"abc"

    session.responses[BASE + "a.mp3"].iter_content = delayed
    source = hls_audio.HlsSourceMixin(session, BASE + "track.m3u8")
    assert started.wait(2)
    source.close()
    release.set()
    source._thread.join(2)
    assert not source._thread.is_alive()
    assert source.read(1) == b""
    assert BASE + "b.mp3" not in session.calls


def test_manifest_connection_error_hides_signed_urls(monkeypatch):
    session = Session()

    def failed(*args, **kwargs):
        raise requests.ConnectionError(BASE + "track.m3u8?token=private")

    monkeypatch.setattr(session, "get", failed)
    with pytest.raises(SoundCloudError, match="connection failed") as exc:
        hls_audio.HlsSourceMixin(session, BASE + "track.m3u8")
    assert "private" not in str(exc.value)


def test_decoder_eof_does_not_swallow_transfer_failure(monkeypatch):
    import miniaudio

    session = Session()
    session.responses[BASE + "b.mp3"] = Response(status=403)
    source = hls_audio.HlsSourceMixin(session, BASE + "track.m3u8")
    source._thread.join(2)
    monkeypatch.setattr(miniaudio, "stream_any", lambda *args, **kwargs: iter(()))
    try:
        with pytest.raises(SoundCloudError, match="HTTP 403"):
            next(source.stream(0))
    finally:
        source.close()

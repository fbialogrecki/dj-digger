"""SoundCloud MP3 HLS: ordered segments form one seekable in-memory MP3."""

import threading
import time
from urllib.parse import urljoin, urlsplit

import requests

from .http import MAX_REDIRECTS, REDIRECT_STATUSES
from .soundcloud_errors import SoundCloudError

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_AUDIO_BYTES = 50 * 1024 * 1024
TIMEOUT = 30


def _cdn_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise SoundCloudError("Invalid SoundCloud media address") from None
    if (parsed.scheme != "https" or not host.endswith(".sndcdn.com")
            or port not in (None, 443) or parsed.username is not None
            or parsed.password is not None):
        raise SoundCloudError("Refusing an untrusted SoundCloud media address")
    return url


def _get(session, url):
    for _ in range(MAX_REDIRECTS + 1):
        try:
            response = session.get(_cdn_url(url), stream=True, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            raise SoundCloudError("SoundCloud HLS connection failed; try playing again") from None
        if response.status_code not in REDIRECT_STATUSES:
            if response.status_code != 200:
                response.close()
                raise SoundCloudError(f"SoundCloud media returned HTTP {response.status_code}")
            return response, url
        location = response.headers.get("Location", "")
        response.close()
        url = urljoin(url, location)
    raise SoundCloudError("Too many SoundCloud media redirects")


def _segments(session, url):
    response, url = _get(session, url)
    data = bytearray()
    try:
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_MANIFEST_BYTES:
                raise SoundCloudError("SoundCloud HLS manifest is too large")
    except requests.RequestException:
        raise SoundCloudError("SoundCloud HLS manifest transfer failed; try playing again") from None
    finally:
        response.close()
    try:
        lines = data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        raise SoundCloudError("SoundCloud sent an invalid HLS manifest") from None
    if not lines or lines[0] != "#EXTM3U" or "#EXT-X-ENDLIST" not in lines:
        raise SoundCloudError("SoundCloud did not provide a complete HLS track")
    # These require a different decoder/container contract; never concatenate
    # encrypted media, byte ranges or a master playlist as if they were MP3.
    unsupported = ("#EXT-X-KEY:", "#EXT-X-MAP:", "#EXT-X-BYTERANGE:", "#EXT-X-STREAM-INF:", "#EXT-X-DISCONTINUITY")
    if any(line.startswith(unsupported) for line in lines):
        raise SoundCloudError("This SoundCloud HLS format is not supported")
    segments = [_cdn_url(urljoin(url, line.strip())) for line in lines if line.strip() and not line.startswith("#")]
    if not segments or len(segments) > 10000:
        raise SoundCloudError("SoundCloud HLS has an invalid segment count")
    return segments


class HlsSourceMixin:
    """Buffer MP3 segments off the decoder thread; seeks reuse received bytes.

    The same 50 MiB memory ceiling as a buffered progressive track applies.
    Larger HLS tracks fail explicitly rather than growing memory without bound.
    """

    def __init__(self, session, url):
        segments = _segments(session, url)
        self._condition = threading.Condition()
        self._buffer = bytearray()
        self._offset = 0
        self._closed = False
        self._done = False
        self._error = None
        self._response = None
        self._thread = threading.Thread(target=self._download, args=(session, segments), daemon=True)
        self._thread.start()

    def _download(self, session, segments):
        try:
            for url in segments:
                with self._condition:
                    if self._closed:
                        return
                response, _ = _get(session, url)
                try:
                    with self._condition:
                        self._response = response
                        if self._closed:
                            return
                    for chunk in response.iter_content(64 * 1024):
                        with self._condition:
                            if self._closed:
                                return
                            if len(self._buffer) + len(chunk) > MAX_AUDIO_BYTES:
                                raise SoundCloudError("HLS preview exceeds the 50 MiB memory limit")
                            self._buffer.extend(chunk)
                            self._condition.notify_all()
                finally:
                    response.close()
        except Exception as exc:
            # Requests errors can include signed URLs; do not send those to the UI.
            with self._condition:
                self._error = str(exc) if isinstance(exc, SoundCloudError) else "SoundCloud HLS transfer failed; try playing again"
        finally:
            with self._condition:
                self._response = None
                self._done = True
                self._condition.notify_all()

    def read(self, num_bytes):
        if num_bytes <= 0:
            return b""
        deadline = time.monotonic() + TIMEOUT
        with self._condition:
            while not self._closed and not self._done and len(self._buffer) < self._offset + num_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SoundCloudError("SoundCloud HLS transfer timed out; try playing again")
                self._condition.wait(remaining)
            if self._closed:
                return b""
            data = bytes(self._buffer[self._offset:self._offset + num_bytes])
            if len(data) < num_bytes and self._error:
                raise SoundCloudError(self._error)
            self._offset += len(data)
            return data

    def seek(self, offset, origin):
        origin = getattr(origin, "value", origin)
        with self._condition:
            if self._closed or (origin == 2 and not self._done):
                return False
            target = offset + (self._offset if origin == 1 else len(self._buffer) if origin == 2 else 0)
            if target < 0 or target > MAX_AUDIO_BYTES or (self._done and target > len(self._buffer)):
                return False
            self._offset = target
            return True

    def stream(self, seek_frame):
        import miniaudio

        try:
            yield from miniaudio.stream_any(
                self, source_format=miniaudio.FileFormat.MP3,
                sample_rate=44100, nchannels=2, seek_frame=seek_frame,
            )
        except Exception:
            if self._error or self.error_in_readcallback:
                raise SoundCloudError(self._error or "SoundCloud HLS transfer failed; try playing again") from None
            raise
        # Some decoder EOF paths swallow a failed source read. A truncated
        # transfer must not look like a completed song and auto-advance.
        if not self._closed and (self._error or self.error_in_readcallback):
            raise SoundCloudError(self._error or "SoundCloud HLS transfer failed; try playing again")

    def close(self):
        with self._condition:
            self._closed = True
            response = self._response
            self._condition.notify_all()
        if response is not None:
            response.close()

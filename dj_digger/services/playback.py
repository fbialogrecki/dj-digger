"""Stream resolution and prepared media independent of table presentation."""

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from ..models import Track
from ..soundcloud_errors import SoundCloudError

LOGGER = logging.getLogger(__name__)
MAX_ARTWORK_BYTES = 4 * 1024 * 1024

class SoundCloudPlayback(Protocol):
    session: Any
    def fetch_track(self, track_id: int) -> dict: ...
    def authorize(self, url: str, **params) -> dict: ...

def unplayable_reason(payload: dict) -> str | None:
    """Why this track cannot be previewed in full, if so."""

    if payload.get("policy") == "BLOCK":
        return "SoundCloud blocks playback of this track for your account or region"
    if payload.get("policy") == "SNIP":
        return "SoundCloud only offers a 30 second snippet of this one"
    if payload.get("streamable") is False:
        return "This track is not streamable"
    transcodings = (payload.get("media") or {}).get("transcodings") or []
    if not transcodings:
        return "SoundCloud did not provide any audio streams for this track"
    if not any(_supported(item) for item in transcodings):
        return "SoundCloud did not offer a supported MP3 stream (progressive or HLS)"
    return None


def _supported(item: dict) -> bool:
    fmt = item.get("format") or {}
    return fmt.get("protocol") in {"progressive", "hls"} and fmt.get("mime_type") == "audio/mpeg"


@dataclass
class Stream:
    url: str
    # SoundCloud's artwork at 500 px; empty when the upload has none.
    artwork_url: str = ""
    duration: float = 0.0
    protocol: str = "progressive"


def resolve_stream(client: SoundCloudPlayback, track_id: int) -> Stream:
    """Signed MP3 URL and protocol, artwork URL and duration, from one refetch.

    The payload is fetched fresh every time because ``track_authorization`` and
    the signature on the returned URL both expire. Duration comes from here too,
    since nothing is written to disk to measure.
    """

    payload = client.fetch_track(track_id)
    reason = unplayable_reason(payload)
    if reason:
        LOGGER.warning(
            "Preview unavailable for SoundCloud track %d (policy=%s, streams=%d): %s",
            track_id,
            payload.get("policy") if payload.get("policy") in {"ALLOW", "BLOCK", "SNIP"} else "unknown",
            len((payload.get("media") or {}).get("transcodings") or []),
            reason,
        )
        raise SoundCloudError(reason)

    candidates = sorted(
        (item for item in payload["media"]["transcodings"] if _supported(item)),
        key=lambda item: item["format"]["protocol"] != "progressive",
    )
    chosen = candidates[0]
    authorized = client.authorize(
        chosen["url"], track_authorization=payload.get("track_authorization")
    )
    url = authorized.get("url")
    if not url:
        raise SoundCloudError("SoundCloud did not hand back a stream URL")
    milliseconds = payload.get("full_duration") or payload.get("duration") or 0
    return Stream(
        url=url,
        artwork_url=str(payload.get("artwork_url") or "").replace("-large.", "-t500x500."),
        duration=float(milliseconds) / 1000.0,
        protocol=chosen["format"]["protocol"],
    )


# ponytail: artwork is SoundCloud user content, so it stays in this session's memory only (API terms §5).
@lru_cache(maxsize=64)
def remote_artwork(session, url: str) -> bytes:
    """Artwork bytes from SoundCloud's CDN; the same host and redirect limits as the audio."""

    from ..hls_audio import _get
    response, _ = _get(session, url)
    data = bytearray()
    try:
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_ARTWORK_BYTES:
                raise SoundCloudError("SoundCloud artwork is too large")
    finally:
        response.close()
    return bytes(data)


def track_waveform(db, track: Track, source, cancel=None) -> list[int]:
    """The track's envelope, at most 1024 peaks, computed here from its audio:
    the local file, or the stream once ``source`` holds all of it. Kept in the
    library after the first computation."""

    from .. import local_audio
    from ..media import signature
    mark = signature(Path(track.local_path)) if track.local_path else ""
    cached = db.waveform(track.key, mark)
    if cached is not None:
        return cached
    if track.local_path:
        samples = local_audio.waveform(Path(track.local_path), cancel)
    else:
        data = source.whole(cancel) if hasattr(source, "whole") else None
        samples = local_audio.encoded_waveform(data) if data else []
    if samples:
        db.save_waveform(track.key, mark, samples)
    return samples


@dataclass
class Prepared:
    """A track made ready to play before anything asked for it."""

    track: Track
    stream: Stream
    # An HTTP source already filling with audio, or None if miniaudio is absent.
    source: object = None

    @property
    def key(self) -> str:
        return self.track.key

    def close(self) -> None:
        if self.source is not None:
            self.source.close()
            self.source = None

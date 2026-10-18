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
    if not any(_rank(item) is not None for item in transcodings):
        if any("encrypted" in str((item.get("format") or {}).get("protocol")) for item in transcodings):
            return "SoundCloud only offers this track as a DRM-protected stream, which dj-digger cannot play"
        return "SoundCloud did not offer a stream dj-digger can play (MP3 or AAC)"
    return None


QUALITY = {"hq": 0, "sq": 1, "lq": 2}


def _rank(item: dict) -> tuple | None:
    """Sort key among the streams we can decode, None for the rest.

    MP3 first, as miniaudio plays it without FFmpeg; then AAC in fragmented-MP4
    HLS, which is all SoundCloud served for most tracks in October 2026.
    ``encrypted-hls`` protocols and the ``abr`` master playlist never match.
    """

    fmt = item.get("format") or {}
    protocol, mime = fmt.get("protocol"), str(fmt.get("mime_type") or "")
    if protocol == "progressive" and mime == "audio/mpeg":
        codec = 0
    elif protocol == "hls" and mime == "audio/mpeg":
        codec = 1
    elif protocol == "hls" and mime.startswith("audio/mp4") and "mp4a" in mime:
        codec = 2
    else:
        return None
    return codec, QUALITY.get(item.get("quality"), 1)


@dataclass
class Stream:
    url: str
    # SoundCloud's artwork at 500 px; empty when the upload has none.
    artwork_url: str = ""
    duration: float = 0.0
    protocol: str = "progressive"


def resolve_stream(client: SoundCloudPlayback, track_id: int) -> Stream:
    """Signed MP3 or AAC URL and protocol, artwork URL and duration, from one refetch.

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

    chosen = min((item for item in payload["media"]["transcodings"] if _rank(item) is not None), key=_rank)
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


def _envelope(value) -> dict:
    """``peaks`` and ``bands`` of a stored or computed envelope; a bare list is a
    row kept before bands existed."""

    if isinstance(value, dict):
        return dict(peaks=list(value.get("peaks") or []), bands=list(value.get("bands") or []))
    return dict(peaks=list(value or []), bands=[])


def track_waveform(db, track: Track, source, cancel=None) -> dict:
    """The track's envelope, at most 1024 peaks plus their low/mid/high levels,
    computed here from its audio: the local file, or the stream once ``source``
    holds all of it. Kept in the library after the first computation; a row kept
    without bands is computed again, and stays as it was if that yields nothing."""

    from .. import local_audio
    from ..media import signature
    mark = signature(Path(track.local_path)) if track.local_path else ""
    cached = db.waveform(track.key, mark)
    cached = _envelope(cached) if cached is not None else None
    if cached is not None and cached["bands"]:
        return cached
    if track.local_path:
        computed = _envelope(local_audio.waveform(Path(track.local_path), cancel))
    else:
        data = source.whole(cancel) if hasattr(source, "whole") else None
        computed = _envelope(local_audio.encoded_waveform(data) if data else [])
    if computed["peaks"]:
        db.save_waveform(track.key, mark, computed)
        return computed
    return cached or computed


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

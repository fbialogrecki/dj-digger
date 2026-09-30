"""Stream resolution and prepared media independent of table presentation."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..models import Track
from ..soundcloud_errors import SoundCloudError

LOGGER = logging.getLogger(__name__)

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
    waveform_url: str = ""
    duration: float = 0.0
    protocol: str = "progressive"


def resolve_stream(client: SoundCloudPlayback, track_id: int) -> Stream:
    """Signed MP3 URL and protocol, waveform URL and duration, from one refetch.

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
        waveform_url=payload.get("waveform_url") or "",
        duration=float(milliseconds) / 1000.0,
        protocol=chosen["format"]["protocol"],
    )


def fetch_waveform(client: SoundCloudPlayback, waveform_url: str) -> list[int]:
    if not waveform_url:
        return []
    try:
        samples = client.session.get(waveform_url, timeout=15).json().get("samples")
        return [int(value) for value in samples] if isinstance(samples, list) else []
    except Exception as exc:  # a missing waveform must not stop playback
        LOGGER.debug("Could not read waveform %s: %s", waveform_url, exc)
        return []


def track_waveform(db, track: Track, waveform_url: str, client, cancel=None) -> list[int]:
    """The track's envelope, at most 1024 values: kept in the library after the first computation."""

    from .. import local_audio
    from ..media import signature
    mark = signature(Path(track.local_path)) if track.local_path else ""
    cached = db.waveform(track.key, mark)
    if cached is not None:
        return cached
    samples = (local_audio.waveform(Path(track.local_path), cancel) if track.local_path
               else fetch_waveform(client, waveform_url))
    samples = samples[::max(1, (len(samples) + 1023) // 1024)]
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

"""Stream resolution and prepared media independent of table presentation."""

import logging
from dataclasses import dataclass, field
from functools import lru_cache
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

    if payload.get("policy") == "SNIP":
        return "SoundCloud only offers a 30 second snippet of this one"
    if payload.get("streamable") is False:
        return "This track is not streamable"
    transcodings = (payload.get("media") or {}).get("transcodings") or []
    if not any(
        (item.get("format") or {}).get("protocol") == "progressive" for item in transcodings
    ):
        return "No plain MP3 stream offered for this track"
    return None


@dataclass
class Stream:
    url: str
    waveform_url: str = ""
    duration: float = 0.0


def resolve_stream(client: SoundCloudPlayback, track_id: int) -> Stream:
    """Signed MP3 URL, waveform URL and duration, from one refetch.

    The payload is fetched fresh every time because ``track_authorization`` and
    the signature on the returned URL both expire. Duration comes from here too,
    since nothing is written to disk to measure.
    """

    payload = client.fetch_track(track_id)
    reason = unplayable_reason(payload)
    if reason:
        raise SoundCloudError(reason)

    progressive = next(
        item
        for item in payload["media"]["transcodings"]
        if (item.get("format") or {}).get("protocol") == "progressive"
    )
    authorized = client.authorize(
        progressive["url"], track_authorization=payload.get("track_authorization")
    )
    url = authorized.get("url")
    if not url:
        raise SoundCloudError("SoundCloud did not hand back a stream URL")
    milliseconds = payload.get("full_duration") or payload.get("duration") or 0
    return Stream(
        url=url,
        waveform_url=payload.get("waveform_url") or "",
        duration=float(milliseconds) / 1000.0,
    )


@lru_cache(maxsize=256)
def _cached_waveform(client: SoundCloudPlayback, waveform_url: str) -> tuple:
    """Kept in memory for the session - 7 KB from a CDN is not worth a cache file."""

    try:
        payload = client.session.get(waveform_url, timeout=15).json()
    except Exception as exc:  # a missing waveform must not stop playback
        LOGGER.debug("Could not read waveform %s: %s", waveform_url, exc)
        return ()
    samples = payload.get("samples")
    return tuple(int(value) for value in samples) if isinstance(samples, list) else ()


def fetch_waveform(client: SoundCloudPlayback, waveform_url: str) -> list[int]:
    if not waveform_url:
        return []
    return list(_cached_waveform(client, waveform_url))


@dataclass
class Prepared:
    """A track made ready to play before anything asked for it."""

    track: Track
    stream: Stream
    waveform: list[int] = field(default_factory=list)
    # An HTTP source already filling with audio, or None if miniaudio is absent.
    source: object = None

    @property
    def key(self) -> str:
        return self.track.key

    def close(self) -> None:
        if self.source is not None:
            self.source.close()
            self.source = None



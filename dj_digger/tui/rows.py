"""The two shapes a row of the crate browser is assembled from."""

from dataclasses import dataclass, field

from ..models import LinkRecord, Track
from ..player import Stream


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


@dataclass
class Row:
    """One track, with every store it turned up in."""

    position: int
    track: Track
    # Best first, in CATEGORY_NAMES order - see links.group_by_track.
    records: list[LinkRecord]

    @property
    def categories(self) -> list[str]:
        return [record.category for record in self.records]

    def record_for(self, category: str) -> LinkRecord | None:
        for record in self.records:
            if record.category == category:
                return record
        return None

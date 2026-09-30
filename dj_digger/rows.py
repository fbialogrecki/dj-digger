"""The two shapes a row of the desktop table is assembled from."""

from dataclasses import dataclass

from .models import LinkRecord, Track


@dataclass
class Row:
    """One track, with every store it turned up in."""

    position: int
    track: Track
    # Best first, in CATEGORY_NAMES order.
    records: list[LinkRecord]

    @property
    def categories(self) -> list[str]:
        return [record.category for record in self.records]

    @property
    def haystack(self) -> str:
        """Everything the search box matches against, lower-cased."""

        track = self.track
        return " ".join(
            (track.artist, track.title, track.genre, *track.tags, track.label_name)
        ).lower()

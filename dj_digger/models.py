"""Shared data structures.

Lives in its own module so ``soundcloud``, ``html_fallback``, ``links`` and
``tui`` can all speak the same vocabulary without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Track:
    """A SoundCloud track, however it was discovered."""

    title: str
    permalink_url: str
    id: Optional[int] = None
    artist: str = ""
    purchase_url: Optional[str] = None
    purchase_title: Optional[str] = None
    description: str = ""
    downloadable: bool = False
    genre: str = ""
    # Links found outside the structured fields, e.g. scraped from a track page.
    extra_links: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identity used for persisted status, across playlists."""

        return str(self.id) if self.id else self.permalink_url

    @property
    def label(self) -> str:
        if self.artist and self.artist.lower() not in self.title.lower():
            return f"{self.artist} - {self.title}"
        return self.title

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "Track":
        user = payload.get("user") or {}

        def clean(value: Any) -> str:
            return (value or "").strip() if isinstance(value, str) else ""

        return cls(
            id=payload.get("id"),
            title=clean(payload.get("title")) or "Unknown title",
            artist=clean(user.get("username")),
            permalink_url=clean(payload.get("permalink_url")),
            purchase_url=clean(payload.get("purchase_url")) or None,
            purchase_title=clean(payload.get("purchase_title")) or None,
            description=payload.get("description") or "",
            downloadable=bool(payload.get("downloadable")),
            genre=clean(payload.get("genre")),
        )


@dataclass
class Crate:
    """A batch of tracks pulled from one source."""

    source: str
    tracks: List[Track] = field(default_factory=list)
    title: str = ""
    declared_count: Optional[int] = None


@dataclass
class LinkRecord:
    """One categorised link belonging to one track."""

    category: str
    track: Track
    link_url: str
    link_text: str

    def as_dict(self) -> Dict[str, Any]:
        """Export shape. Keeps the v0.1 keys so old summaries stay readable."""

        return {
            "title": self.track.title,
            "track_url": self.track.permalink_url,
            "shop_link": self.link_url,
            "artist": self.track.artist,
            "track_id": self.track.id,
            "link_text": self.link_text,
        }

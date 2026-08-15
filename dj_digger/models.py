"""Shared data structures.

Lives in its own module so ``soundcloud``, ``html_fallback``, ``links`` and
``tui`` can all speak the same vocabulary without importing each other.
"""

import shlex
from dataclasses import dataclass, field
from typing import Any, Self


def parse_tags(tag_list: str) -> list[str]:
    """Split SoundCloud's tag_list, where multi-word tags are quoted."""

    if not tag_list:
        return []
    try:
        return shlex.split(tag_list)
    except ValueError:
        # An artist left a quote unclosed; we lose multi-word tags, not the lot.
        return tag_list.replace('"', " ").split()


@dataclass
class Track:
    """A SoundCloud track, however it was discovered."""

    title: str
    permalink_url: str
    id: int | None = None
    artist: str = ""
    purchase_url: str | None = None
    purchase_title: str | None = None
    download_url: str | None = None
    description: str = ""
    downloadable: bool = False
    # Artists cap how many free downloads they hand out, and the cap is reached
    # more often than not: `downloadable` alone promises a file that is gone.
    has_downloads_left: bool = False
    duration: int = 0
    genre: str = ""
    tags: list[str] = field(default_factory=list)
    # Links found outside the structured fields, e.g. scraped from a track page.
    extra_links: list[tuple[str, str]] = field(default_factory=list)
    local_path: str | None = None

    @property
    def key(self) -> str:
        """Stable identity used for persisted status, across playlists."""

        return str(self.id) if self.id else self.permalink_url

    @property
    def free_download(self) -> bool:
        """SoundCloud itself will hand over the file, and has not run out."""

        return self.downloadable and self.has_downloads_left

    @property
    def has_direct_download(self) -> bool:
        """The API says the artist currently offers a concrete download URL."""

        return self.free_download and bool(self.download_url)

    @property
    def duration_label(self) -> str:
        if self.duration <= 0:
            return ""
        seconds = round(self.duration / 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    @property
    def label(self) -> str:
        if self.artist and self.artist.lower() not in self.title.lower():
            return f"{self.artist} - {self.title}"
        return self.title

    @property
    def genre_label(self) -> str:
        """Genre if the artist set one, otherwise their first tag."""

        return self.genre or (self.tags[0] if self.tags else "")

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Self:
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
            download_url=clean(payload.get("download_url")) or None,
            description=payload.get("description") or "",
            downloadable=bool(payload.get("downloadable")),
            has_downloads_left=bool(payload.get("has_downloads_left")),
            duration=int(payload.get("full_duration") or payload.get("duration") or 0),
            genre=clean(payload.get("genre")),
            tags=parse_tags(payload.get("tag_list") or ""),
        )


@dataclass
class Crate:
    """A batch of tracks pulled from one source."""

    source: str
    tracks: list[Track] = field(default_factory=list)
    title: str = ""
    declared_count: int | None = None


@dataclass
class LinkRecord:
    """One categorised link belonging to one track."""

    category: str
    track: Track
    link_url: str
    link_text: str

    def as_dict(self) -> dict[str, Any]:
        """Export shape. Keeps the v0.1 keys so old summaries stay readable."""

        return {
            "title": self.track.title,
            "track_url": self.track.permalink_url,
            "shop_link": self.link_url,
            "artist": self.track.artist,
            "track_id": self.track.id,
            "link_text": self.link_text,
        }

"""Turn tracks into categorised store links, and write them out.

On the API path a track already tells us where to buy it via ``purchase_url``,
so no page scraping is needed. That field is not trustworthy on its own though -
artists also hang interviews and press articles off it - so a link only earns a
store category by matching a known domain. Anything else falls back to
``others``.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

from .models import LinkRecord, Track

DOWNLOAD_KEYWORDS = {"download", "free download", "free d/l"}
LINK_KEYWORDS = DOWNLOAD_KEYWORDS | {"buy", "purchase", "premiere", "kup"}

# Domains grouped by where a link actually takes you. The membership here comes
# from surveying purchase_url across 53 playlists / 3497 tracks rather than from
# guesswork: smart links (lnk.to, ffm.to, fanlink, orcd.co and labels' own .link
# domains) turned out to be the single biggest group that used to land in
# "others", followed by follow-to-download gates like wump.io.
STORE_DOMAINS = {
    "bandcamp": {"bandcamp.com"},
    "beatport": {"beatport.com", "btprt.dj"},
    "traxsource": {"traxsource.com"},
    "junodownload": {"junodownload.com", "juno.co.uk"},
    "apple": {"apple.com", "apple.co"},
    # Real shops that individually turn up too rarely to deserve their own
    # category - their badge shows the domain, so you still know which one.
    "shop": {
        "boomkat.com",
        "redeyerecords.co.uk",
        "volumo.com",
        "gumroad.com",
        "hardwax.com",
        "decks.de",
        "deejay.de",
        "clone.nl",
        "phonicarecords.com",
        "rushhour.nl",
        "bleep.com",
    },
    # Follow-or-like gates: the track is free, but you have to earn it. Hypeddit
    # is the big one, but from where you sit they are all the same chore, so they
    # share a category rather than splitting the count across near-synonyms.
    "gate": {
        "hypeddit.com",
        "hypd.it",
        "gaterush.me",
        "droploud.com",
        "wump.io",
        "theartistunion.com",
        "pumpyoursound.com",
        "toneden.io",
        "hitsdistrict.com",
        "click.dj",
    },
    "smartlink": {
        "distrokid.com",
        "lnk.to",
        "ffm.to",
        "fanlink.to",
        "fanlink.tv",
        "smarturl.it",
        "orcd.co",
        "linktr.ee",
        "found.ee",
        "snd.click",
        "hyperfollow.com",
        "hyperurl.co",
        "push.fm",
        "songwhip.com",
        "li.sten.to",
        "gate.fm",
        "linksr.io",
    },
    "streaming": {
        "open.spotify.com",
        "spotify.com",
        "spoti.fi",
        "youtube.com",
        "youtu.be",
        "deezer.com",
        "tidal.com",
        "music.amazon.com",
    },
}

# Labels buy their own smart-link domains on this TLD, which is what it exists for.
SMARTLINK_TLD = ".link"

# Ordered so that the best outcome comes first, which is also the order the TUI
# opens links in: a file SoundCloud will simply give you, then somewhere to buy,
# then a gate to earn it free, then a click-through, then stream-only, then no
# idea. "soundcloud" is also where tracks land that have no store link at all -
# the track page is still worth opening, for the description if nothing else.
CATEGORY_NAMES = [
    "soundcloud",
    "bandcamp",
    "beatport",
    "traxsource",
    "junodownload",
    "apple",
    "shop",
    "gate",
    "smartlink",
    "streaming",
    "others",
]

# Descriptions are promo boilerplate - full of Spotify, YouTube and the label's
# linktree on every single track. Only destinations you can actually buy or
# download from are worth harvesting out of them.
DESCRIPTION_CATEGORIES = frozenset(CATEGORY_NAMES) - {
    "soundcloud",
    "smartlink",
    "streaming",
    "others",
}

CATEGORY_CHOICES = CATEGORY_NAMES + ["all"]
EXPORT_FORMATS = ["json", "yaml", "csv", "none"]

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")
TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"

NO_STORE_LINK = "No store link found"
FREE_DOWNLOAD = "Free download on SoundCloud"

LOGGER = logging.getLogger(__name__)


def host_of(url: str) -> str:
    host = urlparse(url).netloc.lower().partition(":")[0]
    return host[4:] if host.startswith("www.") else host


def _host_matches(host: str, domain: str) -> bool:
    """Match on domain boundaries, so evil-bandcamp.com.attacker.net does not."""

    return host == domain or host.endswith("." + domain)


def store_for_url(url: str) -> Optional[str]:
    host = host_of(url)
    for category, domains in STORE_DOMAINS.items():
        if any(_host_matches(host, domain) for domain in domains):
            return category
    if host.endswith(SMARTLINK_TLD):
        return "smartlink"
    return None


def urls_in_text(text: str) -> List[str]:
    return [match.rstrip(TRAILING_PUNCTUATION) for match in URL_RE.findall(text or "")]


PURCHASE_FIELD = "purchase"
DESCRIPTION_FIELD = "description"


def candidate_links(track: Track) -> List[Tuple[str, str, str]]:
    """Every link worth inspecting for one track as (url, text, source).

    Ordered best source first, which is what lets ``categorise`` keep the album
    link from ``purchase_url`` over the label homepage from the description.
    """

    candidates: List[Tuple[str, str, str]] = []
    seen: Set[str] = set()

    def add(url: str, text: str, source: str) -> None:
        url = (url or "").strip().rstrip(TRAILING_PUNCTUATION)
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append((url, text, source))

    if track.purchase_url:
        add(track.purchase_url, track.purchase_title or "Buy", PURCHASE_FIELD)
    for url, text in track.extra_links:
        add(url, text, PURCHASE_FIELD)
    for url in urls_in_text(track.description):
        add(url, "Link in description", DESCRIPTION_FIELD)

    return candidates


def categorise(track: Track) -> List[LinkRecord]:
    """Categorise one track's links, never returning an empty list.

    At most one link per store: a track that lists an album on Bandcamp and also
    name-drops the label's Bandcamp page in its description only needs the first,
    and ``candidate_links`` already puts the best source first.
    """

    records: List[LinkRecord] = []
    claimed: Set[str] = set()
    unmatched: List[Tuple[str, str]] = []

    if track.free_download:
        # Nothing beats a file the artist is handing out directly, so this one
        # goes in even when the track also sells somewhere.
        records.append(LinkRecord("soundcloud", track, track.permalink_url, FREE_DOWNLOAD))
        claimed.add("soundcloud")

    for url, text, source in candidate_links(track):
        category = store_for_url(url)
        if category:
            if source == DESCRIPTION_FIELD and category not in DESCRIPTION_CATEGORIES:
                continue
            if category in claimed:
                continue
            claimed.add(category)
            records.append(LinkRecord(category, track, url, text))
        elif url == track.purchase_url:
            # An explicit purchase field pointing somewhere we do not know.
            unmatched.append((url, text))

    if records:
        return records

    if unmatched:
        return [LinkRecord("others", track, url, text) for url, text in unmatched]

    return [LinkRecord("soundcloud", track, track.permalink_url, NO_STORE_LINK)]


def categorise_all(tracks: Iterable[Track]) -> List[LinkRecord]:
    records: List[LinkRecord] = []
    for track in tracks:
        records.extend(categorise(track))
    return records


def group_by_track(records: Sequence[LinkRecord]) -> List[List[LinkRecord]]:
    """One list of links per track, tracks in first-seen order, best link first.

    ``categorise`` emits a record per store, so a track selling on Bandcamp and
    gated on Hypeddit arrives as two records. Anything showing one row per track
    needs them back together, ordered so the first is the one worth opening.
    """

    rank = {name: index for index, name in enumerate(CATEGORY_NAMES)}
    groups: Dict[str, List[LinkRecord]] = {}
    for record in records:
        groups.setdefault(record.track.key, []).append(record)
    return [
        sorted(group, key=lambda record: rank.get(record.category, len(rank)))
        for group in groups.values()
    ]


def build_summary(records: Sequence[LinkRecord]) -> Dict[str, List[Dict[str, object]]]:
    """Group records into the export shape, keyed by category."""

    summary: Dict[str, List[Dict[str, object]]] = {name: [] for name in CATEGORY_NAMES}
    for record in records:
        category = record.category if record.category in CATEGORY_NAMES else "others"
        summary[category].append(record.as_dict())
    return summary


def count_by_category(records: Sequence[LinkRecord]) -> Dict[str, int]:
    counts = {name: 0 for name in CATEGORY_NAMES}
    for record in records:
        category = record.category if record.category in CATEGORY_NAMES else "others"
        counts[category] += 1
    return counts


def present_categories(records: Sequence[LinkRecord]) -> List[str]:
    """Categories this crate actually contains, in canonical order.

    With a dozen possible categories, showing or cycling through the empty ones
    is just noise.
    """

    counts = count_by_category(records)
    return [name for name in CATEGORY_NAMES if counts[name]]


def default_output_path(export_format: str) -> Path:
    extension = {"json": "json", "yaml": "yaml", "csv": "csv"}.get(export_format, "json")
    return Path(f"soundcloud_links.{extension}")


def export_records(
    records: Sequence[LinkRecord],
    export_format: str,
    output_path: Optional[Path] = None,
) -> Optional[Path]:
    """Write categorised links to disk. Returns the path written, if any."""

    if export_format == "none":
        return None

    path = Path(output_path) if output_path else default_output_path(export_format)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)

    if export_format == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["category", "artist", "title", "track_url", "shop_link"])
            for record in records:
                writer.writerow(
                    [
                        record.category,
                        record.track.artist,
                        record.track.title,
                        record.track.permalink_url,
                        record.link_url,
                    ]
                )
        LOGGER.info("Saved %s links to %s", len(records), path)
        return path

    summary = build_summary(records)

    if export_format == "json":
        with path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
        LOGGER.info("Saved %s links to %s", len(records), path)
        return path

    if export_format == "yaml":
        try:
            import yaml
        except ModuleNotFoundError:
            LOGGER.error(
                "YAML export needs PyYAML. Install it with: pip install 'dj-soundcloud-digger[yaml]'"
            )
            return None
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(summary, handle, sort_keys=False, allow_unicode=True)
        LOGGER.info("Saved %s links to %s", len(records), path)
        return path

    raise ValueError(f"Unknown export format: {export_format}")


def load_summary(path: Path) -> List[LinkRecord]:
    """Read a previously exported JSON/YAML summary back into records."""

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Summary file not found: {path}")

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Reading YAML needs PyYAML. Install it with: pip install 'dj-soundcloud-digger[yaml]'"
            ) from exc
        data = yaml.safe_load(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path} should contain a mapping of category to links")

    records: List[LinkRecord] = []
    for category, items in data.items():
        if not isinstance(items, list):
            raise ValueError(f"Category '{category}' in {path} should contain a list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"Items in category '{category}' should be mappings")
            track_url = item.get("track_url")
            if not track_url:
                raise ValueError(f"Items in category '{category}' need a 'track_url'")
            link_url = item.get("shop_link") or track_url
            track = Track(
                title=item.get("title") or "Unknown title",
                permalink_url=track_url,
                id=item.get("track_id"),
                artist=item.get("artist") or "",
                # Carried on the track so a summary can round-trip into a crate
                # and still categorise the same way.
                extra_links=[] if link_url == track_url else [(link_url, item.get("link_text") or "")],
            )
            records.append(
                LinkRecord(
                    category=category if category in CATEGORY_NAMES else "others",
                    track=track,
                    link_url=link_url,
                    link_text=item.get("link_text") or "",
                )
            )
    return records


def tracks_from_records(records: Sequence[LinkRecord]) -> List[Track]:
    """Collapse records back into unique tracks, merging their links onto each."""

    by_key: Dict[str, Track] = {}
    for record in records:
        track = by_key.setdefault(record.track.key, record.track)
        if track is record.track:
            continue
        for pair in record.track.extra_links:
            if pair not in track.extra_links:
                track.extra_links.append(pair)
    return list(by_key.values())

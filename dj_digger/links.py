"""Turn tracks into categorised store links, and write them out.

On the API path a track already tells us where to buy it via ``purchase_url``,
so no page scraping is needed. That field is not trustworthy on its own though -
artists also hang interviews and press articles off it - so a link only earns a
store category by matching a known domain. Anything else falls back to
``others``.
"""

import csv
import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import chain
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .browser import is_fetchable, is_openable
from .models import LinkRecord, Track

LINK_KEYWORDS = {"download", "free download", "free d/l", "buy", "purchase", "premiere", "kup"}

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
# idea. Tracks with no recognised link get their own category so they cannot be
# mistaken for a SoundCloud download.
CATEGORY_NAMES = [
    "soundcloud",
    "no-link",
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

# Where you can actually buy the record. What a link hub is worth opening for.
SHOP_CATEGORIES = frozenset(
    {"bandcamp", "beatport", "traxsource", "junodownload", "apple", "shop"}
)

# Categories whose page might turn out to be a list of shops rather than a
# download - see ``hub_links``. An unrecognised purchase link counts too, which
# is the None case there rather than a name here.
HUB_CATEGORIES = frozenset({"gate", "smartlink"})

# Descriptions are promo boilerplate - full of Spotify, YouTube and the label's
# linktree on every single track. Only destinations you can actually buy or
# download from are worth harvesting out of them.
DESCRIPTION_CATEGORIES = frozenset(CATEGORY_NAMES) - {
    "soundcloud",
    "no-link",
    "smartlink",
    "streaming",
    "others",
}

CATEGORY_CHOICES = CATEGORY_NAMES + ["all"]
EXPORT_FORMATS = ["json", "csv", "none"]

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")
TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"

NO_STORE_LINK = "No link found"
FREE_DOWNLOAD = "Free download on SoundCloud"

LOGGER = logging.getLogger(__name__)


# The only gate provider worth chasing out of a track description rather than
# just purchase_url; shared so the TUI, the digger and the gate router all
# recognise the same hosts (compare via host_of, which strips www.).
HYPEDDIT_HOSTS = frozenset({"hypeddit.com", "hypd.it"})


def redact_url(url: str) -> str:
    """A log-safe URL: host and path only - never credentials, query or fragment.

    Built from ``hostname`` rather than ``netloc`` so ``user:pass@`` can never
    reach a log line; the port is dropped with it.
    """

    try:
        parsed = urlparse(url or "")
    except ValueError:
        return "<invalid-url>"
    host = parsed.hostname or "<unknown-host>"
    return urlunparse((parsed.scheme, host, parsed.path, "", "", ""))


def host_of(url: str) -> str:
    # ``hostname`` is already lowercase, and carries neither port nor userinfo.
    return (urlparse(url).hostname or "").removeprefix("www.")


def is_hypeddit_url(url: str) -> bool:
    return is_openable(url) and host_of(url) in HYPEDDIT_HOSTS


def host_matches(host: str, domain: str) -> bool:
    """Match on domain boundaries, so evil-bandcamp.com.attacker.net does not."""

    return host == domain or host.endswith("." + domain)


def store_for_url(url: str) -> str | None:
    # The scheme is checked before the host, because the host is the only thing
    # the domain tables look at: ``file://bandcamp.com/etc/passwd`` matches
    # bandcamp perfectly well, and a category is what makes a link openable.
    if not is_openable(url):
        return None
    host = host_of(url)
    for category, domains in STORE_DOMAINS.items():
        if any(host_matches(host, domain) for domain in domains):
            return category
    if host.endswith(SMARTLINK_TLD):
        return "smartlink"
    return None


def urls_in_text(text: str) -> list[str]:
    return [match.rstrip(TRAILING_PUNCTUATION) for match in URL_RE.findall(text or "")]


PURCHASE_FIELD = "purchase"
DESCRIPTION_FIELD = "description"


def candidate_links(track: Track) -> list[tuple[str, str, str]]:
    """Every link worth inspecting for one track as (url, text, source).

    Ordered best source first, which is what lets ``categorise`` keep the album
    link from ``purchase_url`` over the label homepage from the description.
    """

    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()

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


def hub_links(track: Track) -> list[str]:
    """Purchase links worth opening to see whether they are a shop list.

    A gate, a smart link, or a host nobody recognises: any of the three can turn
    out to hand over no file at all and just point at Bandcamp and Beatport.
    Description links are left out except for known Hypeddit hosts. That keeps
    generic promo boilerplate cheap while ensuring a download wrapper is
    inspected regardless of which SoundCloud field carried it.
    """

    found: list[str] = []
    for url, _text, source in candidate_links(track):
        known_hypeddit = is_hypeddit_url(url)
        if source != PURCHASE_FIELD and not known_hypeddit:
            continue
        category = store_for_url(url)
        if known_hypeddit or category in HUB_CATEGORIES or (
            category is None and is_openable(url)
        ):
            found.append(url)
    # Openable is the wrong bar for a list that exists to be fetched: nobody
    # pressed a key for these, a dig reads them by itself, and the addresses came
    # out of a purchase_url a stranger set.
    return [url for url in found if is_fetchable(url)]


def categorise(track: Track) -> list[LinkRecord]:
    """Categorise one track's links, never returning an empty list.

    At most one link per store: a track that lists an album on Bandcamp and also
    name-drops the label's Bandcamp page in its description only needs the first,
    and ``candidate_links`` already puts the best source first.
    """

    records: list[LinkRecord] = []
    claimed: set[str] = set()
    unmatched: list[tuple[str, str]] = []

    if track.free_download:
        # Nothing beats a file the artist is handing out directly, so this one
        # goes in even when the track also sells somewhere.
        records.append(
            LinkRecord(
                "soundcloud",
                track,
                track.download_url or track.permalink_url,
                FREE_DOWNLOAD,
            )
        )
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
        elif url == track.purchase_url and is_openable(url):
            # An explicit purchase field pointing somewhere we do not know. It
            # still has to be a web address: "others" is opened like any other
            # category, so an unrecognised destination is not a licence to hand
            # the OS a file:// path.
            unmatched.append((url, text))

    if records:
        return records

    if unmatched:
        return [LinkRecord("others", track, url, text) for url, text in unmatched]

    return [LinkRecord("no-link", track, track.permalink_url, NO_STORE_LINK)]


def categorise_all(tracks: Iterable[Track]) -> list[LinkRecord]:
    return list(chain.from_iterable(categorise(track) for track in tracks))


def group_by_track(records: Sequence[LinkRecord]) -> list[list[LinkRecord]]:
    """One list of links per track, tracks in first-seen order, best link first.

    ``categorise`` emits a record per store, so a track selling on Bandcamp and
    gated on Hypeddit arrives as two records. Anything showing one row per track
    needs them back together, ordered so the first is the one worth opening.
    """

    rank = {name: index for index, name in enumerate(CATEGORY_NAMES)}
    groups: dict[str, list[LinkRecord]] = {}
    for record in records:
        groups.setdefault(record.track.key, []).append(record)
    return [
        sorted(group, key=lambda record: rank.get(record.category, len(rank)))
        for group in groups.values()
    ]


def _bucket(record: LinkRecord) -> str:
    return record.category if record.category in CATEGORY_NAMES else "others"


def build_summary(records: Sequence[LinkRecord]) -> dict[str, list[dict[str, object]]]:
    """Group records into the export shape, keyed by category."""

    summary: dict[str, list[dict[str, object]]] = {name: [] for name in CATEGORY_NAMES}
    for record in records:
        summary[_bucket(record)].append(record.as_dict())
    return summary


def count_by_category(records: Sequence[LinkRecord]) -> dict[str, int]:
    counts = {name: 0 for name in CATEGORY_NAMES}
    counts.update(Counter(_bucket(record) for record in records))
    return counts


def present_categories(records: Sequence[LinkRecord]) -> list[str]:
    """Categories this crate actually contains, in canonical order.

    With a dozen possible categories, showing or cycling through the empty ones
    is just noise.
    """

    counts = count_by_category(records)
    return [name for name in CATEGORY_NAMES if counts[name]]


def export_records(
    records: Sequence[LinkRecord],
    export_format: str,
    output_path: Path | None = None,
) -> Path | None:
    """Write categorised links to disk. Returns the path written, if any."""

    if export_format == "none":
        return None

    path = Path(output_path) if output_path else Path(f"soundcloud_links.{export_format}")
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)

    if export_format == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            # New columns go on the end: a script reading this by position
            # still finds what it read before.
            writer.writerow(
                ["category", "artist", "title", "track_url", "shop_link", "bpm", "key", "release_year", "label"]
            )
            for record in records:
                writer.writerow(
                    [
                        record.category,
                        record.track.artist,
                        record.track.title,
                        record.track.permalink_url,
                        record.link_url,
                        record.track.bpm_label,
                        record.track.key_signature,
                        record.track.release_year or "",
                        record.track.label_name,
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

    raise ValueError(f"Unknown export format: {export_format}")


def load_summary(path: Path) -> list[LinkRecord]:
    """Read a previously exported JSON summary back into records."""

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Summary file not found: {path}")

    if path.suffix.lower() in {".yaml", ".yml"}:
        # Written by 0.5 and earlier. Saying so beats a parser error about a
        # colon on line one.
        raise ValueError(
            f"{path} is YAML, which this version no longer reads. Convert it to "
            "JSON, or re-dig the source."
        )

    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path} should contain a mapping of category to links")

    records: list[LinkRecord] = []
    for category, items in data.items():
        if not isinstance(items, list):
            raise ValueError(f"Category '{category}' in {path} should contain a list")
        records.extend(_record_from_item(category, item) for item in items)
    return records


def _record_from_item(category: str, item: object) -> LinkRecord:
    """One summary entry validated into a record; raises on anything off-shape."""

    if not isinstance(item, dict):
        raise ValueError(f"Items in category '{category}' should be mappings")
    track_url = item.get("track_url")
    if not track_url:
        raise ValueError(f"Items in category '{category}' need a 'track_url'")
    link_url = item.get("shop_link") or track_url
    # Categorisation is skipped for a summary - the category is read off
    # the file - so this is the only place these two are checked before
    # they reach the browser. Loud rather than quiet: this file is
    # written by the digger itself, so anything else in it is either
    # corruption or someone hoping you will press 'o'.
    for field, value in (("track_url", track_url), ("shop_link", link_url)):
        if not is_openable(value):
            raise ValueError(
                f"'{field}' in category '{category}' is not an http or https "
                f"link: {value!r}"
            )
    track = Track(
        title=item.get("title") or "Unknown title",
        permalink_url=track_url,
        id=item.get("track_id"),
        artist=item.get("artist") or "",
        bpm=item.get("bpm") if isinstance(item.get("bpm"), (int, float)) else None,
        key_signature=str(item.get("key") or ""),
        release_year=item.get("release_year") if isinstance(item.get("release_year"), int) else None,
        label_name=str(item.get("label") or ""),
        # Carried on the track so a summary can round-trip into a crate
        # and still categorise the same way.
        extra_links=[] if link_url == track_url else [(link_url, item.get("link_text") or "")],
    )
    return LinkRecord(
        category=category if category in CATEGORY_NAMES else "others",
        track=track,
        link_url=link_url,
        link_text=item.get("link_text") or "",
    )


def tracks_from_records(records: Sequence[LinkRecord]) -> list[Track]:
    """Collapse records back into unique tracks, merging their links onto each."""

    by_key: dict[str, Track] = {}
    for record in records:
        track = by_key.setdefault(record.track.key, record.track)
        if track is record.track:
            continue
        for pair in record.track.extra_links:
            if pair not in track.extra_links:
                track.extra_links.append(pair)
    return list(by_key.values())

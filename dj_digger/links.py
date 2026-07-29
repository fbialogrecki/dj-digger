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
STORE_DOMAINS = {
    "bandcamp": {"bandcamp.com"},
    "beatport": {"beatport.com"},
    "junodownload": {"junodownload.com", "juno.co.uk"},
    "hypeddit": {"hypeddit.com", "hypd.it"},
}
CATEGORY_NAMES = ["hypeddit", "bandcamp", "beatport", "junodownload", "others"]
CATEGORY_CHOICES = CATEGORY_NAMES + ["all"]
EXPORT_FORMATS = ["json", "yaml", "csv", "none"]

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")
TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"

NO_STORE_LINK = "No store link found"

LOGGER = logging.getLogger(__name__)


def store_for_url(url: str) -> Optional[str]:
    domain = urlparse(url).netloc.lower()
    for category, domains in STORE_DOMAINS.items():
        if any(target in domain for target in domains):
            return category
    return None


def urls_in_text(text: str) -> List[str]:
    return [match.rstrip(TRAILING_PUNCTUATION) for match in URL_RE.findall(text or "")]


def candidate_links(track: Track) -> List[Tuple[str, str]]:
    """Every link worth inspecting for one track, best source first."""

    candidates: List[Tuple[str, str]] = []
    seen: Set[str] = set()

    def add(url: str, text: str) -> None:
        url = (url or "").strip().rstrip(TRAILING_PUNCTUATION)
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append((url, text))

    if track.purchase_url:
        add(track.purchase_url, track.purchase_title or "Buy")
    for url, text in track.extra_links:
        add(url, text)
    for url in urls_in_text(track.description):
        add(url, "Link in description")

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

    for url, text in candidate_links(track):
        category = store_for_url(url)
        if category:
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

    return [LinkRecord("others", track, track.permalink_url, NO_STORE_LINK)]


def categorise_all(tracks: Iterable[Track]) -> List[LinkRecord]:
    records: List[LinkRecord] = []
    for track in tracks:
        records.extend(categorise(track))
    return records


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
            track = Track(
                title=item.get("title") or "Unknown title",
                permalink_url=track_url,
                id=item.get("track_id"),
                artist=item.get("artist") or "",
            )
            records.append(
                LinkRecord(
                    category=category if category in CATEGORY_NAMES else "others",
                    track=track,
                    link_url=item.get("shop_link") or track_url,
                    link_text=item.get("link_text") or "",
                )
            )
    return records

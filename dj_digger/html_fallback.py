"""Fallback path: read a playlist from an HTML file saved in the browser.

The API path replaces this for everything public, but a saved page is still the
only way in for private or unlisted playlists, and it keeps working on the day
SoundCloud changes api-v2. A saved page usually carries a ``window.__sc_hydration``
blob with every track id in it, which can be handed straight to the batch
hydrator; if it does not, the anchors on the page are scraped the slow old way.
"""

import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .links import LINK_KEYWORDS
from .models import Track

TRACK_URL_PATTERN = re.compile(
    r"^https://soundcloud.com/([^/]+)/([^/?#]+)(?:[/?#]|$)", re.IGNORECASE
)
HYDRATION_RE = re.compile(r"window\.__sc_hydration\s*=\s*")

RESERVED_FIRST_SEGMENTS = {
    "about",
    "contributors",
    "discover",
    "popular",
    "charts",
    "company",
    "jobs",
    "press",
    "legal",
    "advertisers",
    "terms-of-use",
    "privacy",
    "pages",
    "stream",
    "stations",
    "getstarted",
    "the-upload",
    "you",
}
RESERVED_SECOND_SEGMENTS = {
    "sets",
    "albums",
    "tracks",
    "followers",
    "following",
    "library",
    "likes",
    "comments",
    "reposts",
    "popular-tracks",
    "groups",
    "events",
}

LOGGER = logging.getLogger(__name__)


def clean_track_url(url: str) -> str:
    parsed = urlparse(url)
    cleaned_query = ""
    if parsed.query:
        params = [p for p in parsed.query.split("&") if not p.startswith("in=")]
        if params:
            cleaned_query = "?" + "&".join(params)
    cleaned = parsed._replace(query=cleaned_query, fragment="")
    return cleaned.geturl().rstrip("?")


def is_reserved_path(path_segments: list[str]) -> bool:
    if not path_segments:
        return True
    first_segment = path_segments[0].lower()
    if first_segment in RESERVED_FIRST_SEGMENTS:
        return True
    if len(path_segments) >= 2 and path_segments[1].lower() in RESERVED_SECOND_SEGMENTS:
        return True
    return False


def parse_track_links_from_html(html: str) -> set[str]:
    links: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.startswith("/"):
            href = urljoin("https://soundcloud.com", href)
        match = TRACK_URL_PATTERN.match(href)
        if not match:
            continue
        cleaned = clean_track_url(href)
        segments = [seg for seg in urlparse(cleaned).path.split("/") if seg]
        if len(segments) < 2 or is_reserved_path(segments):
            continue
        links.add(cleaned)
    return links


def parse_hydration(html: str) -> list | None:
    """Pull the ``window.__sc_hydration`` array out of a saved page.

    Uses ``raw_decode`` to read exactly one JSON value starting at the opening
    bracket, which is what the surrounding JavaScript makes impossible to do with
    a plain ``json.loads``.
    """

    match = HYDRATION_RE.search(html)
    if not match:
        return None
    start = html.find("[", match.end())
    if start == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError as exc:
        LOGGER.debug("Could not decode hydration payload: %s", exc)
        return None
    return data if isinstance(data, list) else None


def extract_from_hydration(dataset: list | None) -> tuple[list[int], set[str], int | None]:
    """Return (track ids, track urls, declared count) from a hydration array."""

    track_ids: list[int] = []
    urls: set[str] = set()
    declared: int | None = None

    for entry in dataset or []:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        if not isinstance(data, dict):
            continue

        if entry.get("hydratable") == "sound":
            if isinstance(data.get("id"), int):
                track_ids.append(data["id"])
            if data.get("permalink_url"):
                urls.add(clean_track_url(data["permalink_url"]))
            continue

        if entry.get("hydratable") != "playlist":
            continue

        count = data.get("track_count")
        if declared is None and isinstance(count, int):
            declared = count

        tracks = data.get("tracks")
        if not isinstance(tracks, list):
            continue
        if declared is None:
            declared = len(tracks)
        for track in tracks:
            if not isinstance(track, dict):
                continue
            if isinstance(track.get("id"), int):
                track_ids.append(track["id"])
            permalink = track.get("permalink_url")
            if permalink:
                urls.add(clean_track_url(permalink))

    # Preserve playlist order while removing repeats.
    seen: set[int] = set()
    ordered_ids = [tid for tid in track_ids if not (tid in seen or seen.add(tid))]
    return ordered_ids, urls, declared


def extract_declared_track_count(html: str) -> int | None:
    soup = BeautifulSoup(html, "html.parser")

    meta = soup.find("meta", attrs={"itemprop": "numTracks"})
    if meta:
        content = meta.get("content") or meta.get("value")
        try:
            return int(content)
        except (TypeError, ValueError):
            pass

    text = soup.get_text(" ", strip=True)
    for pattern in (r"Contains tracks\s*(\d+)", r"\b(\d{1,4})\s+tracks?\b"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue

    return None


def read_html(path: Path) -> str:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"HTML file not found: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def load_playlist(path: Path) -> tuple[list[int], list[str], int | None]:
    """Read a saved playlist page.

    Returns track ids (fast API hydration), track urls (slow scraping fallback)
    and the track count the page claims to hold.
    """

    html = read_html(path)
    track_ids, hydration_urls, declared = extract_from_hydration(parse_hydration(html))
    urls = sorted(hydration_urls | parse_track_links_from_html(html))
    if declared is None:
        declared = extract_declared_track_count(html)
    return track_ids, urls, declared


def extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.get_text():
        title = soup.title.get_text().strip()
        for suffix in (" | SoundCloud", " | Listen online for free on SoundCloud"):
            if suffix in title:
                return title.split(suffix)[0].strip()
        return title
    return "Unknown title"


def normalize_link(track_url: str, href: str) -> str:
    if href.startswith("//"):
        return f"{urlparse(track_url).scheme}:{href}"
    if href.startswith("/"):
        parsed = urlparse(track_url)
        return f"{parsed.scheme}://{parsed.netloc}{href}"
    if href.startswith(("http://", "https://")):
        return href
    return urljoin(track_url, href)


def scrape_track_page(
    track_url: str,
    session: requests.Session,
    timeout: float = 20.0,
) -> Track:
    """Slow path: fetch one track page and read purchase links off the anchors."""

    try:
        response = session.get(track_url, timeout=timeout)
    except requests.RequestException as exc:
        LOGGER.warning("Request error for %s: %s", track_url, exc)
        return Track(title="Unknown title", permalink_url=track_url)

    if response.status_code >= 400:
        LOGGER.warning("Could not retrieve %s (HTTP %s)", track_url, response.status_code)
        return Track(title="Unknown title", permalink_url=track_url)

    soup = BeautifulSoup(response.text, "html.parser")
    extra_links: list[tuple[str, str]] = []
    for anchor in soup.select("a[href]"):
        href = anchor["href"].strip()
        if not href:
            continue
        text = anchor.get_text(strip=True)
        if any(keyword in text.lower() for keyword in LINK_KEYWORDS):
            extra_links.append((normalize_link(track_url, href), text))

    return Track(
        title=extract_title(soup),
        permalink_url=track_url,
        extra_links=extra_links,
    )

"""Turning a target into a crate, independent of how progress gets displayed.

Both the CLI (rich progress bar) and the TUI (worker thread plus a status line)
need to do exactly the same work, so the work lives here and the caller supplies
an ``on_progress`` hook.
"""

import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import gates, html_fallback, links, soundcloud
from .models import Crate, Track

# stage, done, total (total is None while it is still unknown)
ProgressHook = Callable[[str, int, int | None], None]

STAGE_LINK = "Reading the link"
STAGE_TRACKS = "Fetching tracks"
STAGE_PAGES = "Scraping track pages"
STAGE_HUBS = "Opening link hubs"

# One page plus a handful of redirects per track, so this is worth doing several
# at a time. Kept modest: these are somebody else's servers.
HUB_WORKERS = 8

LOGGER = logging.getLogger(__name__)


@dataclass
class DigOptions:
    """The knobs a dig needs, bundled so the TUI can carry them around."""

    limit: int | None = None
    timeout: float = 20.0
    delay: float = 0.5


class TargetNotFound(ValueError):
    """The target is neither a soundcloud.com link nor a file on disk."""

    def __init__(self, target: str) -> None:
        super().__init__(
            f"'{target}' is neither a soundcloud.com link nor an existing file."
        )
        self.target = target


def _notify(on_progress: ProgressHook | None, stage: str, done: int, total: int | None) -> None:
    if on_progress:
        on_progress(stage, done, total)


def dig_url(
    url: str,
    *,
    limit: int | None = None,
    timeout: float = 20.0,
    on_progress: ProgressHook | None = None,
) -> Crate:
    _notify(on_progress, STAGE_LINK, 0, None)
    return soundcloud.collect_tracks(
        url,
        limit=limit,
        timeout=timeout,
        on_progress=lambda done, total: _notify(on_progress, STAGE_TRACKS, done, total),
    )


def dig_html(
    path: Path,
    *,
    limit: int | None = None,
    timeout: float = 20.0,
    delay: float = 0.5,
    on_progress: ProgressHook | None = None,
) -> Crate:
    """Read a saved page.

    Track ids in the page's hydration blob go through the fast batch hydrator;
    only a page without them falls back to fetching each track page in turn.
    """

    path = Path(path)
    _notify(on_progress, STAGE_LINK, 0, None)
    track_ids, track_urls, declared = html_fallback.load_playlist(path)
    if limit is not None:
        track_ids = track_ids[:limit]
        track_urls = track_urls[:limit]

    if track_ids:
        LOGGER.info("Found %s track ids in %s - hydrating through the API", len(track_ids), path)
        tracks = soundcloud.hydrate_ids(
            track_ids,
            timeout=timeout,
            on_progress=lambda done, total: _notify(on_progress, STAGE_TRACKS, done, total),
        )
    elif track_urls:
        LOGGER.info(
            "No track ids in %s - falling back to scraping %s track pages",
            path,
            len(track_urls),
        )
        session = soundcloud.create_requests_session()
        tracks = []
        try:
            for index, track_url in enumerate(track_urls, start=1):
                tracks.append(html_fallback.scrape_track_page(track_url, session, timeout))
                _notify(on_progress, STAGE_PAGES, index, len(track_urls))
                if delay > 0:
                    time.sleep(delay)
        finally:
            session.close()
    else:
        tracks = []

    return Crate(source=str(path), tracks=tracks, title=path.stem, declared_count=declared)


def _expand_one(track: Track, timeout: float) -> bool:
    """Swap a track's link hubs for the shops behind them. True if any changed."""

    changed = False
    # One session per track rather than one shared across the pool: these pages
    # set cookies, and a shared jar would have eight gates writing to it at once.
    session = soundcloud.create_requests_session()
    try:
        for url in links.hub_links(track):
            found = gates.store_links_on_page(url, session, timeout=timeout)
            if not found:
                continue
            for pair in found:
                if pair not in track.extra_links:
                    track.extra_links.append(pair)
            # The hub goes: its shops are on the track directly now, and leaving
            # it would badge the track as a gate that gates nothing.
            if track.purchase_url == url:
                track.purchase_url = None
                track.purchase_title = None
            track.extra_links = [pair for pair in track.extra_links if pair[0] != url]
            changed = True
    finally:
        session.close()
    return changed


def expand_link_hubs(
    tracks: Iterable[Track],
    *,
    timeout: float = 20.0,
    on_progress: ProgressHook | None = None,
) -> int:
    """Read the shops off purchase links that turn out to be lists of shops.

    Mutates the tracks in place and returns how many changed. A crate whose
    purchase links are all recognised shops costs nothing here.
    """

    pending = [track for track in tracks if links.hub_links(track)]
    if not pending:
        return 0

    expanded = 0
    with ThreadPoolExecutor(max_workers=HUB_WORKERS) as pool:
        futures = [pool.submit(_expand_one, track, timeout) for track in pending]
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                if future.result():
                    expanded += 1
            except Exception as exc:  # one unreadable page must not sink the dig
                LOGGER.warning("Could not expand a link hub: %s", exc)
            _notify(on_progress, STAGE_HUBS, done, len(pending))
    return expanded


def dig(
    target: str,
    *,
    limit: int | None = None,
    timeout: float = 20.0,
    delay: float = 0.5,
    on_progress: ProgressHook | None = None,
) -> Crate:
    """Dig a SoundCloud link or a saved HTML file."""

    target = target.strip()
    if soundcloud.is_soundcloud_url(target):
        crate = dig_url(target, limit=limit, timeout=timeout, on_progress=on_progress)
    else:
        path = Path(target).expanduser()
        if not path.exists():
            raise TargetNotFound(target)
        crate = dig_html(
            path, limit=limit, timeout=timeout, delay=delay, on_progress=on_progress
        )

    expand_link_hubs(crate.tracks, timeout=timeout, on_progress=on_progress)
    return crate

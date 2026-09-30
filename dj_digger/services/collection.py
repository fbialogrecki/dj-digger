"""Turning a target into a crate, independent of how progress gets displayed.

Both the CLI (rich progress bar) and the TUI (worker thread plus a status line)
need to do exactly the same work, so the work lives here and the caller supplies
an ``on_progress`` hook.
"""

import logging
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from dj_digger.gates import hubs as gate_hubs

from .. import links, soundcloud
from ..crate_models import CrateRecord
from ..models import Cancelled, Crate, Track, check_cancelled

# stage, done, total (total is None while it is still unknown)
ProgressHook = Callable[[str, int, int | None], None]

STAGE_LINK = "Reading the link"
STAGE_TRACKS = "Fetching tracks"
STAGE_HUBS = "Opening link hubs"

# One page plus a handful of redirects per track, so this is worth doing several
# at a time. Kept modest: these are somebody else's servers.
HUB_WORKERS = 8

# api-v2's retry budget is not the right one for a shop page nobody has updated
# in five years. Five connect retries against a 20 second timeout meant a single
# dead host - smartlinks.cygnusmusic.net, in the playlist this was measured on -
# cost around two minutes all by itself, and a big crate has several.
HUB_RETRIES = 2
HUB_BACKOFF = 0.3
HUB_CONNECT_TIMEOUT = 5.0
# After this many failures a host is written off for the rest of the dig. Two
# rather than one: a single timeout is often the network, not the host.
HOST_FAILURE_LIMIT = 2

LOGGER = logging.getLogger(__name__)


@dataclass
class DigOptions:
    """The knobs a dig needs, bundled so the TUI can carry them around."""

    limit: int | None = None
    timeout: float = 20.0


class TargetNotFound(ValueError):
    """The target is not a soundcloud.com link."""

    def __init__(self, target: str) -> None:
        super().__init__(f"'{target}' is not a soundcloud.com link.")
        self.target = target


def _notify(on_progress: ProgressHook | None, stage: str, done: int, total: int | None) -> None:
    if on_progress:
        on_progress(stage, done, total)


class DeadHosts:
    """Hosts that stopped answering, remembered for the rest of one dig.

    A playlist points at the same handful of smart-link domains over and over, so
    a host that is gone is not one wasted request but one per track that mentions
    it - and each of those costs the full connect timeout. Two strikes rather
    than one, because a single timeout is as often the local network.

    Shared across the hub pool, so every method holds the lock.

    ponytail: requests already in flight when the count reaches the limit still
    run, so the worst case is HUB_WORKERS wasted waits rather than two. Tracking
    in-flight hosts as well would close that, at the price of a pool that queues
    behind its own bookkeeping - not worth it for the requests it would save.
    """

    def __init__(self) -> None:
        self._failures: Counter[str] = Counter()
        self._lock = threading.Lock()

    def written_off(self, url: str) -> bool:
        with self._lock:
            return self._failures[links.host_of(url)] >= HOST_FAILURE_LIMIT

    def failed(self, url: str) -> None:
        host = links.host_of(url)
        with self._lock:
            self._failures[host] += 1
            count = self._failures[host]
        if count == HOST_FAILURE_LIMIT:
            LOGGER.info("%s is not answering - skipping it for the rest of this dig.", host)


def _expand_one(
    track: Track,
    timeout: float,
    dead: DeadHosts,
    cancel: threading.Event | None = None,
) -> bool:
    """Swap a track's link hubs for the shops behind them. True if any changed."""

    changed = False
    # One session per track rather than one shared across the pool: these pages
    # set cookies, and a shared jar would have eight gates writing to it at once.
    # Its retry budget is the one for somebody else's shop page, not for api-v2.
    session = soundcloud.create_requests_session(
        max_retries=HUB_RETRIES, backoff_factor=HUB_BACKOFF
    )
    hub_timeout = (HUB_CONNECT_TIMEOUT, timeout)
    try:
        for url in links.hub_links(track):
            check_cancelled(cancel)
            if dead.written_off(url):
                continue
            inspection = gate_hubs.inspect_link_page(url, session, timeout=hub_timeout)
            if inspection is None:
                dead.failed(url)
                continue
            if not inspection.recognized and not inspection.shops and not inspection.gate_urls:
                continue
            for pair in inspection.shops:
                if pair not in track.extra_links:
                    track.extra_links.append(pair)
                    changed = True
            for gate_url in inspection.gate_urls:
                pair = (gate_url, "Free download")
                if pair not in track.extra_links:
                    track.extra_links.append(pair)
                    changed = True
            # A gate that also sells the release stays beside its shops. A pure
            # smart-link wrapper goes once its concrete shops/nested gate exist.
            if not inspection.keep_original:
                if track.purchase_url == url:
                    track.purchase_url = None
                    track.purchase_title = None
                    changed = True
                old_links = track.extra_links
                track.extra_links = [pair for pair in track.extra_links if pair[0] != url]
                if track.extra_links != old_links:
                    changed = True
                if url in track.description:
                    track.description = track.description.replace(url, "")
                    changed = True
    finally:
        session.close()
    return changed


def expand_link_hubs(
    tracks: Iterable[Track],
    *,
    timeout: float = 20.0,
    on_progress: ProgressHook | None = None,
    cancel: threading.Event | None = None,
) -> int:
    """Read the shops off purchase links that turn out to be lists of shops.

    Mutates the tracks in place and returns how many changed. A crate whose
    purchase links are all recognised shops costs nothing here.
    """

    pending = [track for track in tracks if links.hub_links(track)]
    if not pending:
        return 0

    expanded = 0
    dead = DeadHosts()
    with ThreadPoolExecutor(max_workers=HUB_WORKERS) as pool:
        futures = [pool.submit(_expand_one, track, timeout, dead, cancel) for track in pending]
        for done, future in enumerate(as_completed(futures), start=1):
            if cancel is not None and cancel.is_set():
                # Queued hubs are dropped; the ones already talking to a host
                # finish their own timeout. ponytail: the pool's exit still
                # waits for those, which is at most HUB_WORKERS timeouts.
                pool.shutdown(wait=False, cancel_futures=True)
                raise Cancelled()
            try:
                if future.result():
                    expanded += 1
            except Cancelled:
                raise
            except Exception as exc:  # one unreadable page must not sink the dig
                LOGGER.warning("Could not expand a link hub: %s", exc)
            _notify(on_progress, STAGE_HUBS, done, len(pending))
    return expanded


def dig(
    target: str,
    *,
    limit: int | None = None,
    timeout: float = 20.0,
    on_progress: ProgressHook | None = None,
    cancel: threading.Event | None = None,
    on_tracks: soundcloud.TracksCallback | None = None,
) -> Crate:
    """Dig a SoundCloud link.

    ``cancel`` is checked between requests; a set event raises ``Cancelled``
    rather than returning a partial crate, so nothing half-collected is saved.
    ``on_tracks`` only shows the tracks gathered so far; they are not saved.
    """

    target = target.strip()
    if not soundcloud.is_soundcloud_url(target):
        raise TargetNotFound(target)
    _notify(on_progress, STAGE_LINK, 0, None)
    crate = soundcloud.collect_tracks(
        target,
        limit=limit,
        timeout=timeout,
        on_progress=lambda done, total: _notify(on_progress, STAGE_TRACKS, done, total),
        cancel=cancel,
        on_tracks=on_tracks,
    )
    expand_link_hubs(crate.tracks, timeout=timeout, on_progress=on_progress, cancel=cancel)
    return crate


@dataclass(frozen=True)
class CollectionResult:
    record: CrateRecord | None
    exported: Path | None


class CollectionService:
    def __init__(self, get_db):
        self.get_db = get_db

    @property
    def db(self):
        return self.get_db()

    def read(self, target, options, *, cancel=None, progress=None, on_tracks=None):
        return dig(
            target,
            limit=options.limit,
            timeout=options.timeout,
            on_progress=progress,
            cancel=cancel,
            on_tracks=on_tracks,
        )

    def remember(self, crate, *, generation=None, partial=False):
        incoming = CrateRecord.from_crate(crate, partial=partial).to_json()
        raw = self.db.remember_collection(incoming, generation)
        return CrateRecord.from_json(raw) if raw is not None else None

    def persist(self, crate, generation, export_format, export_path):
        record = self.remember(crate, generation=generation)
        if record is None:
            return CollectionResult(None, None)
        records = links.categorise_all(record.active_tracks)
        exported = (
            links.export_records(records, export_format, export_path)
            if export_format != "none"
            else None
        )
        return CollectionResult(record, exported)

    def collect(self, target, options, generation, export_format, export_path, cancel, progress, on_tracks=None):
        crate = self.read(target, options, cancel=cancel, progress=progress, on_tracks=on_tracks)
        check_cancelled(cancel)
        if not crate.tracks:
            raise ValueError(f"Found no tracks behind {crate.source}")
        return self.persist(crate, generation, export_format, export_path)

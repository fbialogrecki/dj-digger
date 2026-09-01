"""Beatport carts are not automated; its results become playlist entries.

Everything that turns a Beatport request or result into a line Soundiiz can
read lives here, on both sides of the cart batch.
"""

from pathlib import Path
from urllib.parse import urlparse

from .cart_models import CartBatchOutcome, CartRequest, CartResult
from .links import redact_url
from .store_urls import canonical_store_url


def _beatport_playlist_result(
    request: CartRequest,
    label: str,
    reason: str,
    url: str = "",
) -> CartResult:
    """Keep a Beatport request useful when read-only product lookup is blocked."""

    return CartResult(
        request.track.key,
        label,
        "beatport",
        "playlist_ready",
        reason,
        "playlist_ready",
        canonical_store_url(url, "beatport") or "",
    )


SOUNDIIZ_BEATPORT_TRANSFER_URL = "https://soundiiz.com/beatport/import-playlist"


def _beatport_playlist_lines(
    requests: list[CartRequest], outcome: CartBatchOutcome
) -> tuple[str, ...]:
    """Soundiiz-compatible entries, exact when Beatport exposed a track URL."""

    requests_by_target = {
        (request.track.key, store): request
        for request in requests
        for store, _url in request.links
    }
    lines: list[str] = []
    seen_keys: set[str] = set()
    for result in outcome.results:
        if (
            result.store != "beatport"
            or result.code != "playlist_ready"
            or result.track_key in seen_keys
        ):
            continue
        request = requests_by_target.get((result.track_key, "beatport"))
        if request is None:
            continue
        seen_keys.add(result.track_key)
        url = canonical_store_url(result.url, "beatport")
        if url and "/track/" in urlparse(url).path:
            lines.append(redact_url(url))
            continue
        artist = " ".join(request.track.artist.split())
        title = " ".join(request.track.title.split())
        lines.append(f"{artist} - {title}" if artist else title)
    return tuple(line for line in lines if line)


def _write_beatport_playlist(lines: tuple[str, ...], directory: Path) -> Path:
    """Create, never overwrite, a plain-text playlist accepted by Soundiiz."""

    directory.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = "" if index == 1 else f" ({index})"
        path = directory / f"Beatport playlist{suffix}.txt"
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines) + "\n")
        except FileExistsError:
            index += 1
            continue
        return path

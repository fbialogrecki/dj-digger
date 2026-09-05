"""Bypass and resolver module for download gates (Hypeddit, ToneDen, etc.).

Extracts direct file download URLs from gate pages without requiring manual
social media login steps.
"""

import urllib.parse

import requests
from bs4 import BeautifulSoup

from dj_digger.gate_models import HypedditInspection, LinkPageInspection

from ..http import REQUEST_HEADERS, UnsafeRedirect, follow_redirects, is_fetchable
from ..links import SHOP_CATEGORIES, host_of, is_hypeddit_url, redact_url, store_for_url
from .providers import LOGGER, _inspect_hypeddit, _page_anchors

# A hub page listing more than this many wrapped links is a page we have misread.
HUB_REDIRECT_LIMIT = 12


# What the thing you press on a gate says it will do. Several languages, because
# a gate run by a German or Spanish label was invisible to a match on the English
# word alone and got rewritten into a shop list.
DOWNLOAD_WORDS = (
    "download",
    "herunterladen",
    "descargar",
    "télécharger",
    "telecharger",
    "scarica",
    "pobierz",
    "baixar",
)

# Where a page says what pressing it does. Matching the whole document instead
# caught every shop page with the word in a footer, a cookie banner or a script.
ACTION_TAGS = ("a", "button", "input", "label", "h1", "h2", "h3")


def _offers_a_download(soup: BeautifulSoup) -> bool:
    """Whether the page offers to hand over a file at all.

    A real follow-to-download gate says so on the thing you press, and a page
    that says it keeps its gate badge rather than being replaced by the shop it
    also happens to link to. A pure link list never says it.

    ponytail: the words are a fixed list in eight languages, and only the action
    elements are read. A gate whose button is an image, or whose language is not
    here, still reads as a hub. Widening it means the word list, not the shape.
    """

    for tag in soup.find_all(ACTION_TAGS):
        # An <input type=submit> carries its label in value=, not in its text.
        candidates = (tag.get_text(" ", strip=True), tag.get("value") or "", tag.get("title") or "")
        haystack = " ".join(candidates).lower()
        if any(word in haystack for word in DOWNLOAD_WORDS):
            return True
    return False


def _read_page(
    url: str, session: requests.Session, timeout: float
) -> tuple[str, str | None] | None:
    """The page behind a link as ``(landed_url, body)``.

    ``None`` when the host never answered. A ``None`` body when something did
    answer but there is nothing to read: an HTTP error, or a redirect towards
    an address this program refuses to request. This is the function that
    issues the request, so it is the one that has to refuse an address inside
    the user's network - the caller filters too, but not for that.
    """

    try:
        response, landed = follow_redirects(
            session, url, headers=REQUEST_HEADERS, timeout=timeout
        )
    except UnsafeRedirect as exc:
        LOGGER.debug("Refusing to read %s: %s", redact_url(url), exc)
        return url, None
    except requests.RequestException as exc:
        LOGGER.debug(
            "Could not read %s (%s)", redact_url(url), type(exc).__name__
        )
        return None
    if response.status_code >= 400:
        return landed, None
    return landed, response.text


def _unwrap_hub_links(
    session: requests.Session,
    landed: str,
    soup: BeautifulSoup,
    timeout: float,
    seen: set[str],
) -> list[tuple[str, str]]:
    """Shops linked from the page, directly or behind the hub's own redirects.

    Hubs wrap each shop in their own redirect (ampsuite's link-redirect, most
    smart-link services), so the real destination only shows up in a Location
    header.
    """

    hub_host = host_of(landed)
    found: list[tuple[str, str]] = []
    wrapped: list[tuple[str, str]] = []
    for href, text in _page_anchors(landed, soup, seen):
        if store_for_url(href) in SHOP_CATEGORIES:
            found.append((href, text))
        elif host_of(href) == hub_host:
            wrapped.append((href, text))

    for href, text in wrapped[:HUB_REDIRECT_LIMIT]:
        try:
            # stream=True so a link that turns out to be a page rather than a
            # redirect costs the headers and nothing else.
            hop = session.get(
                href,
                headers=REQUEST_HEADERS,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            location = hop.headers.get("Location", "")
            hop.close()
        except requests.RequestException:
            continue
        location = urllib.parse.urljoin(href, location)
        if (
            location
            and is_fetchable(location)
            and store_for_url(location) in SHOP_CATEGORIES
        ):
            found.append((location, text))
    return found


def _classify_link_page(
    soup: BeautifulSoup, hypeddit: HypedditInspection | None, shops: bool
) -> tuple[bool, bool]:
    """``(keep_original, recognized)`` for the page behind a purchase link."""

    if hypeddit:
        # Unknown means protocol drift, not a proven empty wrapper. Keep it so
        # the caller has a diagnostic/manual fallback instead of losing a link.
        keep_original = hypeddit.kind in {
            "gate",
            "hybrid",
            "challenge",
            "unknown",
        } or (_offers_a_download(soup) and not hypeddit.nested_gates)
        return keep_original, hypeddit.kind != "unknown"
    keep_original = _offers_a_download(soup)
    return keep_original, shops or keep_original


def inspect_link_page(
    url: str,
    session: requests.Session,
    timeout: float = 10.0,
) -> LinkPageInspection | None:
    """Inspect one purchase link without losing hybrid gate-and-shop pages.

    Some pages behind a purchase link hand over no file: ampsuite release pages,
    and gates run in smart-link mode, are a list of streaming services and shops.
    Those shops are the point, so they are read off the page and the caller drops
    the hub itself.

    ``None`` rather than ``[]`` when the host never answered, so a caller can tell
    "this page had nothing for us" from "this host is gone" and stop asking. A 404
    is the first kind: something replied.
    """

    page = _read_page(url, session, timeout)
    if page is None:
        return None
    landed, text = page
    if text is None:
        return LinkPageInspection()

    # The landed URL, not url: a hub reached through a redirect wraps its shops
    # in links relative to where it landed, not where it was asked for.
    soup = BeautifulSoup(text, "html.parser")
    hypeddit = _inspect_hypeddit(landed, text, soup) if is_hypeddit_url(landed) else None
    found: list[tuple[str, str]] = list(hypeddit.shops) if hypeddit else []
    seen: set[str] = {
        *(pair[0] for pair in found),
        *((hypeddit.nested_gates if hypeddit else ())),
    }
    found.extend(_unwrap_hub_links(session, landed, soup, timeout, seen))

    # A shop linked both directly and through a wrapper is still one shop.
    unique: dict[str, tuple[str, str]] = {}
    for pair in found:
        unique.setdefault(pair[0], pair)
    keep_original, recognized = _classify_link_page(soup, hypeddit, bool(unique))
    gate_urls = hypeddit.nested_gates if hypeddit else ()
    return LinkPageInspection(
        tuple(unique.values()), gate_urls, keep_original, recognized
    )



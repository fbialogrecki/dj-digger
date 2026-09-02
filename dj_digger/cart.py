"""Safe, user-initiated store cart automation."""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from .beatport_playlist import _beatport_playlist_result
from .browser_session import (  # noqa: F401 - re-exported for the TUI and tests
    AutomationError,
    ChromiumMissing,
    install_chromium,
    launch_persistent_context,
    launch_viewer,
)
from .cart_models import (  # re-exported: the TUI and tests import them from here
    CART_DIAGNOSTICS_KEEP,
    MANUAL_AFTER_UNVERIFIED,
    MANUAL_TABS_MAX,
    VERIFY_BUDGET_SECONDS,
    VERIFY_STAGES,
    ApprovalCallback,
    BrowserNavigationError,
    CartBatchOutcome,
    CartCancelled,
    CartItem,
    CartPlan,
    CartProgress,
    CartRequest,
    CartResult,
    CartResultCode,
    CartStatus,
    CartUnverified,
    ManualCallback,
    PriceQuote,
    ProductUnavailable,
    ProgressCallback,
    SecurityChallengeBlocked,
    StoreProduct,
    StoreStructureError,
    UnsafeMatch,
    UnsafeRedirect,
    UserActionTimeout,
    VerifyOutcome,
    _display_text,
    log_safe_text,
)
from .links import redact_url
from .models import Track
from .paths import data_dir
from .store_match import _normalise, _same_product, match_product
from .store_parse import (
    MAX_HTML_BYTES,
    _currency_from_text,
    _decimal,
    products_from_html,
    purchase_price,
)
from .store_urls import (
    STORE_HOME,
    STORE_HOSTS,
    STORE_LOGIN,
    _direct_beatport_track_url,
    canonical_store_url,
    is_store_url,
)

LOGGER = logging.getLogger(__name__)
NAVIGATION_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 15_000
LOGIN_TIMEOUT_SECONDS = 300


def _emit_progress(callback: ProgressCallback | None, progress: CartProgress) -> None:
    if callback is not None:
        callback(progress)


def _log_cart_result(phase: str, result: CartResult) -> None:
    log = LOGGER.warning if result.status == "failed" else LOGGER.info
    log(
        "Cart %s result: store=%s status=%s code=%s track=%r reason=%s url=%s",
        phase,
        result.store or "none",
        result.status,
        result.code or "none",
        result.track_label,
        log_safe_text(result.reason) if result.reason else "none",
        redact_url(result.url) if result.url else "none",
    )


def _async_cancelled(cancel: asyncio.Event) -> None:
    if cancel.is_set():
        raise CartCancelled("cart operation was cancelled")


async def _visible_async(locator: Any) -> list[Any]:
    matches = []
    for index in range(min(await locator.count(), 500)):
        match = locator.nth(index)
        if await match.is_visible():
            matches.append(match)
    return matches


async def _only_visible_async(locator: Any) -> Any | None:
    try:
        matches = await _visible_async(locator)
    except Exception:
        return None
    return matches[0] if len(matches) == 1 else None


async def _first_visible_async(*locators: Any) -> Any | None:
    for locator in locators:
        match = await _only_visible_async(locator)
        if match is not None:
            return match
    return None


async def _first_visible_match_async(locator: Any) -> Any | None:
    try:
        matches = await _visible_async(locator)
    except Exception:
        return None
    return matches[0] if matches else None


async def _dismiss_bandcamp_cookie_banner(page: Any) -> None:
    """Choose necessary cookies so Bandcamp's footer cannot cover purchase controls."""

    necessary = page.get_by_role(
        "button", name=re.compile(r"^accept necessary only$", re.IGNORECASE)
    )
    try:
        await necessary.first.wait_for(state="visible", timeout=1500)
        await necessary.first.click(timeout=1500)
    except Exception:
        # An existing persistent profile normally has this choice already.
        pass


async def _navigate_async(page: Any, url: str, store: str) -> int | None:
    destination = canonical_store_url(url, store)
    if destination is None:
        LOGGER.warning(
            "Cart navigation refused: store=%s url=%s", store, redact_url(url)
        )
        raise UnsafeRedirect("refusing a non-canonical store URL")
    LOGGER.debug(
        "Cart navigation started: store=%s url=%s", store, redact_url(destination)
    )
    response = None
    try:
        response = await page.goto(
            destination, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
        )
    except Exception as first_error:
        LOGGER.debug(
            "Cart navigation retry: store=%s url=%s error=%s",
            store,
            redact_url(destination),
            type(first_error).__name__,
        )
        try:
            response = await page.goto(
                destination, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
            )
        except Exception as exc:
            LOGGER.warning(
                "Cart navigation failed: store=%s url=%s error=%s",
                store,
                redact_url(destination),
                type(exc).__name__,
            )
            raise BrowserNavigationError(f"could not load {store} product page") from exc
    if not is_store_url(page.url, store):
        LOGGER.warning(
            "Cart navigation escaped store: store=%s final_url=%s",
            store,
            redact_url(page.url),
        )
        raise UnsafeRedirect(f"{store} redirected outside its canonical HTTPS domain")
    status = getattr(response, "status", None)
    LOGGER.debug(
        "Cart navigation finished: store=%s status=%s final_url=%s",
        store,
        status if status is not None else "unknown",
        redact_url(page.url),
    )
    if store == "bandcamp":
        await _dismiss_bandcamp_cookie_banner(page)
    return status


async def _bandcamp_dom_products(page: Any) -> list[StoreProduct]:
    """Bounded DOM fallback when Bandcamp omits the historical metadata blobs."""

    products: dict[str, StoreProduct] = {}
    try:
        data = await page.evaluate(
            """() => {
                const t = globalThis.TralbumData || {};
                const current = t.current || {};
                return {
                    id: String(current.track_id || current.id || ""),
                    title: String(current.title || "").slice(0, 500),
                    artist: String(current.artist || t.artist || "").slice(0, 500),
                    minimum: current.minimum_price,
                    currency: String(current.currency || t.currency || "").slice(0, 12),
                };
            }"""
        )
    except Exception:
        data = {}
    if isinstance(data, dict):
        title = str(data.get("title") or "").strip()
        product_id = str(data.get("id") or "")
        currency = str(data.get("currency") or "").upper()
        minimum = _decimal(data.get("minimum"))
        price_text = ""
        price_region = page.locator("li.buyItem.digital h4.compound-button.main-button").first
        try:
            price_text = await price_region.inner_text()
        except Exception:
            pass
        currency = currency or _currency_from_text(price_text)
        if minimum is None:
            match = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)", price_text)
            minimum = _decimal(match.group(1).replace(",", ".")) if match else None
        if title and "/track/" in urlparse(page.url).path:
            products[page.url] = StoreProduct(
                "bandcamp",
                page.url.split("#", 1)[0],
                product_id if product_id.isdigit() else "",
                title,
                str(data.get("artist") or ""),
                minimum,
                currency,
            )

    anchors = page.locator('a[href*="/track/"]')
    try:
        count = min(await anchors.count(), 500)
    except Exception:
        count = 0
    for index in range(count):
        anchor = anchors.nth(index)
        try:
            href = await anchor.get_attribute("href")
            title = (await anchor.inner_text()).strip()[:500]
        except Exception:
            continue
        url = urljoin(page.url, href or "").split("#", 1)[0]
        if title and is_store_url(url, "bandcamp") and "/track/" in urlparse(url).path:
            products.setdefault(url, StoreProduct("bandcamp", url, "", title))

    if not products and "/track/" in urlparse(page.url).path:
        meta = page.locator('meta[property="og:title"]')
        try:
            title = (await meta.first.get_attribute("content") or "").strip()[:500]
        except Exception:
            title = ""
        if title:
            title = title.split(" | ", 1)[0].strip()
            products[page.url] = StoreProduct("bandcamp", page.url, "", title)
    return list(products.values())


async def _bandcamp_search_candidates_async(
    page: Any, track: Track, cancel: asyncio.Event
) -> tuple[list[StoreProduct], list[str]]:
    """Use Bandcamp's visible autocomplete without entering its CAPTCHA search page."""

    _async_cancelled(cancel)
    search = await _first_visible_match_async(
        page.get_by_placeholder("Search for artist, album, or track")
    )
    if search is None:
        await _navigate_async(page, STORE_HOME["bandcamp"], "bandcamp")
        search = await _first_visible_match_async(
            page.get_by_placeholder("Search for artist, album, or track")
        )
    if search is None:
        return [], []
    query = _display_text(track.title)[:200]
    try:
        await search.fill(query)
    except Exception:
        return [], []
    anchors = page.locator('a[href*="from=search"][href*="bandcamp.com/"]')
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        _async_cancelled(cancel)
        if await anchors.count() > 0:
            break
        await asyncio.sleep(0.2)

    tracks: dict[str, StoreProduct] = {}
    albums: dict[str, None] = {}
    for index in range(min(await anchors.count(), 40)):
        anchor = anchors.nth(index)
        try:
            href = await anchor.get_attribute("href") or ""
            lines = [line.strip() for line in (await anchor.inner_text()).splitlines() if line.strip()]
        except Exception:
            continue
        parsed = urlparse(urljoin(page.url, href))
        url = urlunparse(("https", parsed.hostname or "", parsed.path, "", "", ""))
        if not lines or not is_store_url(url, "bandcamp"):
            continue
        if parsed.path.startswith("/track/"):
            artist = ""
            for line in lines[1:]:
                if line.casefold().startswith("by "):
                    artist = line[3:].strip()
                    break
            tracks.setdefault(
                url,
                StoreProduct("bandcamp", url, "", lines[0][:500], artist[:500]),
            )
        elif parsed.path.startswith("/album/"):
            albums.setdefault(url, None)
    LOGGER.debug(
        "Bandcamp autocomplete candidates: tracks=%d albums=%d query=%r",
        len(tracks),
        len(albums),
        query,
    )
    return list(tracks.values()), list(albums)


async def _resolve_bandcamp_product_async(
    page: Any, track: Track, source_url: str, cancel: asyncio.Event
) -> StoreProduct:
    status = await _navigate_async(page, source_url, "bandcamp")
    unavailable: ProductUnavailable | None = None
    if status not in {404, 410}:
        try:
            return match_product(
                track, await _page_products_async(page, "bandcamp", cancel)
            )
        except ProductUnavailable as exc:
            unavailable = exc
    else:
        unavailable = ProductUnavailable("linked Bandcamp release is no longer available")

    search_tracks, album_urls = await _bandcamp_search_candidates_async(
        page, track, cancel
    )
    try:
        chosen = match_product(track, search_tracks)
    except ProductUnavailable:
        chosen = None
    if chosen is not None:
        LOGGER.info(
            "Bandcamp autocomplete resolved track=%r product=%s",
            track.label,
            redact_url(chosen.url),
        )
        return chosen

    for album_url in album_urls[:3]:
        _async_cancelled(cancel)
        status = await _navigate_async(page, album_url, "bandcamp")
        if status in {404, 410}:
            continue
        try:
            chosen = match_product(
                track, await _page_products_async(page, "bandcamp", cancel)
            )
        except ProductUnavailable:
            continue
        LOGGER.info(
            "Bandcamp autocomplete album resolved track=%r product=%s",
            track.label,
            redact_url(chosen.url),
        )
        return chosen
    raise unavailable or ProductUnavailable("linked release has no exact track")


async def _page_products_async(
    page: Any, store: str, cancel: asyncio.Event | None = None
) -> list[StoreProduct]:
    if not is_store_url(page.url, store):
        raise UnsafeRedirect(f"{store} page left its canonical domain")
    deadline = time.monotonic() + 5.0
    last_error: AutomationError | None = None
    while True:
        if cancel is not None:
            _async_cancelled(cancel)
        try:
            content = await page.content()
            products = products_from_html(content, page.url, store)
        except SecurityChallengeBlocked:
            try:
                await page.bring_to_front()
            except Exception:
                pass
            LOGGER.warning(
                "Cart security challenge blocked automation: store=%s url=%s",
                store,
                redact_url(page.url),
            )
            raise
        except AutomationError as exc:
            last_error = exc
            products = []
        except Exception as exc:
            last_error = StoreStructureError(f"could not inspect {store} product page")
            last_error.__cause__ = exc
            products = []
        if store == "bandcamp":
            dom_products = await _bandcamp_dom_products(page)
            by_identity: dict[tuple[str, str], StoreProduct] = {}
            for product in products:
                identity = (urlparse(product.url).hostname or "", urlparse(product.url).path)
                by_identity[identity] = product
            for product in dom_products:
                identity = (urlparse(product.url).hostname or "", urlparse(product.url).path)
                earlier = by_identity.get(identity)
                if earlier is None:
                    by_identity[identity] = product
                else:
                    by_identity[identity] = StoreProduct(
                        "bandcamp",
                        product.url,
                        product.product_id or earlier.product_id,
                        product.title or earlier.title,
                        product.artist or earlier.artist,
                        product.price if product.price is not None else earlier.price,
                        product.currency or earlier.currency,
                    )
            products = list(by_identity.values())
        if products:
            LOGGER.debug(
                "Cart products found: store=%s count=%d url=%s",
                store,
                len(products),
                redact_url(page.url),
            )
            return products[:500]
        if store == "bandcamp" and urlparse(page.url).path in ("", "/"):
            raise ProductUnavailable(
                "linked Bandcamp page is a storefront, not a release or track"
            )
        if time.monotonic() >= deadline:
            raise StoreStructureError(
                f"{store} product structure changed or is unavailable"
            ) from last_error
        await asyncio.sleep(0.2)


async def _login_visible_async(page: Any) -> bool:
    login_name = re.compile(r"^(?:log ?in|sign in)$", re.IGNORECASE)
    try:
        for locator in (
            page.get_by_role("link", name=login_name),
            page.get_by_role("button", name=login_name),
        ):
            if await _visible_async(locator):
                return True
    except Exception:
        return True
    return False


async def _is_logged_in_async(page: Any, store: str) -> bool:
    if store != "bandcamp":
        # Beatport is never logged into: its anti-bot challenge stops an
        # automated browser at the door, and the app builds a playlist instead.
        return False
    if not is_store_url(page.url, store) or await _login_visible_async(page):
        return False
    names = re.compile(r"^(collection|wishlist|log out)$", re.IGNORECASE)
    return (
        await _first_visible_async(
            page.get_by_role("link", name=names),
            page.get_by_role("button", name=names),
        )
        is not None
    )


async def _login_complete_async(page: Any, store: str) -> bool:
    if await _is_logged_in_async(page, store):
        return True
    return (
        store == "bandcamp"
        and urlparse(page.url).path in ("", "/")
        and not await _login_visible_async(page)
    )


async def _raise_if_security_challenge_async(page: Any, store: str) -> None:
    try:
        content = await page.content()
        products_from_html(content, page.url, store)
    except SecurityChallengeBlocked as exc:
        try:
            await page.bring_to_front()
        except Exception:
            pass
        LOGGER.warning(
            "Store login blocked by security challenge: store=%s url=%s",
            store,
            redact_url(page.url),
        )
        raise SecurityChallengeBlocked(
            f"{store.capitalize()} security verification rejects automated browsers; "
            "automatic login was stopped safely"
        ) from exc
    except AutomationError:
        # Login pages are not product pages. Only the explicit challenge signal
        # matters here; missing product metadata is expected.
        pass


async def _ensure_logins_async(
    pages: dict[str, Any], cancel: asyncio.Event, progress: ProgressCallback | None = None
) -> None:
    pending: dict[str, Any] = {}
    for store, page in pages.items():
        _async_cancelled(cancel)
        _emit_progress(progress, CartProgress("login", 0, len(pages), store=store))
        await _navigate_async(page, STORE_HOME[store], store)
        await _raise_if_security_challenge_async(page, store)
        if await _is_logged_in_async(page, store):
            LOGGER.info("Store login already active: store=%s", store)
            continue
        await _navigate_async(page, STORE_LOGIN[store], store)
        await _raise_if_security_challenge_async(page, store)
        if not await _login_complete_async(page, store):
            LOGGER.info("Waiting for manual store login: store=%s", store)
            pending[store] = page

    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    while pending and time.monotonic() < deadline:
        _async_cancelled(cancel)
        for store, page in tuple(pending.items()):
            if page.is_closed():
                raise AutomationError(f"{store} login window was closed")
            await _raise_if_security_challenge_async(page, store)
            if is_store_url(page.url, store) and await _login_complete_async(page, store):
                LOGGER.info("Store login completed: store=%s", store)
                del pending[store]
        if pending:
            await asyncio.sleep(0.25)
    if pending:
        stores = " and ".join(sorted(pending))
        raise AutomationError(f"timed out waiting for manual {stores} login")


async def _bandcamp_quote_async(page: Any, product: StoreProduct) -> PriceQuote:
    minimum = product.price or Decimal(0)
    name = re.compile(r"^buy digital (?:track|album)$", re.IGNORECASE)
    control = await _first_visible_async(
        page.get_by_role("button", name=name),
        page.get_by_role("link", name=name),
        page.get_by_text(name, exact=True),
    )
    if control is None:
        album_only = re.compile(r"^buy the full digital album$", re.IGNORECASE)
        if await _first_visible_async(
            page.get_by_role("link", name=album_only),
            page.get_by_text(album_only, exact=True),
        ):
            raise ProductUnavailable(
                "exact Bandcamp track is sold only as part of a full album"
            )
    control_text = ""
    if control is not None:
        try:
            region = control.locator("xpath=ancestor::h4[1]")
            control_text = await region.inner_text()
        except Exception:
            try:
                control_text = await control.inner_text()
            except Exception:
                pass
    price_input = await _only_visible_async(
        page.locator('input#userPrice, input[name="userPrice"]')
    )
    if price_input is None and control is not None:
        try:
            await control.click(timeout=ACTION_TIMEOUT_MS, force=True)
            price_input = await _only_visible_async(
                page.locator('input#userPrice, input[name="userPrice"]')
            )
        except Exception:
            price_input = None

    try:
        suggested = _decimal(
            await page.evaluate(
                "() => (globalThis.TralbumData || {}).current?.set_price ?? null"
            )
        )
    except Exception:
        suggested = None
    step = None
    if price_input is not None:
        try:
            input_minimum = _decimal(await price_input.get_attribute("min"))
            input_suggested = _decimal(await price_input.input_value())
            if input_minimum is not None:
                minimum = input_minimum
            if input_suggested is not None:
                suggested = input_suggested
            step = _decimal(await price_input.get_attribute("step"))
        except Exception as exc:
            raise StoreStructureError("Bandcamp price field could not be inspected") from exc
    try:
        selected = purchase_price(minimum, suggested, step)
    except AutomationError as exc:
        raise ProductUnavailable(
            "Bandcamp item has no positive cart price; open this name-your-price item manually"
        ) from exc
    currency = product.currency or _currency_from_text(control_text)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise StoreStructureError("Bandcamp did not expose a verifiable currency")
    editable = price_input is not None or bool(
        re.search(r"\bor more\b", control_text, re.IGNORECASE)
    )
    return PriceQuote(currency, minimum, selected, suggested, step, editable)


async def _quote_async(page: Any, store: str, product: StoreProduct) -> PriceQuote:
    if store == "bandcamp":
        return await _bandcamp_quote_async(page, product)
    if product.price is None or not product.currency:
        raise StoreStructureError("beatport did not expose a verifiable price and currency")
    return PriceQuote(
        product.currency,
        product.price,
        product.price,
        suggested=product.price,
        editable=False,
    )


async def _open_bandcamp_cart_async(page: Any) -> bool:
    sidecart = page.locator("#sidecart")
    try:
        if await sidecart.count() and await sidecart.is_visible():
            return True  # artist pages keep it open once something is in it
    except Exception:
        pass
    cart_link = await _first_visible_match_async(
        page.locator('[data-test="mb-cart"] a[title="cart"], a[href="https://bandcamp.com/cart"]')
    )
    if cart_link is None:
        return False
    try:
        await cart_link.click(timeout=ACTION_TIMEOUT_MS)
    except Exception:
        return False
    deadline = time.monotonic() + 3.0
    sidecart = page.locator("#sidecart")
    while time.monotonic() < deadline:
        try:
            if await sidecart.is_visible() or (
                urlparse(page.url).hostname == "bandcamp.com"
                and urlparse(page.url).path == "/cart"
            ):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return True


# The side cart as Bandcamp renders it (recorded in tests/fixtures/bandcamp):
#   <div id="sidecart_item_N" class="item"> <a class="itemName" href=PRODUCT>…</a>
#   <a class="delete" href="#"><span>x</span></a> <span class="price">…</span>
# The remove control is an anchor with class "delete" and the text "x", not a
# link named "remove" - which is what every verification looked for until the
# first diagnostics dump showed the rows sitting there unrecognised.
SIDECART_ROWS = "#sidecartContents #item_list .item, #item_list .item"
SIDECART_REMOVE = "a.delete"


async def _bandcamp_cart_contains_async(page: Any, item: CartItem) -> bool:
    async def contains_row() -> bool:
        remove_name = re.compile(r"^(remove|x)$", re.IGNORECASE)
        expected = urlparse(item.product_url)
        rows = page.locator(SIDECART_ROWS)
        for index in range(min(await rows.count(), 500)):
            row = rows.nth(index)
            try:
                if not await row.is_visible():
                    continue
                anchors = row.locator("a[href]")
                matched = False
                for position in range(min(await anchors.count(), 10)):
                    href = await anchors.nth(position).get_attribute("href") or ""
                    url = urljoin(page.url, href)
                    found = urlparse(url)
                    if is_store_url(url, "bandcamp") and (found.hostname, found.path) == (
                        expected.hostname,
                        expected.path,
                    ):
                        matched = True
                        break
                if not matched:
                    continue
                removable = row.locator(SIDECART_REMOVE)
                if await removable.count() and await removable.first.is_visible():
                    return True
                if await _first_visible_async(row.get_by_role("link", name=remove_name)):
                    return True
            except Exception:
                continue
        return False

    if await contains_row():
        return True

    if not await _open_bandcamp_cart_async(page):
        return False
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if await contains_row():
            return True
        await asyncio.sleep(0.2)
    return False


async def _bandcamp_cart_count_async(page: Any) -> int | None:
    """How many rows the side cart shows; the menubar badge as a fallback.

    Artist pages have no menubar cart badge at all, so the badge-only count
    was always None there and the count stage never ran.
    """

    try:
        rows = page.locator(SIDECART_ROWS)
        total = await rows.count()
        if total or await page.locator("#sidecart").count():
            return total
    except Exception:
        pass
    locator = page.locator('[data-test="mb-cart"] .menubar-cart-icon text')
    try:
        values = []
        for index in range(min(await locator.count(), 5)):
            text = (await locator.nth(index).text_content() or "").strip()
            match = re.search(r"\d+", text)
            if match:
                values.append(int(match.group()))
        return max(values) if values else None
    except Exception:
        return None


async def _verify_bandcamp_click_async(
    page: Any, item: CartItem, count_before: int | None
) -> VerifyOutcome:
    """Three stages, each on its own clock: the cart count, the side cart, a reload.

    The outer budget (VERIFY_BUDGET_SECONDS) is at least the sum of the stage
    budgets, so a slow reload lands as "reload stage timed out" rather than as
    an anonymous timeout that hides which step was slow.
    """

    started = time.monotonic()
    stages = dict(VERIFY_STAGES)

    async def by_count() -> bool:
        deadline = time.monotonic() + stages["count"]
        while time.monotonic() < deadline:
            count_after = await _bandcamp_cart_count_async(page)
            if (
                count_before is not None
                and count_after is not None
                and count_after > count_before
            ):
                return True
            await asyncio.sleep(0.2)
        return False

    async def by_sidecart() -> bool:
        return await _bandcamp_cart_contains_async(page, item)

    async def by_reload() -> bool:
        await _navigate_async(page, item.product_url, "bandcamp")
        return await _bandcamp_cart_contains_async(page, item)

    for name, check in (("count", by_count), ("sidecart", by_sidecart), ("reload", by_reload)):
        try:
            verified = await asyncio.wait_for(check(), timeout=stages[name])
        except TimeoutError:
            LOGGER.debug("Bandcamp verification stage %s timed out: track=%r", name, item.track_label)
            continue
        if verified:
            LOGGER.debug("Bandcamp cart verified at stage %s: track=%r", name, item.track_label)
            return VerifyOutcome(True, name, time.monotonic() - started)
    return VerifyOutcome(False, "reload", time.monotonic() - started)


async def _cart_contains_async(
    page: Any, item: CartItem, cancel: asyncio.Event, *, navigate: bool = True
) -> bool:
    _async_cancelled(cancel)
    if item.store != "bandcamp":
        raise StoreStructureError(f"{item.store} carts are not automated")
    if navigate:
        await _navigate_async(page, item.product_url, item.store)
    try:
        return await _bandcamp_cart_contains_async(page, item)
    except AutomationError:
        raise
    except Exception as exc:
        raise StoreStructureError(f"could not verify the {item.store} cart") from exc


async def _resolve_cart_item_async(
    page: Any, track: Track, store: str, source_url: str, cancel: asyncio.Event
) -> CartItem:
    _async_cancelled(cancel)
    if store == "bandcamp":
        chosen = await _resolve_bandcamp_product_async(
            page, track, source_url, cancel
        )
    else:
        await _navigate_async(page, source_url, store)
        chosen = match_product(
            track, await _page_products_async(page, store, cancel)
        )
    if urlparse(page.url).path != urlparse(chosen.url).path:
        await _navigate_async(page, chosen.url, store)
        products = await _page_products_async(page, store, cancel)
        verified = match_product(track, products)
        if chosen.product_id and verified.product_id != chosen.product_id:
            raise UnsafeMatch(f"{store} track identity changed after release lookup")
        chosen = verified
    quote = await _quote_async(page, store, chosen)
    unresolved = CartItem(
        track.key,
        _display_text(track.label),
        store,
        source_url,
        chosen.url,
        chosen.product_id,
        chosen.title,
        quote.selected,
        quote.currency,
        False,
        quote.minimum,
        quote.step,
        quote.editable,
    )
    if store == "beatport":
        return unresolved
    return replace(unresolved, already_in_cart=await _cart_contains_async(
        page, unresolved, cancel
    ))


async def _refresh_item_async(
    page: Any, expected: CartItem, cancel: asyncio.Event
) -> CartItem:
    _async_cancelled(cancel)
    await _navigate_async(page, expected.product_url, expected.store)
    products = await _page_products_async(page, expected.store, cancel)
    product = next((candidate for candidate in products if _same_product(expected, candidate)), None)
    if product is None:
        raise StoreStructureError(f"{expected.store} product can no longer be verified")
    quote = await _quote_async(page, expected.store, product)
    return replace(
        expected,
        product_url=product.url,
        product_id=product.product_id or expected.product_id,
        product_title=product.title,
        currency=quote.currency,
        minimum_price=quote.minimum,
        price_step=quote.step,
        price_editable=quote.editable,
    )


def _same_async_snapshot(expected: CartItem, current: CartItem) -> bool:
    expected_minimum = expected.minimum_price
    current_minimum = current.minimum_price
    return (
        expected.store == current.store
        and (
            expected.product_id == current.product_id
            if expected.product_id and current.product_id
            else urlparse(expected.product_url).path == urlparse(current.product_url).path
        )
        and _normalise(expected.product_title) == _normalise(current.product_title)
        and expected_minimum == current_minimum
        and expected.currency == current.currency
        and expected.price >= (current_minimum or Decimal(0))
    )


async def _revalidated(
    page: Any, item: CartItem, cancel: asyncio.Event, reason: str
) -> CartItem | CartResult:
    """The item as the page shows it now, or the skip result when it no longer matches."""

    current = await _refresh_item_async(page, item, cancel)
    if _same_async_snapshot(item, current):
        return current
    return CartResult(
        item.track_key, item.track_label, item.store, "skipped", reason, "price_changed"
    )


async def _add_to_cart_async(page: Any, item: CartItem, cancel: asyncio.Event) -> None:
    _async_cancelled(cancel)
    if not is_store_url(page.url, item.store) or urlparse(page.url).path != urlparse(
        item.product_url
    ).path:
        raise StoreStructureError(f"{item.store} product page changed before the cart click")
    if item.store != "bandcamp":
        raise StoreStructureError(f"{item.store} carts are not automated")
    price_input = await _only_visible_async(
        page.locator('input#userPrice, input[name="userPrice"]')
    )
    if price_input is None:
        name = re.compile(r"^buy digital (?:track|album)$", re.IGNORECASE)
        buy_control = await _first_visible_async(
            page.get_by_role("button", name=name),
            page.get_by_role("link", name=name),
            page.get_by_text(name, exact=True),
        )
        if buy_control is None:
            raise StoreStructureError("Bandcamp purchase control changed or is unavailable")
        await buy_control.click(timeout=ACTION_TIMEOUT_MS)
        price_input = await _only_visible_async(
            page.locator('input#userPrice, input[name="userPrice"]')
        )
    if price_input is not None:
        await price_input.fill(format(item.price, "f"), timeout=ACTION_TIMEOUT_MS)
    elif item.price_editable and item.price > (item.minimum_price or Decimal(0)):
        raise StoreStructureError(
            "Bandcamp no longer exposes the editable price field"
        )
    add_button = await _first_visible_async(
        page.get_by_role("button", name=re.compile(r"^add to cart$", re.IGNORECASE)),
        page.get_by_text(re.compile(r"^add to cart$", re.IGNORECASE), exact=True),
    )
    if add_button is None:
        raise StoreStructureError(f"{item.store} add-to-cart control changed or is unavailable")
    _async_cancelled(cancel)
    try:
        await add_button.click(timeout=ACTION_TIMEOUT_MS)
    except Exception as exc:
        raise CartUnverified(f"{item.store} cart click could not be verified") from exc


_SCRIPT_BODY = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_QUERY_STRING = re.compile(r"\?[^\s\"'<>]*")


def redact_diagnostic_html(html_text: str) -> str:
    """What is safe to keep of a page: no script bodies, no query strings, bounded."""

    text = _SCRIPT_BODY.sub("<script></script>", html_text or "")
    text = _QUERY_STRING.sub("?<redacted>", text)
    return text[:MAX_HTML_BYTES]


def _prune_diagnostics(root: Path) -> None:
    folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    for stale in folders[:-CART_DIAGNOSTICS_KEEP]:
        shutil.rmtree(stale, ignore_errors=True)


async def save_cart_diagnostics(
    page: Any, store: str, product_url: str, code: str
) -> Path | None:
    """Keep a screenshot and a redacted copy of the page when a cart step failed.

    Every "fix" to the Bandcamp flow so far was made without the DOM that
    broke it; this is what the next one starts from. Best effort: a failure
    here is logged and never changes the cart result.
    """

    try:
        root = data_dir() / "cart-diagnostics"
        root.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", urlparse(product_url).path).strip("-")[:40] or "page"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = root / f"{stamp}-{store}-{slug}"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(folder / "page.png"), full_page=False)
        except Exception as exc:
            LOGGER.debug("Cart diagnostics screenshot failed: %s", type(exc).__name__)
        try:
            html_text = await page.content()
            (folder / "page.html").write_text(redact_diagnostic_html(html_text), encoding="utf-8")
        except Exception as exc:
            LOGGER.debug("Cart diagnostics page copy failed: %s", type(exc).__name__)
        meta = {
            "store": store,
            "code": code,
            "product_url": redact_url(product_url),
            "page_url": redact_url(str(getattr(page, "url", "") or "")),
            "saved_at": stamp,
        }
        (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        _prune_diagnostics(root)
        LOGGER.info("Cart diagnostics saved: %s", folder)
        return folder
    except Exception as exc:
        LOGGER.debug("Cart diagnostics could not be saved: %s", type(exc).__name__)
        return None


def _cancelled_result(key: str, label: str, store: str) -> CartResult:
    return CartResult(key, label, store, "failed", "cart operation was cancelled", "cancelled")


BEATPORT_BY_TITLE = "Beatport will match this track by artist and title"


# How a failed preflight lookup is reported, by its most specific exception
# type; anything not listed is an unexpected browser failure.
_PREFLIGHT_FAILURES: dict[type[Exception], tuple[CartStatus, CartResultCode]] = {
    UnsafeMatch: ("skipped", "unsafe_match"),
    UnsafeRedirect: ("failed", "unsafe_redirect"),
    SecurityChallengeBlocked: ("failed", "browser_failure"),
    UserActionTimeout: ("failed", "user_action_timeout"),
    StoreStructureError: ("failed", "store_structure"),
    CartCancelled: ("failed", "cancelled"),
    AutomationError: ("failed", "browser_failure"),
}


def _preflight_failure(
    request: CartRequest, label: str, store: str, url: str, exc: Exception
) -> CartResult:
    """The result for one link that could not be resolved.

    Beatport is never a failure: its lookup is best effort and the playlist
    matches by artist and title instead - unless the person stopped the batch
    or a manual step timed out, which end every store the same way.
    """

    if store == "beatport" and not isinstance(exc, (UserActionTimeout, CartCancelled)):
        reason = str(exc) if isinstance(exc, SecurityChallengeBlocked) else BEATPORT_BY_TITLE
        # A link that redirected off Beatport is not one worth keeping.
        kept_url = "" if isinstance(exc, UnsafeRedirect) else url
        return _beatport_playlist_result(request, label, reason, kept_url)
    spec = next(
        (_PREFLIGHT_FAILURES[cls] for cls in type(exc).__mro__ if cls in _PREFLIGHT_FAILURES),
        None,
    )
    if spec is None:
        return CartResult(
            request.track.key,
            label,
            store,
            "failed",
            "unexpected store interaction failure",
            "browser_failure",
        )
    status, code = spec
    shown_url = ""
    if isinstance(exc, SecurityChallengeBlocked):
        shown_url = canonical_store_url(url, store) or ""
    return CartResult(request.track.key, label, store, status, str(exc), code, shown_url)


class _StructureFailures:
    """Stores whose pages keep losing their shape.

    The same structural error on two different tracks means the store, not
    the track, changed: it is marked broken and the rest of its links are
    reported without another lookup.
    """

    def __init__(self) -> None:
        self._seen: dict[str, tuple[str, set[str]]] = {}
        self.broken: set[str] = set()

    def record(self, store: str, signature: str, track_key: str) -> None:
        earlier, keys = self._seen.get(store, (signature, set()))
        keys = {track_key} if earlier != signature else keys | {track_key}
        self._seen[store] = (signature, keys)
        if len(keys) >= 2:
            self.broken.add(store)

    def clear(self, store: str) -> None:
        self._seen.pop(store, None)


def _split_direct_beatport(
    requests: tuple[CartRequest, ...],
) -> tuple[list[CartResult], list[CartRequest]]:
    """Beatport-only requests with a track URL need no browser: playlist entries at once."""

    direct: list[CartResult] = []
    pending: list[CartRequest] = []
    for request in requests:
        direct_url = (
            _direct_beatport_track_url(request.links[0][1])
            if len(request.links) == 1 and request.links[0][0] == "beatport"
            else None
        )
        if direct_url is None:
            pending.append(request)
            continue
        result = _beatport_playlist_result(
            request,
            _display_text(request.track.label),
            "ready for Beatport playlist transfer",
            direct_url,
        )
        direct.append(result)
        _log_cart_result("playlist", result)
    return direct, pending


def _partition_outcomes(
    approved: CartPlan, results: list[CartResult]
) -> tuple[dict[str, list[CartItem]], dict[str, list[CartItem]]]:
    """Approved items by store: those in the cart, and those whose click is uncertain."""

    successful_keys = {
        (result.track_key, result.store)
        for result in results
        if result.status in {"added", "already_in_cart"}
    }
    uncertain_keys = {
        (result.track_key, result.store)
        for result in results
        if result.code == "cart_unverified"
    }
    successful: dict[str, list[CartItem]] = defaultdict(list)
    uncertain: dict[str, list[CartItem]] = defaultdict(list)
    for item in approved.items:
        target = (item.track_key, item.store)
        if target in successful_keys:
            successful[item.store].append(item)
        elif target in uncertain_keys:
            uncertain[item.store].append(item)
    return successful, uncertain


def _merge_manual(
    results: list[CartResult],
    settled: list[CartResult],
    successful: dict[str, list[CartItem]],
    uncertain: dict[str, list[CartItem]],
) -> list[CartResult]:
    """Fold the manual results in: settled items leave uncertain, verified ones join successful."""

    settled_keys = {(result.track_key, result.store) for result in settled}
    for store, items in list(uncertain.items()):
        uncertain[store] = [
            item for item in items if (item.track_key, item.store) not in settled_keys
        ]
        for item in items:
            if (item.track_key, item.store) in settled_keys and any(
                result.track_key == item.track_key and result.code == "manual_verified"
                for result in settled
            ):
                successful[item.store].append(item)
    return [
        result for result in results if (result.track_key, result.store) not in settled_keys
    ] + settled


def _log_batch_summary(outcome: CartBatchOutcome) -> None:
    counts: dict[str, int] = defaultdict(int)
    for result in outcome.results:
        counts[result.status] += 1
    LOGGER.info(
        "Cart batch finished: added=%d already=%d playlist=%d skipped=%d "
        "failed=%d carts=%s",
        counts["added"],
        counts["already_in_cart"],
        counts["playlist_ready"],
        counts["skipped"],
        counts["failed"],
        ",".join(outcome.cart_stores) or "none",
    )


async def _manual_result(
    item: CartItem, page: Any, done: bool, cancel: asyncio.Event
) -> CartResult:
    """One read-only cart check decides what the person's own click achieved."""

    if not done or cancel.is_set():
        return CartResult(
            item.track_key,
            item.track_label,
            item.store,
            "failed",
            "manual completion was given up",
            "cart_unverified",
        )
    try:
        present = await _cart_contains_async(page, item, asyncio.Event())
    except Exception:
        present = False
    return CartResult(
        item.track_key,
        item.track_label,
        item.store,
        "manual" if present else "failed",
        "added by hand in the browser" if present else "not found in the cart after manual completion",
        "manual_verified" if present else "manual_unverified",
    )


class CartBrowserSession:
    """One lazy Playwright context shared by all cart batches in a TUI run.

    The work - product lookup, revalidation, the cart clicks - runs headless on
    the persistent profile, out of the user's way. A window opens only when
    there is something for them to do or see: the finished cart, or items to
    finish by hand. That window is a separate browser carrying the session's
    cookies (``browser_session.launch_viewer``), because relaunching the one
    profile from headless to headed raced Chromium's lock and lost batches.
    """

    def __init__(self, profile: Path | None = None) -> None:
        self.profile = profile
        self._playwright = None
        self._context = None
        # The visible browser and its context, once something needed showing.
        self._viewer: tuple[Any, Any] | None = None
        self._owned_pages: list[Any] = []
        self._cart_pages: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def _context_closed(self, _context: Any) -> None:
        self._context = None
        self._owned_pages.clear()
        self._cart_pages.clear()

    async def _playwright_handle(self) -> Any:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise AutomationError(
                "the required Playwright dependency is missing; reinstall dj-soundcloud-digger"
            ) from exc
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        return self._playwright

    async def _ensure_context(self) -> Any:
        if self._context is not None and not self._context.is_closed():
            return self._context
        playwright = await self._playwright_handle()
        context = await launch_persistent_context(playwright, self.profile, headless=True)
        context.on("close", self._context_closed)
        self._context = context
        self._owned_pages = list(context.pages[:1])
        for page in self._owned_pages:
            self._instrument_page(page)
        LOGGER.info("Store browser session ready: pages=%d", len(self._owned_pages))
        return context

    def _instrument_page(self, page: Any) -> Any:
        def response_received(response: Any) -> None:
            status = getattr(response, "status", 0)
            if status >= 400:
                LOGGER.debug(
                    "Store browser HTTP error: status=%s url=%s",
                    status,
                    redact_url(getattr(response, "url", "")),
                )

        def console_message(message: Any) -> None:
            kind = getattr(message, "type", "")
            if kind not in {"error", "warning"}:
                return
            location = getattr(message, "location", {}) or {}
            LOGGER.debug(
                "Store browser console message: type=%s source=%s",
                kind,
                redact_url(str(location.get("url") or "")),
            )

        try:
            page.on("response", response_received)
            page.on("console", console_message)
            page.on(
                "crash",
                lambda *_args: LOGGER.warning("Store browser page crashed"),
            )
        except Exception:
            # Minimal fake pages in tests do not implement Playwright events.
            pass
        return page

    async def _viewer_context(self) -> Any:
        """The visible browser context, opened on first need with the session's cookies."""

        if self._viewer is not None:
            browser, context = self._viewer
            try:
                if browser.is_connected():
                    return context
            except Exception:
                pass
            self._viewer = None
        cookies: list[dict[str, Any]] = []
        if self._context is not None and not self._context.is_closed():
            try:
                cookies = await self._context.cookies()
            except Exception:
                cookies = []
        playwright = await self._playwright_handle()
        browser, context = await launch_viewer(playwright, cookies)
        self._viewer = (browser, context)
        try:
            browser.on("disconnected", lambda *_args: setattr(self, "_viewer", None))
        except Exception:
            pass
        return context

    async def _close_viewer(self) -> None:
        viewer, self._viewer = self._viewer, None
        self._cart_pages.clear()
        if viewer is None:
            return
        browser, _context = viewer
        try:
            await browser.close()
        except Exception:
            pass

    async def _work_pages(self, count: int = 2) -> list[Any]:
        context = await self._ensure_context()
        pages = [page for page in self._owned_pages if not page.is_closed()]
        while len(pages) < count:
            page = self._instrument_page(await context.new_page())
            pages.append(page)
        self._owned_pages = pages
        return pages[:count]

    async def _replace_page(self, old: Any) -> Any:
        context = await self._ensure_context()
        try:
            await old.close()
        except Exception:
            pass
        new = self._instrument_page(await context.new_page())
        self._owned_pages = [new if page is old else page for page in self._owned_pages]
        return new

    async def _close_context(self) -> None:
        """Close the browser window; Playwright itself stays up for the next one."""

        context, self._context = self._context, None
        if context is not None:
            try:
                await context.close(reason="dj-digger cart session closed")
            except Exception:
                pass
        self._owned_pages.clear()
        self._cart_pages.clear()

    async def close(self) -> None:
        async with self._lock:
            await self._close_context()
            await self._close_viewer()
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    async def reset_profile(self) -> None:
        async with self._lock:
            await self._close_context()
            target = Path(self.profile) if self.profile else data_dir() / "store-browser"
            parent = data_dir().resolve()
            if target.name != "store-browser" or target.parent.resolve() != parent:
                raise AutomationError("refusing to reset an unexpected browser profile path")
            if target.is_symlink():
                raise AutomationError("refusing to reset a symlinked browser profile")
            if not target.exists():
                return
            quarantine = target.with_name(f".store-browser-reset-{os.getpid()}-{time.time_ns()}")
            target.rename(quarantine)
            try:
                shutil.rmtree(quarantine)
            except Exception:
                quarantine.rename(target)
                raise
            target.mkdir(parents=True, mode=0o700)
            if os.name != "nt":
                target.chmod(0o700)

    async def setup_logins(
        self,
        stores: Iterable[str],
        cancel: asyncio.Event,
        progress: ProgressCallback | None = None,
    ) -> None:
        wanted = tuple(dict.fromkeys(store for store in stores if store in STORE_HOSTS))
        if not wanted:
            return
        async with self._lock:
            # A login needs the real profile on screen, so the hidden context
            # steps aside and the profile is opened headed for as long as the
            # login takes; the next batch reopens it hidden.
            await self._close_context()
            playwright = await self._playwright_handle()
            context = await launch_persistent_context(playwright, self.profile, headless=False)
            pages = [self._instrument_page(await context.new_page()) for _ in wanted]
            try:
                await _ensure_logins_async(
                    dict(zip(wanted, pages, strict=True)), cancel, progress
                )
            finally:
                try:
                    await context.close(reason="dj-digger login finished")
                except Exception:
                    pass

    async def check_logins(self, stores: Iterable[str]) -> dict[str, bool]:
        wanted = tuple(dict.fromkeys(store for store in stores if store in STORE_HOSTS))
        if not wanted:
            return {}
        async with self._lock:
            context = await self._ensure_context()
            pages = [self._instrument_page(await context.new_page()) for _ in wanted]
            try:
                states: dict[str, bool] = {}
                for store, page in zip(wanted, pages, strict=True):
                    await _navigate_async(page, STORE_HOME[store], store)
                    states[store] = await _is_logged_in_async(page, store)
                return states
            finally:
                for page in pages:
                    if not page.is_closed():
                        await page.close()

    async def _preflight_one(
        self,
        page: Any,
        request: CartRequest,
        cancel: asyncio.Event,
        failures: _StructureFailures,
    ) -> tuple[CartItem | CartResult, Any]:
        """Resolve one request over its links; the first eligible store wins.

        Returns the item, or the result that stands in for it, and the page to
        keep working on: a page that lost its shape is swapped for a fresh one.
        """

        label = _display_text(request.track.label)
        if cancel.is_set():
            store = request.links[0][0] if request.links else ""
            return _cancelled_result(request.track.key, label, store), page
        unavailable: list[str] = []
        for store, url in request.links:
            if store in failures.broken:
                if store == "beatport":
                    return _beatport_playlist_result(request, label, BEATPORT_BY_TITLE, url), page
                return CartResult(
                    request.track.key,
                    label,
                    store,
                    "failed",
                    "store automation stopped after repeated structural failures",
                    "store_structure",
                ), page
            try:
                try:
                    item = await _resolve_cart_item_async(
                        page, request.track, store, url, cancel
                    )
                except StoreStructureError:
                    await save_cart_diagnostics(page, store, url, "store_structure")
                    page = await self._replace_page(page)
                    item = await _resolve_cart_item_async(
                        page, request.track, store, url, cancel
                    )
            except ProductUnavailable as exc:
                if store == "beatport":
                    return _beatport_playlist_result(request, label, BEATPORT_BY_TITLE, url), page
                unavailable.append(str(exc))
                continue
            except Exception as exc:
                if not isinstance(exc, (UnsafeMatch, AutomationError)):
                    LOGGER.error(
                        "Unexpected cart preflight error: store=%s track=%r error=%s",
                        store,
                        label,
                        type(exc).__name__,
                    )
                elif isinstance(exc, StoreStructureError) and store != "beatport":
                    failures.record(store, str(exc), request.track.key)
                return _preflight_failure(request, label, store, url, exc), page
            failures.clear(store)
            return item, page
        return CartResult(
            request.track.key,
            label,
            request.links[-1][0] if request.links else "",
            "skipped",
            unavailable[-1] if unavailable else "no eligible Bandcamp or Beatport link",
            "unavailable",
        ), page

    async def _preflight(
        self,
        requests: tuple[CartRequest, ...],
        pages: list[Any],
        cancel: asyncio.Event,
        progress: ProgressCallback | None,
    ) -> CartPlan:
        queue: asyncio.Queue[tuple[int, CartRequest] | None] = asyncio.Queue()
        for index, request in enumerate(requests):
            queue.put_nowait((index, request))
        for _ in pages:
            queue.put_nowait(None)

        outcomes: list[CartItem | CartResult | None] = [None] * len(requests)
        failures = _StructureFailures()
        completed = 0

        async def worker(worker_index: int, page: Any) -> None:
            nonlocal completed
            while (entry := await queue.get()) is not None:
                index, request = entry
                try:
                    outcome, page = await self._preflight_one(page, request, cancel, failures)
                    pages[worker_index] = page
                    outcomes[index] = outcome
                    if isinstance(outcome, CartResult):
                        _log_cart_result("preflight", outcome)
                    else:
                        LOGGER.info(
                            "Cart preflight ready: store=%s track=%r product=%s price=%s %s",
                            outcome.store,
                            outcome.track_label,
                            redact_url(outcome.product_url),
                            outcome.price,
                            outcome.currency,
                        )
                finally:
                    completed += 1
                    _emit_progress(
                        progress,
                        CartProgress(
                            "preflight",
                            completed,
                            len(requests),
                            track_label=_display_text(request.track.label),
                        ),
                    )
                    queue.task_done()
            queue.task_done()

        async with asyncio.TaskGroup() as group:
            for index, page in enumerate(pages):
                group.create_task(worker(index, page))
        return CartPlan(
            tuple(outcome for outcome in outcomes if isinstance(outcome, CartItem)),
            tuple(outcome for outcome in outcomes if isinstance(outcome, CartResult)),
        )

    async def _execute_store(
        self,
        store: str,
        items: list[CartItem],
        page: Any,
        cancel: asyncio.Event,
        progress: ProgressCallback | None,
        progress_state: list[int],
        total: int,
    ) -> list[CartResult]:
        results: list[CartResult] = []
        unverified = 0
        clicked = False

        def _failed(item: CartItem, reason: str, code: CartResultCode) -> CartResult:
            return CartResult(item.track_key, item.track_label, store, "failed", reason, code)

        async def _click_and_verify(
            item: CartItem, ready: CartItem, count_before: int | None
        ) -> CartResult:
            nonlocal clicked, unverified
            await _add_to_cart_async(page, ready, cancel)
            clicked = True
            outcome = await asyncio.wait_for(
                _verify_bandcamp_click_async(page, ready, count_before),
                timeout=VERIFY_BUDGET_SECONDS,
            )
            if outcome.verified:
                return CartResult(item.track_key, item.track_label, store, "added")
            unverified += 1
            await save_cart_diagnostics(page, store, item.product_url, "cart_unverified")
            return _failed(
                item,
                f"cart click was not verified (gave up at the {outcome.stage} "
                f"stage after {outcome.elapsed:.0f}s); it was not retried",
                "cart_unverified",
            )

        for index, item in enumerate(items):
            if cancel.is_set():
                results.extend(
                    _cancelled_result(pending.track_key, pending.track_label, store)
                    for pending in items[index:]
                )
                break
            if unverified >= MANUAL_AFTER_UNVERIFIED:
                # Two clicks this store would not confirm: stop clicking. The
                # rest go to the person at the window (see _finish_manually).
                results.extend(
                    _failed(
                        pending,
                        "left for manual completion after repeated unverified clicks",
                        "cart_unverified",
                    )
                    for pending in items[index:]
                )
                break
            clicked = False
            try:
                current = await _revalidated(
                    page, item, cancel, "product identity or price changed after preflight"
                )
                if isinstance(current, CartResult):
                    results.append(current)
                    continue
                if await _cart_contains_async(page, current, cancel):
                    results.append(
                        CartResult(item.track_key, item.track_label, store, "already_in_cart")
                    )
                    continue
                ready = await _revalidated(
                    page, item, cancel, "product identity or price changed after cart inspection"
                )
                if isinstance(ready, CartResult):
                    results.append(ready)
                    continue
                count_before = (
                    await _bandcamp_cart_count_async(page) if store == "bandcamp" else None
                )
                results.append(await _click_and_verify(item, ready, count_before))
            except CartUnverified as exc:
                unverified += 1
                await save_cart_diagnostics(page, store, item.product_url, "cart_unverified")
                results.append(_failed(item, str(exc), "cart_unverified"))
            except CartCancelled:
                if clicked:
                    results.append(_failed(item, "cart state is uncertain", "cart_unverified"))
                else:
                    results.append(_cancelled_result(item.track_key, item.track_label, store))
            except UnsafeRedirect as exc:
                results.append(_failed(item, str(exc), "unsafe_redirect"))
            except AutomationError as exc:
                code = "cart_unverified" if clicked else "store_structure"
                results.append(_failed(item, str(exc), code))
            except Exception as exc:
                LOGGER.error(
                    "Unexpected cart execution error: store=%s track=%r error=%s",
                    store,
                    item.track_label,
                    type(exc).__name__,
                )
                code = "cart_unverified" if clicked else "browser_failure"
                results.append(_failed(item, "unexpected store interaction failure", code))
            finally:
                if results and results[-1].track_key == item.track_key:
                    _log_cart_result("execution", results[-1])
                progress_state[0] += 1
                _emit_progress(
                    progress,
                    CartProgress(
                        "adding",
                        progress_state[0],
                        total,
                        store,
                        item.track_label,
                    ),
                )
        return results

    async def _show_store_cart(
        self, store: str, items: list[CartItem], verified: list[CartItem]
    ) -> tuple[Any, list[CartItem]]:
        """A visible page on the store's cart, and the verified items it fails to show."""

        # Shown in the visible browser, which carries the session's cookies;
        # the hidden context stays as it is.
        viewer = await self._viewer_context()
        page = self._instrument_page(await viewer.new_page())
        await _navigate_async(page, items[-1].product_url, store)
        await _open_bandcamp_cart_async(page)
        deadline = time.monotonic() + 3.0
        while True:
            present = await asyncio.gather(
                *(_bandcamp_cart_contains_async(page, item) for item in verified)
            )
            missing = [
                item for item, found in zip(verified, present, strict=True) if not found
            ]
            if not missing or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
        return page, missing

    async def _open_final_carts(
        self,
        successful: dict[str, list[CartItem]],
        keep_open: dict[str, list[CartItem]] | None = None,
    ) -> tuple[tuple[str, ...], tuple[CartResult, ...]]:
        opened: list[str] = []
        warnings: list[CartResult] = []
        self._cart_pages.clear()
        targets: dict[str, list[CartItem]] = defaultdict(list)
        for source in (successful, keep_open or {}):
            for store, items in source.items():
                for item in items:
                    if item not in targets[store]:
                        targets[store].append(item)
        for store, items in targets.items():
            if not items:
                continue
            try:
                page, missing = await self._show_store_cart(
                    store, items, successful.get(store, [])
                )
                if missing:
                    warnings.append(
                        CartResult(
                            "",
                            "Bandcamp cart view",
                            store,
                            "failed",
                            f"final cart view did not expose {len(missing)} verified item(s)",
                            "cart_view_incomplete",
                        )
                    )
                await page.bring_to_front()
            except Exception as exc:
                LOGGER.warning(
                    "Could not expose final cart: store=%s error=%s",
                    store,
                    type(exc).__name__,
                )
                warnings.append(
                    CartResult(
                        "",
                        f"{store.capitalize()} cart view",
                        store,
                        "failed",
                        "the cart window could not be shown; the additions above still stand",
                        "cart_view_failed",
                    )
                )
                continue
            self._cart_pages[store] = page
            opened.append(store)
        return tuple(opened), tuple(warnings)

    async def run_batch(
        self,
        requests: Iterable[CartRequest],
        cancel: asyncio.Event,
        *,
        approve: ApprovalCallback,
        progress: ProgressCallback | None = None,
        manual: ManualCallback | None = None,
    ) -> CartBatchOutcome:
        request_list = tuple(requests)
        async with self._lock:
            LOGGER.info("Cart batch started: tracks=%d", len(request_list))
            _emit_progress(progress, CartProgress("starting", 0, len(request_list)))
            direct_results, pending_requests = _split_direct_beatport(request_list)
            if not pending_requests:
                _emit_progress(
                    progress,
                    CartProgress("ready", len(request_list), len(request_list)),
                )
                return CartBatchOutcome(tuple(direct_results))
            pages = await self._work_pages(2)
            try:
                plan = await self._preflight(
                    tuple(pending_requests), pages, cancel, progress
                )
            except UserActionTimeout as exc:
                return CartBatchOutcome(
                    tuple(direct_results) + tuple(
                        CartResult(
                            request.track.key,
                            request.track.label,
                            request.links[0][0] if request.links else "",
                            "failed",
                            str(exc),
                            "user_action_timeout",
                        )
                        for request in pending_requests
                    )
                )
            plan = CartPlan(plan.items, tuple(direct_results) + plan.results)
            if cancel.is_set():
                cancelled = tuple(
                    _cancelled_result(item.track_key, item.track_label, item.store)
                    for item in plan.items
                )
                return CartBatchOutcome(plan.results + cancelled, cancelled=True)
            if not plan.items:
                LOGGER.info(
                    "Cart batch stopped after preflight: ready=0 results=%d",
                    len(plan.results),
                )
                return CartBatchOutcome(plan.results)
            LOGGER.info(
                "Cart preflight completed: ready=%d results=%d",
                len(plan.items),
                len(plan.results),
            )
            _emit_progress(progress, CartProgress("approval", 0, len(plan.items)))
            approved = await approve(plan)
            if approved is None or cancel.is_set():
                LOGGER.info("Cart batch approval cancelled")
                return CartBatchOutcome(plan.results, cancelled=True)
            LOGGER.info("Cart plan approved: items=%d", len(approved.items))

            by_store: dict[str, list[CartItem]] = defaultdict(list)
            for item in approved.items:
                by_store[item.store].append(item)
            store_pages = {
                store: pages[index]
                for index, store in enumerate(by_store)
            }
            progress_state = [0]
            all_results = list(approved.results)
            if "beatport" in by_store:
                playlist_items = [
                    CartResult(
                        item.track_key,
                        item.track_label,
                        "beatport",
                        "playlist_ready",
                        "ready for Beatport playlist transfer",
                        "playlist_ready",
                        item.product_url,
                    )
                    for item in by_store.pop("beatport")
                ]
                all_results.extend(playlist_items)
                for result in playlist_items:
                    _log_cart_result("playlist", result)
            tasks = [
                self._execute_store(
                    store,
                    items,
                    store_pages[store],
                    cancel,
                    progress,
                    progress_state,
                    len(approved.items),
                )
                for store, items in by_store.items()
            ]
            for store_results in await asyncio.gather(*tasks):
                all_results.extend(store_results)
            successful, uncertain = _partition_outcomes(approved, all_results)
            if uncertain and manual is not None and not cancel.is_set():
                settled = await self._finish_manually(uncertain, manual, cancel, progress)
                all_results = _merge_manual(all_results, settled, successful, uncertain)
            opened, warnings = await self._open_final_carts(successful, uncertain)
            all_results.extend(warnings)
            _emit_progress(progress, CartProgress("ready", len(approved.items), len(approved.items)))
            candidates = tuple(item for items in uncertain.values() for item in items)
            outcome = CartBatchOutcome(tuple(all_results), opened, cancel.is_set(), candidates)
            _log_batch_summary(outcome)
            return outcome

    async def _stage_manual_page(self, context: Any, item: CartItem) -> Any:
        """A visible tab on the product, Buy control expanded and the price filled in."""

        page = self._instrument_page(await context.new_page())
        try:
            await _navigate_async(page, item.product_url, item.store)
            await _dismiss_bandcamp_cookie_banner(page)
            name = re.compile(r"^buy digital (?:track|album)$", re.IGNORECASE)
            buy_control = await _first_visible_async(
                page.get_by_role("button", name=name),
                page.get_by_role("link", name=name),
                page.get_by_text(name, exact=True),
            )
            if buy_control is not None:
                await buy_control.click(timeout=ACTION_TIMEOUT_MS, force=True)
            price_input = await _only_visible_async(
                page.locator('input#userPrice, input[name="userPrice"]')
            )
            if price_input is not None:
                await price_input.fill(format(item.price, "f"), timeout=ACTION_TIMEOUT_MS)
        except Exception as exc:
            LOGGER.debug(
                "Manual staging could not prepare %r: %s", item.track_label, type(exc).__name__
            )
        return page

    async def _finish_manually(
        self,
        uncertain: dict[str, list[CartItem]],
        manual: ManualCallback,
        cancel: asyncio.Event,
        progress: ProgressCallback | None,
    ) -> list[CartResult]:
        """Open the unverified products for the person at the window, then check.

        Each page gets its Buy control expanded and the price filled, exactly
        as preflight does; the Add-to-cart click is theirs. Once they say they
        are done, one read-only cart check per item decides the result.
        """

        items = [item for store_items in uncertain.values() for item in store_items]
        items = items[:MANUAL_TABS_MAX]
        if not items:
            return []
        _emit_progress(progress, CartProgress("manual", 0, len(items)))
        try:
            context = await self._viewer_context()
        except AutomationError as exc:
            return [
                CartResult(item.track_key, item.track_label, item.store, "failed",
                           f"could not open a browser window: {exc}", "cart_unverified")
                for item in items
            ]
        staged: list[tuple[CartItem, Any]] = []
        for item in items:
            if cancel.is_set():
                break
            staged.append((item, await self._stage_manual_page(context, item)))
        if staged:
            try:
                await staged[0][1].bring_to_front()
            except Exception:
                pass
        done = await manual([item for item, _page in staged])
        results: list[CartResult] = []
        for item, page in staged:
            results.append(await _manual_result(item, page, done, cancel))
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
        for result in results:
            _log_cart_result("manual", result)
        return results

    async def finish_manually(
        self, items: list[CartItem], manual: ManualCallback, cancel: asyncio.Event
    ) -> list[CartResult]:
        """The result screen's 'Finish in browser' for items a batch left uncertain."""

        by_store: dict[str, list[CartItem]] = defaultdict(list)
        for item in items:
            by_store[item.store].append(item)
        async with self._lock:
            return await self._finish_manually(by_store, manual, cancel, None)

    async def focus_carts(self) -> None:
        async with self._lock:
            for page in self._cart_pages.values():
                if not page.is_closed():
                    await page.bring_to_front()

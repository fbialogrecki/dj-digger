"""Safe, user-initiated store cart automation."""

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from dj_digger.automation_errors import AutomationError
from dj_digger.diagnostics import log_safe_text

from ..cart_models import (
    CART_DIAGNOSTICS_KEEP,
    VERIFY_STAGES,
    BrowserNavigationError,
    CartCancelled,
    CartItem,
    CartProgress,
    CartResult,
    CartUnverified,
    PriceQuote,
    ProductUnavailable,
    ProgressCallback,
    SecurityChallengeBlocked,
    StoreProduct,
    StoreStructureError,
    UnsafeMatch,
    UnsafeRedirect,
    VerifyOutcome,
    _display_text,
)
from ..links import redact_url
from ..models import Track
from ..paths import data_dir
from ..store_match import _normalise, _same_product, match_product
from ..store_parse import (
    MAX_HTML_BYTES,
    _currency_from_text,
    _decimal,
    products_from_html,
    purchase_price,
)
from ..store_urls import (
    STORE_HOME,
    STORE_LOGIN,
    canonical_store_url,
    is_store_url,
)

LOGGER = logging.getLogger(__name__)
NAVIGATION_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 15_000
LOGIN_TIMEOUT_SECONDS = 300
BANDCAMP_CART_URL = "https://bandcamp.com/cart"


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


async def _poll_async(check: Callable[[], Awaitable[bool]], seconds: float) -> bool:
    """Whether *check* came true within *seconds*, asking every 0.2s."""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await check():
            return True
        await asyncio.sleep(0.2)
    return False


def _role_pair(page: Any, name: re.Pattern[str]) -> tuple[Any, Any]:
    """The button and the link a store may render one control as."""

    return page.get_by_role("button", name=name), page.get_by_role("link", name=name)


async def _surface_challenge_async(page: Any, store: str, event: str) -> None:
    """Bring the challenge page to the front and log it; the caller decides what to raise."""

    with contextlib.suppress(Exception):
        await page.bring_to_front()
    LOGGER.warning("%s: store=%s url=%s", event, store, redact_url(page.url))


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
    for attempt in (1, 2):
        try:
            response = await page.goto(
                destination, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
            )
            break
        except Exception as exc:
            if attempt == 1:
                LOGGER.debug(
                    "Cart navigation retry: store=%s url=%s error=%s",
                    store,
                    redact_url(destination),
                    type(exc).__name__,
                )
                continue
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

    async def suggestions_shown() -> bool:
        _async_cancelled(cancel)
        return await anchors.count() > 0

    await _poll_async(suggestions_shown, 5.0)

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
            await _surface_challenge_async(
                page, store, "Cart security challenge blocked automation"
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
                by_identity[identity] = (
                    product if earlier is None else product.merged_over(earlier)
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
        for locator in _role_pair(page, login_name):
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
    return await _first_visible_async(*_role_pair(page, names)) is not None


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
        await _surface_challenge_async(page, store, "Store login blocked by security challenge")
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


BUY_CONTROL = re.compile(r"^buy digital (?:track|album)$", re.IGNORECASE)
PRICE_INPUT = 'input#userPrice, input[name="userPrice"]'


async def _buy_control_async(page: Any) -> Any | None:
    return await _first_visible_async(
        *_role_pair(page, BUY_CONTROL), page.get_by_text(BUY_CONTROL, exact=True)
    )


async def _price_input_async(page: Any) -> Any | None:
    return await _only_visible_async(page.locator(PRICE_INPUT))


async def _expand_buy_async(page: Any, *, force: bool) -> Any | None:
    """The price field, opening the Buy dialog first when it is not shown yet.

    None when the dialog has no price field; StoreStructureError when there
    is no Buy control to open it with.
    """

    price_input = await _price_input_async(page)
    if price_input is not None:
        return price_input
    control = await _buy_control_async(page)
    if control is None:
        raise StoreStructureError("Bandcamp purchase control changed or is unavailable")
    await control.click(timeout=ACTION_TIMEOUT_MS, force=force)
    return await _price_input_async(page)


async def _price_field_values(
    price_input: Any,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Bandcamp's price field as (minimum, current value, step); None where unset."""

    try:
        return (
            _decimal(await price_input.get_attribute("min")),
            _decimal(await price_input.input_value()),
            _decimal(await price_input.get_attribute("step")),
        )
    except Exception as exc:
        raise StoreStructureError("Bandcamp price field could not be inspected") from exc


async def _bandcamp_quote_async(page: Any, product: StoreProduct) -> PriceQuote:
    minimum = product.price or Decimal(0)
    control = await _buy_control_async(page)
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
    try:
        price_input = await _expand_buy_async(page, force=True)
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
        input_minimum, input_suggested, step = await _price_field_values(price_input)
        if input_minimum is not None:
            minimum = input_minimum
        if input_suggested is not None:
            suggested = input_suggested
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
    sidecart = page.locator("#sidecart")

    async def cart_shown() -> bool:
        try:
            return await sidecart.is_visible() or (
                urlparse(page.url).hostname == "bandcamp.com"
                and urlparse(page.url).path == "/cart"
            )
        except Exception:
            return False

    await _poll_async(cart_shown, 3.0)
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
    return await _poll_async(contains_row, 3.0)


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

    async def count_grew() -> bool:
        count_after = await _bandcamp_cart_count_async(page)
        return count_before is not None and count_after is not None and count_after > count_before

    async def by_count() -> bool:
        return await _poll_async(count_grew, stages["count"])

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
        # A storefront side cart may expose only that seller's items. Use the
        # global cart before deciding that a purchase can safely be skipped.
        await _navigate_async(page, BANDCAMP_CART_URL, item.store)
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
    current_minimum = current.minimum_price
    return (
        expected.store == current.store
        and _same_product(expected, current)
        and _normalise(expected.product_title) == _normalise(current.product_title)
        and expected.minimum_price == current_minimum
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
    price_input = await _expand_buy_async(page, force=False)
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



"""Beatport cart through the user's own signed-in browser.

The page states it relies on are reproduced in tests/fixtures/beatport. The
product is never found by artist and title here: the track is the exact id from
a direct link or from the release page's own rows, and the add control is the
one of that id (spec section 3.6).
"""

import asyncio
import re
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from ..cart_models import (
    VERIFY_STAGES,
    CartItem,
    CartUnverified,
    NotLoggedIn,
    StoreProduct,
    StoreStructureError,
    VerifyOutcome,
)
from ..store_match import _normalise
from ..store_parse import _currency_from_text, _decimal, beatport_title
from ..store_urls import _beatport_track_id
from ..webext.page import ACTION_TIMEOUT_MS
from . import bandcamp

BEATPORT_CART_URL = "https://www.beatport.com/cart"
# Release rows (narrow windows render list items) and the cart page's rows.
ROWS = '[data-testid="tracks-table-row"], [data-testid="tracks-list-item"]'
# The add control, or what it turns into once the track is in the cart.
ADD = 'button[aria-label^="Add track"], button[aria-label$="is already in your cart"]'
# A track page's own control; the recommendations below it are rows with theirs.
MAIN_ADD = f':is({ADD}):not([data-testid="tracks-table-row"] *):not([data-testid="tracks-list-item"] *)'
CART_LINK = 'a[data-testid="header-mainnav-link-cart"]'
# A real cart row has a "Move to cart" control; a recommendation has "Add track ...".
CART_ROW_MARK = '[aria-label="Move to cart"]'
# Add track 'SPACESHIP' (Original Mix) by Artist, Other to cart. Price: $1.49
ADD_LABEL = re.compile(r"Add track '(.*)'(?: \((.*)\))? by (.*) to cart\. Price: (.+)", re.DOTALL)
IN_CART_LABEL = re.compile(r"Track '.*' is already in your cart", re.DOTALL)
MAX_ROWS = 200


def parse_add_label(label: str) -> tuple[str, str, Decimal, str] | None:
    """(title, artist, price, currency) of an add control's label, None when it is not one."""

    match = ADD_LABEL.fullmatch((label or "").strip())
    amount = re.search(r"\d+(?:[.,]\d+)?", match.group(4)) if match else None
    price = _decimal(amount.group().replace(",", ".")) if amount else None
    currency = _currency_from_text(match.group(4)) if match else ""
    if match is None or price is None or not currency:
        return None
    title, version, artist, _price = match.groups()
    return beatport_title(title, version), artist, price, currency


def _on_track_page(page: Any, product_id: str) -> bool:
    return "/track/" in urlparse(page.url).path and _beatport_track_id(page.url) == product_id


async def _label(buttons: Any) -> str:
    info = await buttons.all_texts_and_attrs(["aria-label"], text=False, limit=1)
    return (info[0]["attrs"].get("aria-label") or "") if info else ""


async def _row_tracks(page: Any, row: Any) -> list[tuple[str, str]]:
    """(url, numeric id) of every track link in a row."""

    anchors = await row.locator('a[href*="/track/"]').all_texts_and_attrs(
        ["href"], text=False, limit=10
    )
    urls = [urljoin(page.url, a["attrs"].get("href") or "").split("#", 1)[0] for a in anchors]
    return [(url, _beatport_track_id(url)) for url in urls]


async def add_control_async(page: Any, product_id: str) -> tuple[Any, str] | None:
    """The add button of exactly this product with its label, None when the page has none."""

    if _on_track_page(page, product_id):
        buttons = page.locator(MAIN_ADD)
        label = await _label(buttons)
        return (buttons.first, label) if label else None
    rows = page.locator(ROWS)
    for index in range(min(await rows.count(), MAX_ROWS)):
        row = rows.nth(index)
        if product_id not in [found for _url, found in await _row_tracks(page, row)]:
            continue
        buttons = row.locator(ADD)
        label = await _label(buttons)
        if label:
            return buttons.first, label
    return None


def _product(label: str, url: str, product_id: str) -> StoreProduct | None:
    parsed = parse_add_label(label)
    if parsed is None or not product_id.isdigit():
        return None
    title, artist, price, currency = parsed
    url = urlparse(url)._replace(query="", fragment="").geturl()
    return StoreProduct("beatport", url, product_id, title, artist, price, currency)


async def with_labels_async(page: Any, products: list[StoreProduct]) -> list[StoreProduct]:
    """The page's products with the version, artists and price its add controls state.

    Release anchors only carry the bare title, so every mix of a release looks
    alike and has no price until its row's own control is read.
    """

    by_id = {product.product_id: product for product in products}
    found: list[StoreProduct | None] = []
    try:
        pid = _beatport_track_id(page.url)
        if _on_track_page(page, pid):
            found.append(_product(await _label(page.locator(MAIN_ADD)), page.url, pid))
        rows = page.locator(ROWS)
        for index in range(min(await rows.count(), MAX_ROWS)):
            row = rows.nth(index)
            tracks = await _row_tracks(page, row)
            if tracks:
                found.append(_product(await _label(row.locator(ADD)), *tracks[0]))
    except Exception:
        pass  # the anchors alone still identify; the quote then reports the missing price
    for product in found:
        if product is not None:
            earlier = by_id.get(product.product_id)
            by_id[product.product_id] = product.merged_over(earlier) if earlier else product
    return list(by_id.values())


async def _cart_rows(page: Any, product_id: str) -> tuple[bool, int]:
    """Whether a real cart row holds the product, and how many real rows there are."""

    rows = page.locator(ROWS)
    real = 0
    for index in range(min(await rows.count(), MAX_ROWS)):
        row = rows.nth(index)
        if not await row.locator(CART_ROW_MARK).count():
            continue
        real += 1
        if product_id in [found for _url, found in await _row_tracks(page, row)]:
            return True, real
    return False, real


async def _in_cart_here(page: Any, product_id: str) -> bool:
    """Whether the product's own page says it is in the cart.

    Only a yes counts: before the page hydrates the control offers "Add" either way.
    """

    if not _on_track_page(page, product_id):
        return False
    found = await add_control_async(page, product_id)
    return bool(found and IN_CART_LABEL.fullmatch(found[1].strip()))


async def _open_cart(page: Any) -> None:
    """The cart page; /cart moves on to /cart/cart/<id> client-side, or home when signed out."""

    await bandcamp._navigate_async(page, BEATPORT_CART_URL, "beatport")

    async def settled() -> bool:
        return urlparse(page.url).path != "/cart"

    await bandcamp._poll_async(settled, 8.0)
    if not urlparse(page.url).path.startswith("/cart/"):
        raise NotLoggedIn("not logged in to Beatport")


async def cart_contains_async(page: Any, item: CartItem, *, navigate: bool = True) -> bool:
    """Whether the signed-in account's cart holds the item; NotLoggedIn when it is not signed in."""

    if navigate:
        if await _in_cart_here(page, item.product_id):
            return True
        await _open_cart(page)
    count = await cart_count_async(page)
    if count == 0:
        return False
    deadline = time.monotonic() + 8.0
    while True:
        found, real = await _cart_rows(page, item.product_id)
        # The rows render after the counter; stop once all of them are there.
        if found or (real and real >= (count or 1)) or time.monotonic() >= deadline:
            return found
        await asyncio.sleep(0.2)


async def cart_count_async(page: Any) -> int | None:
    """The header counter ("Open cart, 1 items"), None where the page has none."""

    try:
        match = re.search(r"(\d+) items?", await _label(page.locator(CART_LINK)))
    except Exception:
        return None
    return int(match.group(1)) if match else None


async def add_to_cart_async(page: Any, item: CartItem, cancel: asyncio.Event) -> None:
    """Click the control of this product, after its label still matches the preflight snapshot."""

    found = await add_control_async(page, item.product_id)
    parsed = parse_add_label(found[1]) if found else None
    if found is None or parsed is None:
        raise StoreStructureError("Beatport add-to-cart control changed or is unavailable")
    title, _artist, price, currency = parsed
    if (price, currency) != (item.price, item.currency) or _normalise(title) != _normalise(
        item.product_title
    ):
        raise StoreStructureError("Beatport add-to-cart control no longer matches the preflight")
    bandcamp._async_cancelled(cancel)
    try:
        await found[0].click(timeout=ACTION_TIMEOUT_MS)
    except Exception as exc:
        raise CartUnverified("beatport cart click could not be verified") from exc


async def verify_click_async(
    page: Any, item: CartItem, count_before: int | None
) -> VerifyOutcome:
    """The header counter first, then the cart page, each on its own clock."""

    started = time.monotonic()
    stages = dict(VERIFY_STAGES)

    async def by_count() -> bool:
        async def grew() -> bool:
            after = await cart_count_async(page)
            return count_before is not None and after is not None and after > count_before

        return await bandcamp._poll_async(grew, stages["count"])

    async def by_cart() -> bool:
        await _open_cart(page)
        return await cart_contains_async(page, item, navigate=False)

    for name, check, seconds in (
        ("count", by_count, stages["count"]),
        ("cart", by_cart, stages["reload"]),
    ):
        try:
            verified = await asyncio.wait_for(check(), timeout=seconds)
        except TimeoutError:
            continue
        if verified:
            return VerifyOutcome(True, name, time.monotonic() - started)
    return VerifyOutcome(False, "cart", time.monotonic() - started)

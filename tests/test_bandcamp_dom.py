"""Bandcamp selectors against recorded pages, in a real Chromium, with no network.

Opt-in (``-m bandcamp_dom``): needs Playwright's Chromium and the recorded
pages described in ``tests/fixtures/bandcamp/README.md``. Everything the
browser asks for is answered from those files or refused, so nothing leaves
the machine.
"""

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest

from dj_digger import cart
from dj_digger.models import Track

pytestmark = pytest.mark.bandcamp_dom

FIXTURES = Path(__file__).parent / "fixtures" / "bandcamp"
PAGES = {
    "track_page": "track_page.html",
    "buy_open": "track_page_buy_open.html",
    "sidecart": "sidecart_after_add.html",
    "cart": "cart_page.html",
}


def _fixture(name: str) -> str:
    path = FIXTURES / PAGES[name]
    if not path.is_file():
        pytest.skip(f"recorded page missing: {path.name} (see tests/fixtures/bandcamp/README.md)")
    return path.read_text(encoding="utf-8")


def _product_url() -> str:
    path = FIXTURES / "product_url.txt"
    if not path.is_file():
        pytest.skip("tests/fixtures/bandcamp/product_url.txt is missing")
    return path.read_text(encoding="utf-8").strip()


async def _serve(state: str):
    """A headless page whose every request is answered from the recording."""

    playwright_async = pytest.importorskip("playwright.async_api")
    body = _fixture(state)
    product_url = _product_url()
    playwright = await playwright_async.async_playwright().start()
    if not Path(playwright.chromium.executable_path).is_file():
        await playwright.stop()
        pytest.skip("Playwright Chromium is not installed")
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context()

    async def answer(route, request):
        if request.url.split("?")[0] in {product_url, "https://bandcamp.com/cart"}:
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        else:
            await route.abort()

    await context.route("**/*", answer)
    page = await context.new_page()
    await page.goto(product_url, wait_until="domcontentloaded")
    return playwright, browser, page, product_url


def _run(coro):
    return asyncio.run(coro)


def test_page_products_read_tralbum_and_price_button():
    async def scenario():
        playwright, browser, page, product_url = await _serve("track_page")
        try:
            products = await cart._page_products_async(page, "bandcamp")
        finally:
            await browser.close()
            await playwright.stop()
        assert products, "the recorded track page exposes a product"
        assert any(
            cart.canonical_store_url(product.url, "bandcamp") == cart.canonical_store_url(product_url, "bandcamp")
            for product in products
        )

    _run(scenario())


def test_quote_reads_user_price_min_and_step():
    async def scenario():
        playwright, browser, page, product_url = await _serve("buy_open")
        try:
            products = await cart._page_products_async(page, "bandcamp")
            product = next(
                p for p in products
                if cart.canonical_store_url(p.url, "bandcamp") == cart.canonical_store_url(product_url, "bandcamp")
            )
            quote = await cart._bandcamp_quote_async(page, product)
        finally:
            await browser.close()
            await playwright.stop()
        assert quote.currency
        assert quote.minimum >= Decimal("0")
        assert quote.selected >= quote.minimum

    _run(scenario())


def test_add_click_then_sidecart_row_verifies():
    async def scenario():
        playwright, browser, page, product_url = await _serve("sidecart")
        try:
            products = await cart._page_products_async(page, "bandcamp")
            product = next(
                p for p in products
                if cart.canonical_store_url(p.url, "bandcamp") == cart.canonical_store_url(product_url, "bandcamp")
            )
            track = Track(title=product.title, artist=product.artist, permalink_url="https://soundcloud.com/x/y")
            item = cart.CartItem(
                track_key=track.key,
                track_label=track.label,
                store="bandcamp",
                source_url=product_url,
                product_url=cart.canonical_store_url(product.url, "bandcamp") or product_url,
                product_id=product.product_id,
                product_title=product.title,
                price=product.price or Decimal("0"),
                currency=product.currency or "USD",
            )
            present = await cart._bandcamp_cart_contains_async(page, item)
            count = await cart._bandcamp_cart_count_async(page)
        finally:
            await browser.close()
            await playwright.stop()
        assert present, "the recorded side cart shows a removable row for the product"
        assert count == 1

    _run(scenario())


def test_cart_count_from_menubar_icon():
    async def scenario():
        playwright, browser, page, _url = await _serve("sidecart")
        try:
            count = await cart._bandcamp_cart_count_async(page)
        finally:
            await browser.close()
            await playwright.stop()
        assert count is None or count >= 1

    _run(scenario())

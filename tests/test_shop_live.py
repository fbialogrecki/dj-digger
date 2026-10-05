"""Opt-in, read-only checks against public store pages."""

import asyncio
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest

from dj_digger import links, store_parse, store_urls
from dj_digger.models import Track
from dj_digger.stores import bandcamp as bandcamp_adapter
from dj_digger.webext import link
from dj_digger.webext import page as webpage

pytestmark = pytest.mark.shop_live


@pytest.fixture
def store_context() -> Iterator[Any]:
    """The user's own browser through the extension; needs the app closed and the extension connected."""

    try:
        with webpage.session(visible=False) as context:
            yield context
    except link.ExtensionMissing:
        pytest.skip("connect the dj-digger browser extension to run shop_live tests")


def _public_html(context: Any, url: str, store: str) -> str:
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        assert store_urls.is_store_url(page.url, store), f"unsafe redirect to {links.redact_url(page.url)}"
        return page.content()
    finally:
        page.close()


def test_bandcamp_public_release_contract(store_context: Any) -> None:
    url = "https://spydnb.bandcamp.com/album/fever"
    products = store_parse.products_from_html(_public_html(store_context, url, "bandcamp"), url, "bandcamp")

    titles = {product.title for product in products}
    assert {"Fever", "Fever (Instrumental)"} <= titles
    assert all(product.product_id.isdigit() for product in products)
    assert all("/track/" in product.url for product in products)


def test_bandcamp_album_only_track_contract(store_context: Any) -> None:
    url = "https://lithe.bandcamp.com/track/haptic-feedback-igloo-demo"
    html = _public_html(store_context, url, "bandcamp")
    products = store_parse.products_from_html(html, url, "bandcamp")

    assert len(products) == 1
    assert products[0].price is None
    assert "not available for individual purchase" in html.lower()


def test_bandcamp_async_adapter_resolves_current_dom_without_cart_mutation() -> None:
    async def check() -> None:
        async with webpage.async_session(visible=False) as context:
            page = await context.new_page()
            try:
                item = await bandcamp_adapter._resolve_cart_item_async(
                    page,
                    Track(
                        title="Phil:osophy - Remember",
                        artist="UKF",
                        permalink_url="https://soundcloud.com/ukf/philosophy-remember",
                        id=1,
                    ),
                    "bandcamp",
                    "https://integralrecords.bandcamp.com/album/int041-heavy-hearts-ep",
                    asyncio.Event(),
                )
            finally:
                await page.close()
        assert item.product_url.endswith("/track/remember")
        assert item.product_id == "3157363108"
        assert item.currency == "GBP"
        assert item.price > 0
        assert item.already_in_cart is False

    try:
        asyncio.run(check())
    except link.ExtensionMissing:
        pytest.skip("connect the dj-digger browser extension to run shop_live tests")


def test_bandcamp_async_adapter_prefers_current_track_price_over_download_action() -> None:
    async def check() -> None:
        async with webpage.async_session(visible=False) as context:
            page = await context.new_page()
            try:
                item = await bandcamp_adapter._resolve_cart_item_async(
                    page,
                    Track(
                        title="Impak // Fractals // C4CDIGUK045",
                        artist="Cause4Concern",
                        permalink_url="https://soundcloud.com/cause4concern/impak-fractals",
                        id=2,
                    ),
                    "bandcamp",
                    "https://cause4concern.bandcamp.com/album/the-truth-is-out-there-e-p",
                    asyncio.Event(),
                )
            finally:
                await page.close()
        assert item.product_url.endswith("/track/fractals")
        assert item.product_title == "Fractals"
        assert item.price == Decimal("1.99")
        assert item.currency == "GBP"

    try:
        asyncio.run(check())
    except link.ExtensionMissing:
        pytest.skip("connect the dj-digger browser extension to run shop_live tests")


def test_bandcamp_moved_lights_on_is_now_album_only(store_context: Any) -> None:
    # The old cross-label example no longer offers an individual purchase.
    # Recovery matching remains covered by the controlled test_cart fixtures.
    url = "https://flexoutaudio.bandcamp.com/track/lights-on"
    products = store_parse.products_from_html(_public_html(store_context, url, "bandcamp"), url, "bandcamp")
    product = next(product for product in products if product.url == url)
    assert product.title == "Lights On"
    assert product.artist == "Revan & Ollie Norton"
    assert product.price is None


def test_beatport_public_release_contract(store_context: Any) -> None:
    url = "https://www.beatport.com/release/cl4sh-ep/6785416"
    html = _public_html(store_context, url, "beatport")
    lowered = html.lower()
    if any(
        marker in lowered
        for marker in (
            "captcha",
            "access denied",
            "just a moment",
            "cf-chl-",
            "performing security verification",
            "ray id:",
        )
    ):
        pytest.skip("Beatport presented an anti-bot challenge; the test never bypasses it")

    products = store_parse.products_from_html(html, url, "beatport")
    assert products, "Beatport release no longer exposes semantic track products"
    assert all(product.product_id.isdigit() for product in products)
    assert all("/track/" in product.url for product in products)

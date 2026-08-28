"""Opt-in, read-only checks against public store pages."""

import asyncio
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from dj_digger import cart, links
from dj_digger.models import Track

pytestmark = pytest.mark.shop_live


@pytest.fixture(scope="module")
def store_context(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="Playwright is required for store contract tests"
    )
    profile = Path(tmp_path_factory.mktemp("shop-live-profile"))
    try:
        with sync_api.sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=True,
                    locale="en-US",
                    accept_downloads=False,
                    chromium_sandbox=True,
                )
            except sync_api.Error as exc:
                if "executable doesn't exist" in str(exc).lower():
                    pytest.skip(
                        "run 'uv run python -m playwright install chromium' "
                        "to enable shop_live tests"
                    )
                raise
            try:
                yield context
            finally:
                context.close()
    finally:
        assert profile.is_dir()


def _public_html(context: Any, url: str, store: str) -> str:
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        assert cart.is_store_url(page.url, store), f"unsafe redirect to {links.redact_url(page.url)}"
        return page.content()
    finally:
        page.close()


def test_bandcamp_public_release_contract(store_context: Any) -> None:
    url = "https://spydnb.bandcamp.com/album/fever"
    products = cart.products_from_html(_public_html(store_context, url, "bandcamp"), url, "bandcamp")

    titles = {product.title for product in products}
    assert {"Fever", "Fever (Instrumental)"} <= titles
    assert all(product.product_id.isdigit() for product in products)
    assert all("/track/" in product.url for product in products)


def test_bandcamp_album_only_track_contract(store_context: Any) -> None:
    url = "https://lithe.bandcamp.com/track/haptic-feedback-igloo-demo"
    html = _public_html(store_context, url, "bandcamp")
    products = cart.products_from_html(html, url, "bandcamp")

    assert len(products) == 1
    assert products[0].price is None
    assert "not available for individual purchase" in html.lower()


def test_bandcamp_async_adapter_resolves_current_dom_without_cart_mutation() -> None:
    async_api = pytest.importorskip(
        "playwright.async_api", reason="Playwright is required for store contract tests"
    )

    async def check() -> None:
        async with async_api.async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(locale="en-US", accept_downloads=False)
            try:
                item = await cart._resolve_cart_item_async(
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
                await browser.close()
        assert item.product_url.endswith("/track/remember")
        assert item.product_id == "3157363108"
        assert item.currency == "GBP"
        assert item.price > 0
        assert item.already_in_cart is False

    asyncio.run(check())


def test_bandcamp_async_adapter_prefers_current_track_price_over_download_action() -> None:
    async_api = pytest.importorskip(
        "playwright.async_api", reason="Playwright is required for store contract tests"
    )

    async def check() -> None:
        async with async_api.async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(locale="en-US", accept_downloads=False)
            try:
                item = await cart._resolve_cart_item_async(
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
                await browser.close()
        assert item.product_url.endswith("/track/fractals")
        assert item.product_title == "Fractals"
        assert item.price == Decimal("1.99")
        assert item.currency == "GBP"

    asyncio.run(check())


def test_bandcamp_visible_autocomplete_recovers_a_moved_cross_label_track() -> None:
    async_api = pytest.importorskip(
        "playwright.async_api", reason="Playwright is required for store contract tests"
    )

    async def check() -> None:
        async with async_api.async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(locale="en-US", accept_downloads=False)
            try:
                product = await cart._resolve_bandcamp_product_async(
                    page,
                    Track(
                        title="Revan & Ollie Norton - Lights On",
                        artist="Flexout Audio",
                        permalink_url="https://soundcloud.com/flexoutaudio/lights-on",
                        id=3,
                    ),
                    "https://revanbristol.bandcamp.com/album/lights-on",
                    asyncio.Event(),
                )
            finally:
                await browser.close()
        assert product.url == "https://flexoutaudio.bandcamp.com/track/lights-on"
        assert product.title == "Lights On"
        assert product.artist == "Revan & Ollie Norton"

    asyncio.run(check())


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

    products = cart.products_from_html(html, url, "beatport")
    assert products, "Beatport release no longer exposes semantic track products"
    assert all(product.product_id.isdigit() for product in products)
    assert all("/track/" in product.url for product in products)

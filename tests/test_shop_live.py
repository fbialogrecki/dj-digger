"""Opt-in, read-only checks against public store pages."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dj_digger import cart

pytestmark = pytest.mark.shop_live


@pytest.fixture(scope="module")
def store_context(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="install the shop extra to run store contract tests"
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
                    pytest.skip("run 'playwright install chromium' to enable shop_live tests")
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
        assert cart.is_store_url(page.url, store), f"unsafe redirect to {cart.redact_url(page.url)}"
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

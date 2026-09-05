"""One real Bandcamp add, verify, remove - opt-in, and only ever on a free track.

Runs with ``-m shop_mutate`` when ``DJ_DIGGER_SHOP_MUTATE_URL`` names a
name-your-price track page. A throwaway profile is used, the item is removed
from the side cart afterwards, and checkout is never approached.
"""

import asyncio
import os
from decimal import Decimal
from pathlib import Path

import pytest

from dj_digger import browser_session, store_urls
from dj_digger.models import Track
from dj_digger.services import purchases as cart
from dj_digger.stores import bandcamp as bandcamp_adapter

pytestmark = pytest.mark.shop_mutate


def test_name_your_price_add_verify_remove(tmp_path: Path) -> None:
    url = os.environ.get("DJ_DIGGER_SHOP_MUTATE_URL", "").strip()
    if not url:
        pytest.skip("set DJ_DIGGER_SHOP_MUTATE_URL to a name-your-price Bandcamp track")
    if not store_urls.is_store_url(url, "bandcamp"):
        pytest.skip("DJ_DIGGER_SHOP_MUTATE_URL is not a canonical Bandcamp URL")
    playwright_async = pytest.importorskip("playwright.async_api")

    async def scenario():
        playwright = await playwright_async.async_playwright().start()
        try:
            context = await browser_session.launch_persistent_context(
                playwright, tmp_path / "profile", headless=True
            )
            try:
                page = await context.new_page()
                cancel = asyncio.Event()
                await bandcamp_adapter._navigate_async(page, url, "bandcamp")
                products = await bandcamp_adapter._page_products_async(page, "bandcamp")
                product = next(
                    p for p in products
                    if store_urls.canonical_store_url(p.url, "bandcamp") == store_urls.canonical_store_url(url, "bandcamp")
                )
                track = Track(
                    title=product.title, artist=product.artist, permalink_url="https://soundcloud.com/x/probe"
                )
                item = await bandcamp_adapter._resolve_cart_item_async(page, track, "bandcamp", url, cancel)
                assert item.price == (item.minimum_price or Decimal("0")), "only a free add is attempted"
                assert not item.already_in_cart
                before = await bandcamp_adapter._bandcamp_cart_count_async(page)
                await bandcamp_adapter._add_to_cart_async(page, item, cancel)
                outcome = await bandcamp_adapter._verify_bandcamp_click_async(page, item, before)
                assert outcome.verified, f"gave up at stage {outcome.stage}"
                assert await bandcamp_adapter._open_bandcamp_cart_async(page)
                remove = page.locator(bandcamp_adapter.SIDECART_ROWS).first.locator(bandcamp_adapter.SIDECART_REMOVE).first
                await remove.click(timeout=cart.ACTION_TIMEOUT_MS)
                await asyncio.sleep(1)
                assert not await bandcamp_adapter._bandcamp_cart_contains_async(page, item)
            finally:
                await context.close()
        finally:
            await playwright.stop()

    asyncio.run(scenario())

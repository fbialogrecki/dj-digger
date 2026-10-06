"""Beatport cart steps against the synthetic page states in tests/fixtures/beatport.

The fake page answers the extension's locator calls from the fixture HTML, so
the selectors run against real markup; nothing here opens a browser.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from test_cart import track

from dj_digger import cart_models
from dj_digger.gui.backend import result_status
from dj_digger.services import purchases as cart
from dj_digger.stores import bandcamp as bandcamp_adapter
from dj_digger.stores import beatport

FIXTURES = Path(__file__).parent / "fixtures" / "beatport"
BASE = "https://www.beatport.com"
RELEASE = f"{BASE}/release/test-release/9001"
TRACK = f"{BASE}/track/signal/100001"
CART = f"{BASE}/cart/cart/111"
LOGIN = f"{BASE}/?next=/cart"


class Loc:
    def __init__(self, page, elements):
        self.page, self.elements = page, elements

    @property
    def first(self):
        return Loc(self.page, self.elements[:1])

    def nth(self, index):
        return Loc(self.page, self.elements[index : index + 1])

    def locator(self, css):
        found = {}
        for element in self.elements:
            found.update({id(match): match for match in element.select(css)})
        return Loc(self.page, list(found.values()))

    async def count(self):
        return len(self.elements)

    async def is_visible(self):
        return bool(self.elements)

    async def get_attribute(self, name, timeout=None):
        return self.elements[0].get(name)

    async def click(self, timeout=None, force=False):
        self.page.shop.clicked.append(self.elements[0].get("aria-label"))
        self.page.shop.on_click(self.elements[0])

    async def all_texts_and_attrs(self, attrs, text=True, limit=500):
        return [
            {"text": el.get_text(" ", strip=True), "attrs": {name: el.get(name) for name in attrs}}
            for el in self.elements[:limit]
        ]


class Shop:
    """What Beatport shows: the page states, the account and the cart."""

    def __init__(self, *, logged_in=True, update_count=True, update_cart=True, price="$1.49"):
        self.logged_in, self.update_count, self.update_cart = logged_in, update_count, update_cart
        self.price, self.in_cart, self.count, self.clicked = price, False, 0, []

    def on_click(self, element):
        if "'Signal' (Original Mix)" in element.get("aria-label", ""):
            self.count += self.update_count
            self.in_cart = self.in_cart or self.update_cart

    def html(self, url):
        read = lambda name: (FIXTURES / name).read_text(encoding="utf-8")  # noqa: E731
        if url == RELEASE:
            return read("release_page.html")
        if url == TRACK:
            text = read("track_page.html").replace("Price: $1.49", f"Price: {self.price}", 1)
            return text.replace("Open cart, 0 items", f"Open cart, {self.count} items")
        if url == CART:
            return read("cart_page.html" if self.in_cart else "cart_page_without_item.html")
        return "<html><body><a data-testid='header-login-button'>Log in</a></body></html>"


class FakePage:
    def __init__(self, shop, url="about:blank"):
        self.shop, self.url = shop, url

    async def goto(self, url, wait_until=None, timeout=None):
        if url == f"{BASE}/cart":
            url = CART if self.shop.logged_in else LOGIN
        self.url = url
        return SimpleNamespace(status=200)

    async def content(self):
        return self.shop.html(self.url)

    def locator(self, css):
        return Loc(self, BeautifulSoup(self.shop.html(self.url), "html.parser").select(css))

    async def bring_to_front(self):
        pass


@pytest.fixture(autouse=True)
def quick_and_private(monkeypatch):
    async def no_diagnostics(*_args):
        return None

    monkeypatch.setattr(bandcamp_adapter, "save_cart_diagnostics", no_diagnostics)
    monkeypatch.setattr(beatport, "VERIFY_STAGES", (("count", 0.3), ("sidecart", 1.0), ("reload", 2.0)))


def resolve(shop, url, title="Signal"):
    page = FakePage(shop)
    item = asyncio.run(
        bandcamp_adapter._resolve_cart_item_async(page, track(title), "beatport", url, asyncio.Event())
    )
    return page, item


def test_label_gives_title_artists_price_and_currency():
    assert beatport.parse_add_label(
        "Add track 'Don't Stop' (Rido Remix) by A, B to cart. Price: €2,49"
    ) == ("Don't Stop (Rido Remix)", "A, B", cart_models.Decimal("2.49"), "EUR")
    # the default mix is left out, as in SoundCloud titles
    assert beatport.parse_add_label(
        "Add track 'Signal' (Original Mix) by Test Artist to cart. Price: $1.49"
    )[0] == "Signal"
    assert beatport.parse_add_label("Choose a cart for this item") is None


def test_release_row_is_matched_by_its_track_id_not_by_position():
    page = FakePage(Shop(), RELEASE)

    control, label = asyncio.run(beatport.add_control_async(page, "100002"))

    assert "(Rido Remix)" in label and "$1.79" in label
    assert asyncio.run(beatport.add_control_async(page, "999")) is None


def test_release_link_resolves_the_exact_track_from_the_page_code():
    page, item = resolve(Shop(), RELEASE)

    assert (item.product_id, str(item.price), item.currency) == ("100001", "1.49", "USD")
    assert page.url == CART  # the track page was revalidated by id, then the cart was read
    # the rows state their mix, so a remix is told apart from the original
    page = FakePage(Shop(), RELEASE)
    products = asyncio.run(bandcamp_adapter._page_products_async(page, "beatport"))
    chosen = bandcamp_adapter.match_product(track("Signal (Rido Remix)"), products)
    assert (chosen.product_id, str(chosen.price)) == ("100002", "1.79")


def test_track_page_main_button_is_chosen_over_look_alike_recommendations():
    page = FakePage(Shop(), TRACK)

    _control, label = asyncio.run(beatport.add_control_async(page, "100001"))

    assert "Test Artist" in label and "$1.49" in label  # not the $2.99 "Signal" below it
    # a direct link needs no title match: the id in the link is the product
    _page, item = resolve(Shop(), TRACK, "Some Other Title")
    assert (item.product_id, str(item.price)) == ("100001", "1.49")


def test_not_logged_in_is_a_playlist_fallback_that_keeps_an_exact_link():
    page = FakePage(Shop(logged_in=False))
    direct = cart_models.CartRequest(track("Signal"), (("beatport", TRACK),))
    release = cart_models.CartRequest(track("Signal"), (("beatport", RELEASE),))

    with pytest.raises(cart_models.NotLoggedIn):
        asyncio.run(
            bandcamp_adapter._resolve_cart_item_async(page, direct.track, "beatport", TRACK, asyncio.Event())
        )
    session = cart.CartBrowserSession()
    for asked, reason, url in (
        (direct, "ready for Beatport playlist transfer", TRACK),
        (release, "Beatport will match this track by artist and title", RELEASE),
    ):
        result, _page = asyncio.run(
            session._preflight_one(FakePage(Shop(logged_in=False)), asked, asyncio.Event(), cart._StructureFailures())
        )
        assert (result.status, result.reason, result.url) == ("playlist_ready", reason, url)


def test_no_exact_track_in_the_page_code_is_never_searched_for():
    page = FakePage(Shop())
    asked = cart_models.CartRequest(track("Missing Song"), (("beatport", RELEASE),))

    result, _page = asyncio.run(
        cart.CartBrowserSession()._preflight_one(page, asked, asyncio.Event(), cart._StructureFailures())
    )

    assert result.code == "playlist_ready"
    assert page.url == RELEASE  # no search page was ever opened


def test_cart_membership_needs_a_real_cart_row_not_a_recommendation():
    shop = Shop()
    page = FakePage(shop, CART)
    _page, item = resolve(shop, TRACK)
    remix = cart_models.CartItem(**{**item.__dict__, "product_id": "100002"})
    shop.in_cart = True

    assert asyncio.run(beatport.cart_contains_async(page, item, navigate=False))
    # 100002 is only a recommendation on that page
    assert not asyncio.run(beatport.cart_contains_async(page, remix, navigate=False))
    assert resolve(shop, TRACK)[1].already_in_cart


def test_price_change_after_preflight_skips_and_never_clicks():
    shop = Shop()
    page, item = resolve(shop, TRACK)
    shop.price = "$1.99"

    current = asyncio.run(bandcamp_adapter._revalidated(page, item, asyncio.Event(), "changed"))
    assert isinstance(current, cart_models.CartResult) and current.code == "price_changed"

    # and even a page that changes between revalidation and the click is refused
    asyncio.run(page.goto(TRACK))
    with pytest.raises(cart_models.StoreStructureError, match="no longer matches"):
        asyncio.run(beatport.add_to_cart_async(page, item, asyncio.Event()))
    assert shop.clicked == []


def test_a_verified_click_is_added_by_the_counter_or_by_the_cart_page():
    for shop, stage in ((Shop(), "count"), (Shop(update_count=False), "cart")):
        page, item = resolve(shop, TRACK)
        result, missed = asyncio.run(
            cart.CartBrowserSession()._execute_item("beatport", item, page, asyncio.Event())
        )
        assert (result.status, missed, len(shop.clicked)) == ("added", 0, 1)
        # the same state settles at the stage that can see it
        asyncio.run(page.goto(TRACK))
        outcome = asyncio.run(beatport.verify_click_async(page, item, shop.count - 1 if shop.count else 0))
        assert (outcome.verified, outcome.stage) == (True, stage)


def test_an_item_already_in_the_cart_is_not_clicked():
    shop = Shop()
    page, item = resolve(shop, TRACK)
    shop.in_cart = True

    result, _missed = asyncio.run(
        cart.CartBrowserSession()._execute_item("beatport", item, page, asyncio.Event())
    )

    assert result.status == "already_in_cart" and shop.clicked == []


def test_uncertain_verification_is_reported_and_the_click_is_not_repeated():
    shop = Shop(update_count=False, update_cart=False)
    page, item = resolve(shop, TRACK)

    result, missed = asyncio.run(
        cart.CartBrowserSession()._execute_item("beatport", item, page, asyncio.Event())
    )

    assert (result.code, missed) == ("cart_unverified", 1)
    assert "not retried" in result.reason
    assert len(shop.clicked) == 1


def test_billing_pages_are_forbidden_to_the_extension():
    group = json.loads(
        (Path(__file__).parents[1] / "dj_digger/webext/extension/allowlist.json").read_text()
    )["groups"]["beatport"]

    assert {"/checkout", "/account/billing", "/subscriptions/billing"} <= set(group["forbiddenPaths"])


def test_results_log_tells_the_beatport_cart_from_the_playlist():
    def shown(status, store="beatport"):
        return result_status(cart_models.CartResult("1", "x", store, status))

    assert shown("added") == "in Beatport cart"
    assert shown("already_in_cart") == "already in Beatport cart"
    assert shown("playlist_ready") == "to playlist"
    assert shown("failed") == "failed"

import asyncio
import html
import json
import logging
import threading
from dataclasses import replace
from decimal import Decimal

import pytest
from test_webext_page import HOLD, address, private_home  # noqa: F401
from test_webext_page import world as webext_world  # noqa: F401  (fixture, requested by name)

from dj_digger import automation_errors, cart_models, store_match, store_parse, store_urls
from dj_digger.diagnostics import log_safe_text
from dj_digger.models import Track
from dj_digger.services import purchases as cart
from dj_digger.stores import bandcamp as bandcamp_adapter
from dj_digger.webext import page as webpage
from dj_digger.webext.link import ExtensionMissing


@pytest.mark.parametrize(
    "url,store",
    [
        ("https://bandcamp.com/track/a", "bandcamp"),
        ("https://artist.bandcamp.com/album/a", "bandcamp"),
        ("https://beatport.com/track/a/1", "beatport"),
        ("https://www.beatport.com/release/a/2", "beatport"),
    ],
)
def test_only_canonical_https_store_urls_are_safe(url, store):
    assert store_urls.is_store_url(url, store)


@pytest.mark.parametrize(
    "url,store",
    [
        ("http://artist.bandcamp.com/track/a", "bandcamp"),
        ("https://evilbandcamp.com/track/a", "bandcamp"),
        ("https://bandcamp.com.evil.test/track/a", "bandcamp"),
        ("https://user:pass@bandcamp.com/track/a", "bandcamp"),
        ("https://bandcamp.com:444/track/a", "bandcamp"),
        ("javascript:https://bandcamp.com/track/a", "bandcamp"),
        ("https://notbeatport.com/track/a/1", "beatport"),
    ],
)
def test_lookalikes_credentials_and_unsafe_schemes_are_rejected(url, store):
    assert not store_urls.is_store_url(url, store)


def test_plain_http_store_link_is_upgraded_only_after_domain_validation():
    assert (
        store_urls.canonical_store_url(
            "http://artist.bandcamp.com/album/release?from=embed", "bandcamp"
        )
        == "https://artist.bandcamp.com/album/release?from=embed"
    )
    assert store_urls.canonical_store_url("http://bandcamp.com.evil.test/a", "bandcamp") is None
    assert store_urls.canonical_store_url("http://user:pass@bandcamp.com/a", "bandcamp") is None


def test_legacy_beatport_subdomains_are_canonicalized():
    assert (
        store_urls.canonical_store_url(
            "https://pro.beatport.com/track/who-am-i/7008154?from=old", "beatport"
        )
        == "https://www.beatport.com/track/who-am-i/7008154?from=old"
    )


def test_cart_log_text_redacts_queries_and_obvious_secret_fields():
    safe = log_safe_text(
        "failed https://www.beatport.com/login?state=secret oauth_token=also-secret"
    )

    assert safe == "failed https://www.beatport.com/login oauth_token=<redacted>"


def product(title, *, artist="Artist", product_id="1"):
    return cart_models.StoreProduct(
        store="bandcamp",
        url=f"https://artist.bandcamp.com/track/{product_id}",
        product_id=product_id,
        title=title,
        artist=artist,
    )


def track(title, *, artist="Artist"):
    return Track(
        title=title,
        artist=artist,
        permalink_url="https://soundcloud.com/artist/track",
        id=10,
    )


def test_exact_version_survives_artist_prefix_and_promotional_tags():
    chosen = store_match.match_product(
        track("Artist - Signal (VIP) [PREMIERE]"),
        [product("Signal"), product("Signal (VIP)", product_id="2")],
    )

    assert chosen.product_id == "2"


def test_a_different_version_is_never_treated_as_the_same_track():
    with pytest.raises(cart_models.UnsafeMatch, match="version"):
        store_match.match_product(track("Signal (VIP)"), [product("Signal (Original Mix)")])


def test_duplicate_exact_titles_need_an_artist_tie_breaker():
    chosen = store_match.match_product(
        track("Signal", artist="Right Artist"),
        [
            product("Signal", artist="Other Artist", product_id="1"),
            product("Signal", artist="Right Artist", product_id="2"),
        ],
    )

    assert chosen.product_id == "2"


def test_an_unresolved_duplicate_is_skipped_instead_of_guessed():
    with pytest.raises(cart_models.UnsafeMatch, match="ambiguous"):
        store_match.match_product(
            track("Signal", artist="Uploader"),
            [product("Signal", artist="One"), product("Signal", artist="Two", product_id="2")],
        )


def test_a_common_artist_word_cannot_break_a_title_tie():
    with pytest.raises(cart_models.UnsafeMatch, match="ambiguous"):
        store_match.match_product(
            track("Signal", artist="The Right Artist"),
            [
                product("Signal", artist="The Wrong Artist", product_id="1"),
                product("Signal", artist="Someone Else", product_id="2"),
            ],
        )


def test_no_exact_title_is_a_business_unavailability():
    with pytest.raises(cart_models.ProductUnavailable, match="exact"):
        store_match.match_product(track("Signal"), [product("Another Track")])


def test_a_unique_trailing_title_survives_different_artist_aliases():
    chosen = store_match.match_product(
        track("Phil:osophy - Remember", artist="UKF"),
        [product("Philth Tangent - Remember", artist="Philth Tangent")],
    )

    assert chosen.title == "Philth Tangent - Remember"


def test_double_slash_promo_title_can_match_one_exact_segment():
    chosen = store_match.match_product(
        track("Impak // Fractals // C4CDIGUK045", artist="Cause4Concern"),
        [product("No Time"), product("Fractals", product_id="2")],
    )

    assert chosen.product_id == "2"


def test_quoted_premiere_title_ignores_uploader_and_label_context():
    chosen = store_match.match_product(
        track("PREMIERE: Rohaan 'I Found You' [Mad Zoo]", artist="dtdnb"),
        [product("City of Ezra"), product("I Found You", product_id="2")],
    )

    assert chosen.product_id == "2"


def test_catalogue_context_does_not_hide_an_exact_trailing_title():
    chosen = store_match.match_product(
        track("Aristide - Check It Out [TX006]", artist="Tx Records"),
        [product("Check It Out")],
    )

    assert chosen.product_id == "1"


def test_feat_and_ft_are_equivalent_in_an_exact_trailing_title():
    chosen = store_match.match_product(
        track("Philth - Ravanelli (ft. Sense MC)", artist="Drum&BassArena"),
        [product("Ravanelli (feat. Sense MC)")],
    )

    assert chosen.product_id == "1"


def test_radio_cut_is_not_mistaken_for_the_full_store_track():
    with pytest.raises(cart_models.UnsafeMatch, match="version"):
        store_match.match_product(
            track("Arkaik & Creatures - Stroboscope (NOISIA RADIO S6E29 Cut)"),
            [product("Stroboscope")],
        )


def test_duplicate_trailing_titles_are_still_ambiguous():
    with pytest.raises(cart_models.UnsafeMatch, match="trailing"):
        store_match.match_product(
            track("Alias - Remember"),
            [
                product("First Artist - Remember", product_id="1"),
                product("Second Artist - Remember", product_id="2"),
            ],
        )


def test_trailing_title_never_hides_a_version_mismatch():
    with pytest.raises(cart_models.UnsafeMatch, match="version"):
        store_match.match_product(
            track("Alias - Signal (VIP)"),
            [product("Other Alias - Signal (Original Mix)")],
        )


def bandcamp_html(*, structured_price="1.10", tralbum_price=1.1):
    tralbum = {
        "current": {"artist": "Right Artist", "minimum_price": tralbum_price},
        "trackinfo": [
            {"track_id": 12, "title": "Signal", "title_link": "/track/signal"},
            {"track_id": 13, "title": "Signal (VIP)", "title_link": "/track/signal-vip"},
        ],
    }
    structured = {
        "@type": "MusicRecording",
        "@id": "https://right.bandcamp.com/track/signal",
        "name": "Signal",
        "byArtist": {"name": "Right Artist"},
        "additionalProperty": [{"name": "track_id", "value": 12}],
        "offers": {
            "@type": "Offer",
            "priceCurrency": "GBP",
            "price": structured_price,
            "priceSpecification": {"minPrice": structured_price},
        },
    }
    return (
        '<div data-tralbum="'
        + html.escape(json.dumps(tralbum), quote=True)
        + '"></div><script type="application/ld+json">'
        + json.dumps(structured)
        + "</script>"
    )


def test_bandcamp_release_tracks_are_resolved_to_canonical_track_products():
    products = store_parse.products_from_html(
        bandcamp_html(), "https://right.bandcamp.com/album/release", "bandcamp"
    )

    chosen = store_match.match_product(track("Signal (VIP)", artist="Right Artist"), products)
    assert chosen.product_id == "13"
    assert chosen.url == "https://right.bandcamp.com/track/signal-vip"


def test_bandcamp_price_is_decimal_and_cross_checked_on_the_track_page():
    products = store_parse.products_from_html(
        bandcamp_html(), "https://right.bandcamp.com/track/signal", "bandcamp"
    )

    chosen = store_match.match_product(track("Signal", artist="Right Artist"), products)
    assert chosen.price == Decimal("1.10")
    assert chosen.currency == "GBP"


def test_bandcamp_conflicting_visible_and_structured_prices_are_refused():
    with pytest.raises(automation_errors.AutomationError, match="price"):
        store_parse.products_from_html(
            bandcamp_html(structured_price="1.20"),
            "https://right.bandcamp.com/track/signal",
            "bandcamp",
        )


def test_beatport_json_ld_keeps_the_numeric_track_id_and_decimal_price():
    structured = {
        "@type": "MusicRecording",
        "url": "https://www.beatport.com/track/signal/987",
        "name": "Signal",
        "byArtist": {"name": "Right Artist"},
        "offers": {"price": "2.49", "priceCurrency": "EUR"},
    }

    products = store_parse.products_from_html(
        f'<script type="application/ld+json">{json.dumps(structured)}</script>',
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    assert products == [
        cart_models.StoreProduct(
            store="beatport",
            url="https://www.beatport.com/track/signal/987",
            product_id="987",
            title="Signal",
            artist="Right Artist",
            price=Decimal("2.49"),
            currency="EUR",
        )
    ]


def test_conflicting_structured_offers_are_not_reduced_to_the_first_price():
    structured = {
        "@type": "MusicRecording",
        "url": "https://www.beatport.com/track/signal/987",
        "name": "Signal",
        "offers": [
            {"price": "1.69", "priceCurrency": "USD"},
            {"price": "2.49", "priceCurrency": "USD"},
        ],
    }

    products = store_parse.products_from_html(
        f'<script type="application/ld+json">{json.dumps(structured)}</script>',
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    assert products[0].price is None
    assert products[0].currency == ""


def test_beatport_release_can_resolve_semantic_track_links_before_visiting_the_track():
    products = store_parse.products_from_html(
        '<a href="/track/signal/987" aria-label="Signal">Signal</a>',
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    assert products == [
        cart_models.StoreProduct(
            store="beatport",
            url="https://www.beatport.com/track/signal/987",
            product_id="987",
            title="Signal",
        )
    ]


def test_beatport_release_uses_the_track_slug_to_keep_the_exact_remix():
    products = store_parse.products_from_html(
        """
        <a href="/track/signal-original-mix/986" aria-label="Signal">Signal</a>
        <a href="/track/signal-rido-remix/987" aria-label="Signal">Signal</a>
        """,
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    chosen = store_match.match_product(track("Signal (Rido Remix)"), products)

    assert chosen.product_id == "987"


def test_store_security_challenge_is_a_technical_failure_not_missing_product():
    html = """
        <html><head><title>Just a moment...</title></head>
        <body>Performing security verification. Ray ID: public-challenge-id</body></html>
    """

    with pytest.raises(cart_models.SecurityChallengeBlocked, match="did not clear on its own"):
        store_parse.products_from_html(
            html,
            "https://www.beatport.com/release/release/123",
            "beatport",
        )


def test_name_your_price_uses_a_positive_default_then_an_explicit_step():
    assert store_parse.purchase_price(Decimal("0"), Decimal("1.00"), Decimal("0.01")) == Decimal("1.00")
    assert store_parse.purchase_price(Decimal("0"), None, Decimal("0.01")) == Decimal("0.01")


def test_name_your_price_takes_the_minimum_unless_the_suggested_price_is_chosen():
    assert store_parse.purchase_price(Decimal("1"), Decimal("3"), None) == Decimal("1")
    assert store_parse.purchase_price(Decimal("1"), Decimal("3"), None, suggested=True) == Decimal("3")
    assert store_parse.purchase_price(Decimal("2"), Decimal("1"), None, suggested=True) == Decimal("2")


def test_name_your_price_without_a_positive_value_is_not_guessed():
    with pytest.raises(automation_errors.AutomationError, match="positive"):
        store_parse.purchase_price(Decimal("0"), None, None)


def request(*stores):
    return cart_models.CartRequest(
        track=track("Signal"),
        links=tuple(
            (store, f"https://{'artist.bandcamp.com' if store == 'bandcamp' else 'www.beatport.com'}/track/signal/1")
            for store in stores
        ),
    )


def resolved_item(store, *, already=False, price="1.25", currency="GBP"):
    return cart_models.CartItem(
        track_key="10",
        track_label="Artist - Signal",
        store=store,
        source_url=f"https://{'artist.bandcamp.com' if store == 'bandcamp' else 'www.beatport.com'}/track/signal/1",
        product_url=f"https://{'artist.bandcamp.com' if store == 'bandcamp' else 'www.beatport.com'}/track/signal/1",
        product_id="1",
        product_title="Signal",
        price=Decimal(price),
        currency=currency,
        already_in_cart=already,
    )


def test_preflight_summary_separates_currencies_and_excludes_existing_items():
    plan = cart_models.CartPlan(
        items=(
            resolved_item("bandcamp", price="1.25", currency="GBP"),
            resolved_item("beatport", price="2.49", currency="EUR"),
            resolved_item("bandcamp", already=True, price="9.00", currency="GBP"),
        ),
        results=(cart_models.CartResult("20", "Missing", "bandcamp", "skipped", "no exact track"),),
    )

    summary = plan.summary()

    assert "GBP 1.25" in summary
    assert "EUR 2.49" in summary
    assert "GBP 10.25" not in summary
    assert "already in cart" in summary
    assert "Missing" in summary


def test_preflight_summary_flattens_untrusted_track_labels():
    item = replace(resolved_item("bandcamp"), track_label="Track\nGBP 999 [bold]")

    summary = cart_models.CartPlan(items=(item,)).summary()

    assert "Track GBP 999 [bold] — bandcamp" in summary
    assert "Track\nGBP 999" not in summary


def test_redacted_urls_drop_credentials_queries_and_fragments():
    from dj_digger.links import redact_url

    assert (
        redact_url("https://user:pass@bandcamp.com/track/a?token=secret#frag")
        == "https://bandcamp.com/track/a"
    )


class RedirectPage:
    def __init__(self, target):
        self.url = "about:blank"
        self.target = target
        self.calls = []

    def goto(self, url, **_kwargs):
        self.calls.append(url)
        self.url = self.target


class EmptyLocator:
    def count(self):
        return 0


class NoControlsPage:
    def get_by_role(self, *_args, **_kwargs):
        return EmptyLocator()

    def get_by_text(self, *_args, **_kwargs):
        return EmptyLocator()


class BodyTextLocator:
    def __init__(self, text):
        self.text = text

    def inner_text(self, **_kwargs):
        return self.text


class BodyTextPage:
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        assert selector == "body"
        return BodyTextLocator(self.text)


class VisibilityNode:
    def __init__(self, index, visible):
        self.index = index
        self.visible = visible

    async def is_visible(self):
        return self.visible


class MultiLocator:
    def __init__(self, visibility):
        self.visibility = visibility

    async def count(self):
        return len(self.visibility)

    def nth(self, index):
        return VisibilityNode(index, self.visibility[index])


class RolePage:
    def __init__(self, url, visible_names):
        self.url = url
        self.visible_names = visible_names

    def get_by_role(self, _role, *, name):
        matches = [label for label in self.visible_names if name.fullmatch(label)]
        return MultiLocator([True] * len(matches))


class PriceButtonsPage(RolePage):
    def get_by_text(self, *_args, **_kwargs):
        return EmptyLocator()


class ProductRegion:
    def get_by_role(self, _role, *, name):
        assert name.fullmatch("$1.69")
        return MultiLocator([True])


class ProductHeading(VisibilityNode):
    def locator(self, selector):
        assert selector == "xpath=ancestor::*[.//button][1]"
        return ProductRegion()


class HeadingLocator:
    def count(self):
        return 1

    def nth(self, index):
        assert index == 0
        return ProductHeading(index, True)


class ScopedPriceButtonsPage:
    def get_by_role(self, role, **_kwargs):
        return HeadingLocator() if role == "heading" else MultiLocator([True, True])

    def get_by_text(self, *_args, **_kwargs):
        return EmptyLocator()


class RemoveRegion:
    def __init__(self, has_remove):
        self.has_remove = has_remove

    def get_by_role(self, _role, *, name):
        assert name.search("Remove")
        return MultiLocator([self.has_remove])


class CartAnchor(VisibilityNode):
    def __init__(self, index, has_remove):
        super().__init__(index, True)
        self.has_remove = has_remove

    def locator(self, selector):
        assert selector == "xpath=ancestor::*[.//button][1]"
        return RemoveRegion(self.has_remove)


class CartAnchors:
    def count(self):
        return 2

    def nth(self, index):
        return CartAnchor(index, has_remove=index == 1)


class BeatportCartPage:
    def locator(self, selector):
        assert '/1"' in selector
        return CartAnchors()


class BandcampLoginRedirectPage(RolePage):
    def __init__(self):
        super().__init__("about:blank", set())
        self.calls = []

    def goto(self, url, **_kwargs):
        self.calls.append(url)
        self.url = "https://bandcamp.com/" if url.endswith("/login") else url


class DuplicateLoginPage(RolePage):
    def __init__(self):
        super().__init__("https://bandcamp.com/", set())

    def get_by_role(self, role, *, name):
        if role == "link" and name.fullmatch("Log in"):
            return MultiLocator([True, True])
        return EmptyLocator()


def test_locator_accepts_one_visible_control_among_responsive_duplicates():
    chosen = asyncio.run(bandcamp_adapter._first_visible_async(MultiLocator([False, True])))

    assert chosen.index == 1


class CartPage(RedirectPage):
    def __init__(self):
        super().__init__("")
        self.selectors = []

    def goto(self, url, **_kwargs):
        self.calls.append(url)
        self.url = url

    def locator(self, selector):
        self.selectors.append(selector)
        return EmptyLocator()


class BandcampCartAnchor(CartAnchor):
    def get_attribute(self, name):
        assert name == "href"
        return "/track/signal/1"

    def locator(self, selector):
        assert selector == "xpath=ancestor::*[.//a][1]"
        return RemoveRegion(self.has_remove)


class BandcampCartAnchors:
    def count(self):
        return 1

    def nth(self, index):
        assert index == 0
        return BandcampCartAnchor(index, has_remove=True)


class BandcampCartPage:
    def locator(self, selector):
        if selector.endswith("a[href]"):
            return BandcampCartAnchors()
        return EmptyLocator()


class BatchTab:
    def __init__(self, name):
        self.name = name
        self.url = "about:blank"


class BatchContext:
    def __init__(self):
        self.pages = [BatchTab("first")]
        self.closed = False

    def new_page(self):
        page = BatchTab(f"tab-{len(self.pages) + 1}")
        self.pages.append(page)
        return page


def two_store_plan():
    first = resolved_item("bandcamp")
    second = replace(
        resolved_item("beatport", price="2.49", currency="EUR"),
        track_key="20",
        track_label="Artist - Second",
        product_id="2",
        source_url="https://www.beatport.com/track/second/2",
        product_url="https://www.beatport.com/track/second/2",
        product_title="Second",
    )
    return cart_models.CartPlan(items=(first, second))


def async_requests(count=6):
    return tuple(
        cart_models.CartRequest(
            replace(track(f"Track {index}"), id=index),
            (("bandcamp", f"https://label.bandcamp.com/track/{index}"),),
        )
        for index in range(1, count + 1)
    )


def test_async_preflight_has_two_real_workers_not_one_task_per_track(monkeypatch):
    active = 0
    peak = 0

    async def resolve(_page, candidate, store, url, _cancel):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return replace(
            resolved_item(store),
            track_key=candidate.key,
            track_label=candidate.label,
            source_url=url,
            product_url=url,
        )

    monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", resolve)
    session = cart.CartBrowserSession()

    plan = asyncio.run(
        session._preflight(async_requests(8), [object(), object()], asyncio.Event(), None)
    )

    assert len(plan.items) == 8
    assert peak == 2


def test_two_distinct_structural_failures_stop_pending_navigation(monkeypatch):
    calls = []

    async def broken(_page, candidate, store, _url, _cancel):
        calls.append((candidate.key, store))
        raise cart_models.StoreStructureError("same missing product marker")

    async def replace_page(_page):
        return object()

    monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", broken)
    session = cart.CartBrowserSession()
    monkeypatch.setattr(session, "_replace_page", replace_page)

    plan = asyncio.run(
        session._preflight(async_requests(10), [object(), object()], asyncio.Event(), None)
    )

    assert not plan.items
    assert len(plan.results) == 10
    # Two workers each make one fresh-page retry. The remaining queue is drained
    # as failed without loading another product.
    assert len(calls) == 4
    assert all(result.code == "store_structure" for result in plan.results)


def test_preflight_failure_is_logged_without_url_query_credentials(monkeypatch, caplog):
    async def blocked(_page, _candidate, _store, _url, _cancel):
        raise cart_models.SecurityChallengeBlocked("automated browser rejected")

    candidate = cart_models.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/track/signal/1?token=secret"),),
    )
    monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", blocked)
    session = cart.CartBrowserSession()

    with caplog.at_level(logging.INFO, logger="dj_digger"):
        plan = asyncio.run(
            session._preflight((candidate,), [object(), object()], asyncio.Event(), None)
        )

    assert plan.results[0].code == "playlist_ready"
    assert plan.results[0].status == "playlist_ready"
    assert "Cart preflight result" in caplog.text
    assert "https://www.beatport.com/track/signal/1" in caplog.text
    assert "token=secret" not in caplog.text


def test_bandcamp_preflight_does_not_require_an_account_login(monkeypatch):
    async def resolve(_page, candidate, store, url, _cancel):
        return replace(
            resolved_item(store),
            track_key=candidate.key,
            track_label=candidate.label,
            source_url=url,
            product_url=url,
        )

    monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", resolve)
    session = cart.CartBrowserSession()

    plan = asyncio.run(
        session._preflight(async_requests(1), [object(), object()], asyncio.Event(), None)
    )

    assert len(plan.items) == 1


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"
        self.closed = False
        self.focused = 0
        self.released = False

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True

    async def bring_to_front(self):
        self.focused += 1

    async def release(self):
        self.released = True


class FakeContext:
    """What AsyncExtensionContext offers the cart session: one window, shown or hidden."""

    def __init__(self):
        self.visible = False
        self.closed = False
        self.pages = []
        self.shows = 0
        self.hides = 0
        self.listeners = {}

    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)

    def is_closed(self):
        return self.closed

    async def new_page(self):
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def show(self):
        self.visible = True
        self.shows += 1

    async def hide(self):
        self.visible = False
        self.hides += 1

    async def close(self, reason=None):
        self.closed = True
        for callback in self.listeners.get("close", ()):
            callback(self)


@pytest.fixture
def opened_contexts(monkeypatch):
    """Every window the session opens, in order; nothing reaches a browser."""

    contexts = []

    async def open_context(visible=False, listener=None, *, background=False):
        context = FakeContext()
        context.visible = visible
        context.background = background
        contexts.append(context)
        return context

    monkeypatch.setattr(cart.webpage.AsyncExtensionContext, "open", open_context)
    return contexts


def test_cart_session_opens_one_hidden_window_and_reuses_it(opened_contexts):
    session = cart.CartBrowserSession()

    async def scenario():
        await session._ensure_context()
        await session._ensure_context()
        pages = await session._work_pages(2)
        again = await session._work_pages(2)
        assert pages == again
        await session.close()

    asyncio.run(scenario())

    assert len(opened_contexts) == 1, "one session, reused, never reopened"
    assert opened_contexts[0].background, "tabs of the person's own window, no window of its own"
    assert not opened_contexts[0].shows
    assert opened_contexts[0].closed


def test_every_batch_closes_its_work_tabs_however_it_ends(opened_contexts, monkeypatch):
    """The work tabs (lookups, a skipped item's page) never outlive their batch; released carts are not theirs."""

    session = cart.CartBrowserSession()

    async def nothing_ready(*_args, **_kwargs):
        raise cart_models.ProductUnavailable("linked release has no exact track")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("the page fell over")

    async def scenario():
        monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", nothing_ready)
        outcome = await session.run_batch(async_requests(2), asyncio.Event(), approve=None)  # ends at preflight
        assert not outcome.cart_stores
        monkeypatch.setattr(session, "_preflight", broken)
        with pytest.raises(RuntimeError):
            await session.run_batch(async_requests(1), asyncio.Event(), approve=None)
        async def view_fails(_store, _items, _verified):
            await (await session._shown_context()).new_page()  # the cart tab, never handed over
            raise RuntimeError("the cart did not load")

        monkeypatch.setattr(session, "_show_store_cart", view_fails)
        session._cart_stores = ("bandcamp",)
        with pytest.raises(RuntimeError):
            await session.focus_carts()

    asyncio.run(scenario())

    assert len(opened_contexts) == 3, "each batch has a session of its own"
    assert all(context.closed for context in opened_contexts), "no work tab survives a batch"
    assert session._context is None and session._owned_pages == []


def test_a_lost_window_is_replaced_and_its_pages_forgotten(opened_contexts):
    session = cart.CartBrowserSession()

    async def scenario():
        await session._work_pages(2)
        await opened_contexts[0].close()  # the browser went away
        assert session._owned_pages == []
        await session._work_pages(2)

    asyncio.run(scenario())

    assert len(opened_contexts) == 2


def test_beatport_preflight_reads_public_metadata_before_requesting_login(monkeypatch):
    async def resolve(_page, candidate, store, url, _cancel):
        return replace(
            resolved_item(store, price="2.49", currency="EUR"),
            track_key=candidate.key,
            track_label=candidate.label,
            source_url=url,
            product_url=url,
        )

    candidate = cart_models.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/track/signal/1"),),
    )
    monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", resolve)
    session = cart.CartBrowserSession()

    plan = asyncio.run(
        session._preflight((candidate,), [object(), object()], asyncio.Event(), None)
    )

    assert plan.items[0].store == "beatport"


def test_beatport_lookup_failure_still_prepares_a_metadata_playlist(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise cart_models.ProductUnavailable("linked release has no exact track")

    candidate = cart_models.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/release/signal/1"),),
    )
    monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", unavailable)
    session = cart.CartBrowserSession()

    plan = asyncio.run(
        session._preflight((candidate,), [object(), object()], asyncio.Event(), None)
    )

    assert not plan.items
    assert plan.results == (
        cart_models.CartResult(
            candidate.track.key,
            candidate.track.label,
            "beatport",
            "playlist_ready",
            "Beatport will match this track by artist and title",
            "playlist_ready",
            candidate.links[0][1],
        ),
    )


def test_direct_beatport_track_without_a_login_stays_an_exact_playlist_entry(monkeypatch):
    candidate = cart_models.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/track/signal/987?token=secret"),),
    )
    session = cart.CartBrowserSession()

    async def pages(_count=2):
        return [object(), object()]

    async def logged_out(*_args, **_kwargs):
        raise cart_models.NotLoggedIn("not logged in to Beatport")

    async def no_approval(_plan):
        pytest.fail("a playlist entry needs no cart approval")

    monkeypatch.setattr(session, "_work_pages", pages)
    monkeypatch.setattr(bandcamp_adapter, "_resolve_cart_item_async", logged_out)

    outcome = asyncio.run(
        session.run_batch((candidate,), asyncio.Event(), approve=no_approval)
    )

    assert outcome.results == (
        cart_models.CartResult(
            candidate.track.key,
            candidate.track.label,
            "beatport",
            "playlist_ready",
            "ready for Beatport playlist transfer",
            "playlist_ready",
            "https://www.beatport.com/track/signal/987",
        ),
    )


def test_a_known_security_challenge_stops_without_looping_or_structure_retry(monkeypatch):
    monkeypatch.setattr(bandcamp_adapter, "CHALLENGE_GRACE_SECONDS", 0)
    class ChallengePage:
        url = "https://www.beatport.com/track/signal/1"
        focused = False

        async def content(self):
            return "<title>Just a moment...</title>Performing security verification"

        async def bring_to_front(self):
            self.focused = True

    page = ChallengePage()
    with pytest.raises(cart_models.SecurityChallengeBlocked, match="did not clear on its own"):
        asyncio.run(bandcamp_adapter._page_products_async(page, "beatport", asyncio.Event()))

    assert page.focused


def test_a_security_check_that_clears_itself_is_waited_out(monkeypatch):
    # Seen live on Beatport: "Just a moment..." passes in the user's browser after a few seconds.
    monkeypatch.setattr(bandcamp_adapter.asyncio, "sleep", _no_sleep)
    real = store_parse.products_from_html
    calls = []

    def products(content, url, store):
        calls.append(content)
        return real(content, url, store) if len(calls) < 3 else ["product"]

    monkeypatch.setattr(bandcamp_adapter, "products_from_html", products)

    class ClearingPage:
        url = "https://www.beatport.com/track/signal/1"
        focused = False

        async def content(self):
            return "<title>Just a moment...</title>Performing security verification"

        async def bring_to_front(self):
            self.focused = True

    async def labels(page, found):
        return found

    from dj_digger.stores import beatport
    monkeypatch.setattr(beatport, "with_labels_async", labels)
    page = ClearingPage()
    assert asyncio.run(bandcamp_adapter._page_products_async(page, "beatport", asyncio.Event())) == ["product"]
    assert len(calls) == 3 and not page.focused


async def _no_sleep(_seconds):
    return None


def test_bandcamp_storefront_without_a_release_is_not_a_structure_failure(monkeypatch):
    class StorefrontPage:
        url = "https://label.bandcamp.com/"

        async def content(self):
            return "<html><title>Label</title><body>music</body></html>"

    async def no_dom_products(_page):
        return []

    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_dom_products", no_dom_products)

    with pytest.raises(cart_models.ProductUnavailable, match="storefront"):
        asyncio.run(
            bandcamp_adapter._page_products_async(StorefrontPage(), "bandcamp", asyncio.Event())
        )


def test_bandcamp_dom_price_merges_by_product_path_not_download_query(monkeypatch):
    class ProductPage:
        url = "https://label.bandcamp.com/track/signal"

        async def content(self):
            return "<html><title>Signal</title></html>"

    parsed = cart_models.StoreProduct(
        "bandcamp",
        "https://label.bandcamp.com/track/signal?action=download",
        "999",
        "full digital discography",
    )
    current = cart_models.StoreProduct(
        "bandcamp",
        "https://label.bandcamp.com/track/signal",
        "123",
        "Signal",
        price=Decimal("1.99"),
        currency="GBP",
    )

    monkeypatch.setattr(bandcamp_adapter, "products_from_html", lambda *_args: [parsed])

    async def dom_products(_page):
        return [current]

    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_dom_products", dom_products)

    products = asyncio.run(
        bandcamp_adapter._page_products_async(ProductPage(), "bandcamp", asyncio.Event())
    )

    assert products == [current]


def test_bandcamp_dead_source_can_resolve_an_exact_autocomplete_track(monkeypatch):
    candidate = track("Revan & Ollie Norton - Lights On", artist="Flexout Audio")
    found = cart_models.StoreProduct(
        "bandcamp",
        "https://flexoutaudio.bandcamp.com/track/lights-on",
        "",
        "Lights On",
        "Revan & Ollie Norton",
    )

    async def navigate(_page, _url, _store):
        return 404

    async def search(_page, _track, _cancel):
        return [found], []

    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_search_candidates_async", search)

    chosen = asyncio.run(
        bandcamp_adapter._resolve_bandcamp_product_async(
            object(),
            candidate,
            "https://revanbristol.bandcamp.com/album/lights-on",
            asyncio.Event(),
        )
    )

    assert chosen == found


def test_bandcamp_storefront_search_moves_to_the_global_homepage(monkeypatch):
    class Search:
        async def fill(self, _query):
            pass

    class Anchor:
        async def get_attribute(self, _name):
            return "https://label.bandcamp.com/album/signal?from=search"

        async def inner_text(self):
            return "Signal\nby Artist"

    class Anchors:
        async def count(self):
            return 1

        async def all_texts_and_attrs(self, attrs, text=True, limit=500):
            assert attrs == ["href"]
            return [{"attrs": {"href": await Anchor().get_attribute("href")},
                     "text": await Anchor().inner_text()}]

    class Page:
        url = "https://label.bandcamp.com/"

        def get_by_placeholder(self, _name):
            return object()

        def locator(self, _selector):
            return Anchors()

    matches = iter((None, Search()))
    visited = []

    async def visible(_locator):
        return next(matches)

    async def navigate(page, url, _store):
        visited.append(url)
        page.url = url
        return 200

    monkeypatch.setattr(bandcamp_adapter, "_first_visible_match_async", visible)
    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)

    _tracks, albums = asyncio.run(
        bandcamp_adapter._bandcamp_search_candidates_async(
            Page(), track("Artist - Signal"), asyncio.Event()
        )
    )

    assert visited == [store_urls.STORE_HOME["bandcamp"]]
    assert albums == ["https://label.bandcamp.com/album/signal"]


def test_bandcamp_autocomplete_album_is_bounded_and_inspected_for_exact_track(
    monkeypatch,
):
    candidate = track("Artist - Signal", artist="Uploader")
    found = product("Signal", artist="Artist")
    visited = []

    async def navigate(_page, url, _store):
        visited.append(url)
        return 404 if len(visited) == 1 else 200

    async def search(_page, _track, _cancel):
        return [], [
            "https://label.bandcamp.com/album/one",
            "https://label.bandcamp.com/album/two",
            "https://label.bandcamp.com/album/three",
            "https://label.bandcamp.com/album/not-visited",
        ]

    async def products(_page, _store, _cancel):
        return [found]

    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_search_candidates_async", search)
    monkeypatch.setattr(bandcamp_adapter, "_page_products_async", products)

    chosen = asyncio.run(
        bandcamp_adapter._resolve_bandcamp_product_async(
            object(),
            candidate,
            "https://label.bandcamp.com/album/missing",
            asyncio.Event(),
        )
    )

    assert chosen == found
    assert visited == [
        "https://label.bandcamp.com/album/missing",
        "https://label.bandcamp.com/album/one",
    ]


def test_beatport_release_resolution_keeps_the_exact_track_and_checks_the_cart(
    monkeypatch,
):
    candidate = track("Signal (Rido Remix)")
    product = cart_models.StoreProduct(
        "beatport",
        "https://www.beatport.com/track/signal-rido-remix/987",
        "987",
        "Signal (Rido Remix)",
        "Artist",
        Decimal("2.49"),
        "EUR",
    )

    class Page:
        url = "about:blank"

    page = Page()

    async def navigate(_page, url, _store):
        _page.url = url
        return 200

    async def products(*_args):
        return [product]

    async def quote(*_args):
        return cart_models.PriceQuote("EUR", Decimal("2.49"), Decimal("2.49"))

    async def in_cart(*_args, **_kwargs):
        return True

    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    monkeypatch.setattr(bandcamp_adapter, "_page_products_async", products)
    monkeypatch.setattr(bandcamp_adapter, "_quote_async", quote)
    monkeypatch.setattr(bandcamp_adapter, "_cart_contains_async", in_cart)

    item = asyncio.run(
        bandcamp_adapter._resolve_cart_item_async(
            page,
            candidate,
            "beatport",
            "https://www.beatport.com/release/signal/123",
            asyncio.Event(),
        )
    )

    assert item.product_url == product.url
    assert item.already_in_cart


def test_beatport_release_rejects_a_different_mix_on_the_target_page(monkeypatch):
    candidate = track("Signal (Rido Remix)")
    release_product = cart_models.StoreProduct(
        "beatport",
        "https://www.beatport.com/track/signal-rido-remix/987",
        "987",
        "Signal",
    )
    changed_product = replace(
        release_product,
        title="Signal (Original Mix)",
        price=Decimal("2.49"),
        currency="EUR",
    )

    class Page:
        url = "about:blank"

    page = Page()
    snapshots = iter(([release_product], [changed_product]))

    async def navigate(_page, url, _store):
        _page.url = url
        return 200

    async def products(*_args):
        return next(snapshots)

    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    monkeypatch.setattr(bandcamp_adapter, "_page_products_async", products)

    with pytest.raises(cart_models.UnsafeMatch, match="title or version"):
        asyncio.run(
            bandcamp_adapter._resolve_cart_item_async(
                page,
                candidate,
                "beatport",
                "https://www.beatport.com/release/signal/123",
                asyncio.Event(),
            )
        )


def test_bandcamp_click_is_verified_by_cart_count_without_reload(monkeypatch):
    counts = iter((2, 3))

    async def count(_page):
        return next(counts)

    async def no_reload(*_args, **_kwargs):
        pytest.fail("a changed cart count must not trigger reload verification")

    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", no_reload)

    assert asyncio.run(
        bandcamp_adapter._verify_bandcamp_click_async(object(), resolved_item("bandcamp"), 2)
    )


@pytest.mark.parametrize(
    "visible,removable,expected",
    [(False, True, False), (True, False, False), (True, True, True)],
)
def test_async_bandcamp_membership_requires_a_visible_removable_row(
    visible, removable, expected, monkeypatch
):
    """The side cart row: a product anchor plus the "x" delete anchor, both visible."""

    class Element:
        def __init__(self, shown=True, href=None):
            self.shown, self.href = shown, href

        async def is_visible(self):
            return self.shown

        async def get_attribute(self, _name):
            return self.href

        async def count(self):
            return 1

        def nth(self, _index):
            return self

        @property
        def first(self):
            return self

    class Nodes:
        def __init__(self, nodes):
            self.nodes = nodes

        async def count(self):
            return len(self.nodes)

        def nth(self, index):
            return self.nodes[index]

        @property
        def first(self):
            return self.nodes[0]

    class Row(Element):
        def locator(self, selector):
            if selector == "a[href]":
                return Nodes([Element(href="https://artist.bandcamp.com/track/signal/1")])
            if selector == bandcamp_adapter.SIDECART_REMOVE:
                return Nodes([Element(shown=True)]) if removable else Nodes([])
            return Nodes([])

        def get_by_role(self, _role, *, name):
            return Nodes([])

    class Page:
        url = "https://artist.bandcamp.com/track/signal/1"

        def locator(self, selector):
            if selector == bandcamp_adapter.SIDECART_ROWS:
                return Nodes([Row(shown=visible)])
            return Nodes([])

    async def cart_closed(_page):
        return False

    monkeypatch.setattr(bandcamp_adapter, "_open_bandcamp_cart_async", cart_closed)

    assert (
        asyncio.run(bandcamp_adapter._bandcamp_cart_contains_async(Page(), resolved_item("bandcamp")))
        is expected
    )


def test_bandcamp_quote_defaults_to_minimum_and_only_marks_a_real_field_editable(
    monkeypatch,
):
    class Region:
        async def inner_text(self):
            return "Buy Digital Track £1 GBP"

    class Control:
        clicks = 0

        def locator(self, _selector):
            return Region()

        async def click(self, **_kwargs):
            self.clicks += 1

    class Page:
        def get_by_role(self, *_args, **_kwargs):
            return object()

        def get_by_text(self, *_args, **_kwargs):
            return object()

        def locator(self, _selector):
            return object()

        async def evaluate(self, _script):
            return Decimal("1.50")

    control = Control()

    async def first(*_locators):
        return control

    async def no_input(_locator):
        return None

    monkeypatch.setattr(bandcamp_adapter, "_first_visible_async", first)
    monkeypatch.setattr(bandcamp_adapter, "_only_visible_async", no_input)
    candidate = replace(product("Signal"), price=Decimal("1.00"), currency="GBP")

    quote = asyncio.run(bandcamp_adapter._bandcamp_quote_async(Page(), candidate))

    assert quote.selected == Decimal("1.00")
    assert not quote.editable
    assert control.clicks == 1


def test_bandcamp_name_your_price_uses_the_positive_field_default(monkeypatch):
    class Region:
        async def inner_text(self):
            return "Buy Digital Track £0 or more GBP"

    class Control:
        def locator(self, _selector):
            return Region()

    class PriceInput:
        async def get_attribute(self, name):
            return {"min": "0", "step": "0.50"}[name]

        async def input_value(self):
            return "2.00"

    class Page:
        def get_by_role(self, *_args, **_kwargs):
            return object()

        def get_by_text(self, *_args, **_kwargs):
            return object()

        def locator(self, _selector):
            return object()

        async def evaluate(self, _script):
            return None

    async def first(*_locators):
        return Control()

    async def price_input(_locator):
        return PriceInput()

    monkeypatch.setattr(bandcamp_adapter, "_first_visible_async", first)
    monkeypatch.setattr(bandcamp_adapter, "_only_visible_async", price_input)
    candidate = replace(product("Signal"), price=Decimal("0"), currency="GBP")

    quote = asyncio.run(bandcamp_adapter._bandcamp_quote_async(Page(), candidate))

    assert quote.minimum == Decimal("0")
    assert quote.selected == Decimal("2.00")
    assert quote.editable


def test_raised_bandcamp_price_is_never_ignored_when_the_field_disappears(
    monkeypatch,
):
    item = replace(
        resolved_item("bandcamp"),
        price=Decimal("2.00"),
        minimum_price=Decimal("1.00"),
        price_editable=True,
    )

    class Control:
        async def click(self, **_kwargs):
            pass

    class Page:
        url = item.product_url

        def locator(self, _selector):
            return object()

        def get_by_role(self, *_args, **_kwargs):
            return object()

        def get_by_text(self, *_args, **_kwargs):
            return object()

    async def no_input(_locator):
        return None

    async def buy(*_locators):
        return Control()

    monkeypatch.setattr(bandcamp_adapter, "_only_visible_async", no_input)
    monkeypatch.setattr(bandcamp_adapter, "_first_visible_async", buy)

    with pytest.raises(cart_models.StoreStructureError, match="editable price field"):
        asyncio.run(bandcamp_adapter._add_to_cart_async(Page(), item, asyncio.Event()))


def test_beatport_and_bandcamp_are_added_serially_per_store_without_a_playlist(monkeypatch):
    bandcamp = resolved_item("bandcamp")
    beatport = resolved_item("beatport", price="2.49", currency="EUR")
    plan = cart_models.CartPlan((bandcamp, beatport))
    session = cart.CartBrowserSession()
    executed = []

    async def pages(_count=2):
        return [object(), object()]

    async def preflight(_requests, _pages, _cancel, _progress):
        return plan

    async def approve(candidate):
        return candidate

    async def execute(store, items, *_args):
        executed.append((store, items))
        return [cart_models.CartResult("10", items[0].track_label, store, "added")]

    async def final(successful, _uncertain=None):
        assert successful == {"bandcamp": [bandcamp], "beatport": [beatport]}
        return ("bandcamp", "beatport"), ()

    monkeypatch.setattr(session, "_work_pages", pages)
    monkeypatch.setattr(session, "_preflight", preflight)
    monkeypatch.setattr(session, "_execute_store", execute)
    monkeypatch.setattr(session, "_open_final_carts", final)

    outcome = asyncio.run(
        session.run_batch(
            (request("bandcamp", "beatport"),),
            asyncio.Event(),
            approve=approve,
        )
    )

    assert executed == [("bandcamp", [bandcamp]), ("beatport", [beatport])]
    assert {result.store for result in outcome.results if result.status == "added"} == {
        "bandcamp",
        "beatport",
    }
    assert not outcome.beatport_playlist_ready


def test_retry_targets_do_not_repeat_a_successful_store_for_the_same_track():
    outcome = cart_models.CartBatchOutcome(
        (
            cart_models.CartResult("10", "Signal", "bandcamp", "added"),
            cart_models.CartResult(
                "10",
                "Signal",
                "beatport",
                "failed",
                "browser failed",
                "browser_failure",
            ),
        )
    )

    assert outcome.retryable_targets == frozenset({("10", "beatport")})


def test_unverified_bandcamp_click_is_kept_open_for_manual_inspection(monkeypatch):
    item = resolved_item("bandcamp")
    plan = cart_models.CartPlan((item,))
    session = cart.CartBrowserSession()
    kept = []

    async def pages(_count=2):
        return [object(), object()]

    async def preflight(_requests, _pages, _cancel, _progress):
        return plan

    async def approve(candidate):
        return candidate

    async def execute(*_args):
        return [
            cart_models.CartResult(
                item.track_key,
                item.track_label,
                "bandcamp",
                "failed",
                "uncertain",
                "cart_unverified",
            )
        ]

    async def final(successful, uncertain):
        kept.append((successful, uncertain))
        return ("bandcamp",), ()

    monkeypatch.setattr(session, "_work_pages", pages)
    monkeypatch.setattr(session, "_preflight", preflight)
    monkeypatch.setattr(session, "_execute_store", execute)
    monkeypatch.setattr(session, "_open_final_carts", final)

    outcome = asyncio.run(
        session.run_batch(
            (request("bandcamp"),), asyncio.Event(), approve=approve
        )
    )

    assert kept == [({}, {"bandcamp": [item]})]
    assert outcome.cart_stores == ("bandcamp",)


def test_final_bandcamp_cart_is_one_tab_handed_over_to_the_person(monkeypatch, opened_contexts):
    item = resolved_item("bandcamp")
    session = cart.CartBrowserSession()

    async def navigate(_page, url, _store):
        _page.url = url
        return 200

    async def opened(_page):
        return True

    async def contains(_page, _item):
        return True

    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    monkeypatch.setattr(bandcamp_adapter, "_open_bandcamp_cart_async", opened)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_contains_async", contains)

    async def scenario():
        work = await session._work_pages(2)
        stores, warnings = await session._open_final_carts({"bandcamp": [item]})
        return work, stores, warnings

    work, stores, warnings = asyncio.run(scenario())

    assert len(opened_contexts) == 1, "no second browser"
    context = opened_contexts[0]
    [cart_page] = [page for page in context.pages if page not in work]
    assert stores == ("bandcamp",) and session._cart_stores == ("bandcamp",)
    assert not warnings
    assert cart_page.url == bandcamp_adapter.BANDCAMP_CART_URL
    assert cart_page.focused == 1 and cart_page.released


def test_existing_bandcamp_item_is_checked_in_the_global_cart(monkeypatch):
    item = resolved_item("bandcamp")

    class Page:
        url = item.product_url

    page = Page()

    async def navigate(_page, url, _store):
        _page.url = url

    async def contains(_page, _item):
        return _page.url == bandcamp_adapter.BANDCAMP_CART_URL

    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_contains_async", contains)

    assert asyncio.run(bandcamp_adapter._cart_contains_async(page, item, asyncio.Event()))
    assert page.url == bandcamp_adapter.BANDCAMP_CART_URL


def test_showing_the_carts_again_opens_a_fresh_cart_tab(monkeypatch, opened_contexts):
    session = cart.CartBrowserSession()
    session._cart_stores = ("bandcamp",)

    async def navigate(_page, url, _store):
        _page.url = url
        return 200

    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    asyncio.run(session.focus_carts())

    [page] = opened_contexts[0].pages
    assert page.url == bandcamp_adapter.BANDCAMP_CART_URL
    assert page.focused == 1 and page.released


def test_a_new_batch_hides_the_window_the_carts_were_shown_in(opened_contexts):
    session = cart.CartBrowserSession()

    async def scenario():
        context = await session._shown_context()
        assert context.visible
        await session._work_pages(2)
        return context

    context = asyncio.run(scenario())

    assert not context.visible and context.hides == 1


def test_final_cart_view_failure_keeps_verified_results(monkeypatch):
    """A relaunch race used to throw away a batch whose clicks had all verified."""

    item = resolved_item("bandcamp")
    plan = cart_models.CartPlan((item,))
    session = cart.CartBrowserSession()

    async def pages(_count=2):
        return [object(), object()]

    async def preflight(_requests, _pages, _cancel, _progress):
        return plan

    async def approve(candidate):
        return candidate

    async def execute(store, items, *_args):
        return [cart_models.CartResult(item.track_key, item.track_label, store, "added")]

    async def no_window():
        raise automation_errors.AutomationError("the window could not be shown")

    monkeypatch.setattr(session, "_work_pages", pages)
    monkeypatch.setattr(session, "_preflight", preflight)
    monkeypatch.setattr(session, "_execute_store", execute)
    monkeypatch.setattr(session, "_shown_context", no_window)

    outcome = asyncio.run(
        session.run_batch((request("bandcamp"),), asyncio.Event(), approve=approve)
    )

    statuses = {(r.track_key, r.code): r.status for r in outcome.results}
    assert statuses[(item.track_key, "")] == "added"
    assert any(r.code == "cart_view_failed" for r in outcome.results)
    assert outcome.cart_stores == ()


def test_verification_stage_timeouts_fit_inside_the_outer_budget():
    assert sum(seconds for _name, seconds in cart_models.VERIFY_STAGES) <= cart_models.VERIFY_BUDGET_SECONDS


def test_slow_reload_stage_reports_its_stage_name(monkeypatch):
    item = resolved_item("bandcamp")
    monkeypatch.setattr(bandcamp_adapter, "VERIFY_STAGES", (("count", 0.01), ("sidecart", 0.01), ("reload", 0.05)))

    async def count(_page):
        return None

    async def contains(_page, _item):
        return False

    async def slow_navigate(_page, _url, _store):
        await asyncio.sleep(1)

    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_contains_async", contains)
    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", slow_navigate)

    outcome = asyncio.run(bandcamp_adapter._verify_bandcamp_click_async(object(), item, None))

    assert outcome.verified is False
    assert outcome.stage == "reload"
    assert outcome.elapsed < 0.5, "the reload stage stopped on its own clock"


class DiagnosticPage:
    url = "https://label.bandcamp.com/track/one?fan_id=42"

    def __init__(self):
        self.shots = []

    async def screenshot(self, path, full_page=False):
        self.shots.append(path)
        open(path, "wb").write(b"png")

    async def content(self):
        return (
            "<html><script>var fan = {\"id\": 42}</script>"
            "<a href=\"/track/one?action=download&token=x\">x</a></html>"
        )


def test_diagnostics_strip_query_strings_and_scripts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    page = DiagnosticPage()

    folder = asyncio.run(
        bandcamp_adapter.save_cart_diagnostics(page, "bandcamp", page.url, "cart_unverified")
    )

    assert folder is not None and folder.parent == tmp_path / "dj-digger" / "cart-diagnostics"
    html_text = (folder / "page.html").read_text()
    assert "fan" not in html_text and "token=x" not in html_text
    assert "<script></script>" in html_text
    meta = json.loads((folder / "meta.json").read_text())
    assert "fan_id" not in meta["product_url"] and meta["code"] == "cart_unverified"
    assert (folder / "page.png").exists()


def test_diagnostics_redact_email_addresses_of_the_signed_in_account():
    html_text = (
        '<a href="mailto:dj.name+x@example.co.uk">dj.name+x@example.co.uk</a>'
        '<span data-fan="&quot;fan@mail.example&quot;">Signed in as Fan_1@Mail.example.</span>'
    )

    redacted = bandcamp_adapter.redact_diagnostic_html(html_text)

    assert "@" not in redacted
    assert redacted.count("<email>") == 4
    assert "Signed in as <email>." in redacted


def test_diagnostics_survive_a_screenshot_the_hidden_window_cannot_take(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    class HiddenPage(DiagnosticPage):
        async def screenshot(self, path, full_page=False):
            raise automation_errors.AutomationError("the window is not visible")

    folder = asyncio.run(
        bandcamp_adapter.save_cart_diagnostics(HiddenPage(), "bandcamp", HiddenPage.url, "x")
    )

    assert folder is not None and (folder / "page.html").exists()
    assert not (folder / "page.png").exists()


def test_diagnostics_keep_only_the_last_ten(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    root = tmp_path / "dj-digger" / "cart-diagnostics"
    root.mkdir(parents=True)
    for index in range(12):
        (root / f"2026010{index // 10}-00000{index % 10}-bandcamp-old").mkdir()

    asyncio.run(
        bandcamp_adapter.save_cart_diagnostics(DiagnosticPage(), "bandcamp", DiagnosticPage.url, "x")
    )

    assert len([p for p in root.iterdir() if p.is_dir()]) == cart_models.CART_DIAGNOSTICS_KEEP


def test_second_unverified_click_switches_the_store_to_manual_mode(monkeypatch):
    items = [
        replace(
            resolved_item("bandcamp"),
            track_key=str(10 + n),
            product_id=str(n),
            product_url=f"https://artist.bandcamp.com/track/{n}",
        )
        for n in range(4)
    ]
    session = cart.CartBrowserSession()
    clicks = []

    async def refresh(_page, item, _cancel):
        return item

    async def contains(_page, _item, _cancel, navigate=True):
        return False

    async def count(_page):
        return None

    async def add(_page, item, _cancel):
        clicks.append(item.product_id)

    async def verify(_page, _item, _before):
        return cart_models.VerifyOutcome(False, "reload", 1.0)

    async def no_diagnostics(*_args, **_kwargs):
        return None

    monkeypatch.setattr(bandcamp_adapter, "_refresh_item_async", refresh)
    monkeypatch.setattr(bandcamp_adapter, "_cart_contains_async", contains)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(bandcamp_adapter, "_add_to_cart_async", add)
    monkeypatch.setattr(bandcamp_adapter, "_verify_bandcamp_click_async", verify)
    monkeypatch.setattr(bandcamp_adapter, "save_cart_diagnostics", no_diagnostics)

    results = asyncio.run(
        session._execute_store("bandcamp", items, object(), asyncio.Event(), None, [0], 4)
    )

    assert clicks == ["0", "1"], "the third and fourth are never clicked"
    assert [r.code for r in results] == ["cart_unverified"] * 4
    assert "manual completion" in results[2].reason


def test_manual_completion_records_manual_results_without_clicking(monkeypatch):
    item = resolved_item("bandcamp")
    session = cart.CartBrowserSession()

    class Page:
        url = item.product_url
        closed = False

        async def bring_to_front(self):
            pass

        def is_closed(self):
            return self.closed

        async def close(self):
            self.closed = True

        def get_by_role(self, *_a, **_k):
            return None

        def get_by_text(self, *_a, **_k):
            return None

        def locator(self, *_a, **_k):
            return None

    class Context:
        async def new_page(self):
            return Page()

    async def context():
        return Context()

    async def navigate(_page, _url, _store):
        return 200

    async def banner(_page):
        return None

    async def nothing_visible(*_locators):
        return None

    added = []

    async def contains(_page, _item, _cancel, navigate=True):
        return bool(added)

    async def manual(items):
        added.extend(items)  # "the person pressed Add to cart"
        return True

    monkeypatch.setattr(session, "_shown_context", context)
    monkeypatch.setattr(bandcamp_adapter, "_navigate_async", navigate)
    monkeypatch.setattr(bandcamp_adapter, "_dismiss_bandcamp_cookie_banner", banner)
    monkeypatch.setattr(bandcamp_adapter, "_first_visible_async", nothing_visible)
    monkeypatch.setattr(bandcamp_adapter, "_only_visible_async", nothing_visible)
    monkeypatch.setattr(bandcamp_adapter, "_cart_contains_async", contains)

    results = asyncio.run(session.finish_manually([item], manual, asyncio.Event()))

    assert [(r.status, r.code) for r in results] == [("manual", "manual_verified")]


def test_cancel_after_a_cart_click_finishes_verification_instead_of_clicking_again(
    monkeypatch,
):
    item = resolved_item("bandcamp")
    cancel = asyncio.Event()
    clicks = 0

    async def refresh(_page, candidate, _cancel):
        return candidate

    async def contains(_page, _candidate, _cancel, **_kwargs):
        return False

    async def add(_page, _candidate, _cancel):
        nonlocal clicks
        clicks += 1
        cancel.set()

    async def verify(_page, _candidate, _count):
        return cart_models.VerifyOutcome(True, "count", 0.1)

    async def count(_page):
        return 0

    monkeypatch.setattr(bandcamp_adapter, "_refresh_item_async", refresh)
    monkeypatch.setattr(bandcamp_adapter, "_cart_contains_async", contains)
    monkeypatch.setattr(bandcamp_adapter, "_add_to_cart_async", add)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(bandcamp_adapter, "_verify_bandcamp_click_async", verify)
    session = cart.CartBrowserSession()

    results = asyncio.run(
        session._execute_store(
            "bandcamp", [item], object(), cancel, None, [0], 1
        )
    )

    assert clicks == 1
    assert results[0].status == "added"


def test_soundiiz_limit_is_reported_apart_from_import_failures(tmp_path, monkeypatch):
    import asyncio

    from dj_digger import beatport_playlist
    from dj_digger.services.purchases import prepare_playlist

    lines = tuple(f"Artist - Track {index}" for index in range(201))
    monkeypatch.setattr(beatport_playlist, "_beatport_playlist_lines", lambda requests, outcome: lines)
    monkeypatch.setattr(beatport_playlist, "_beatport_playlist_requests", lambda requests, outcome: lines)
    def post(*args, **kwargs):
        raise AssertionError("an oversized import must not reach Soundiiz")
    monkeypatch.setattr(beatport_playlist.requests, "post", post)
    exported = asyncio.run(prepare_playlist((), None, "Big", tmp_path, ""))
    assert exported.limit_exceeded and exported.import_failed
    assert len(exported.path.read_text(encoding="utf-8").splitlines()) == 201


def test_a_missing_extension_is_reported_and_never_becomes_a_beatport_playlist(monkeypatch):
    missing = ExtensionMissing("The dj-digger browser extension is not connected")

    async def no_extension(visible=False, listener=None, *, background=False):
        raise missing

    monkeypatch.setattr(cart.webpage.AsyncExtensionContext, "open", no_extension)
    release = cart_models.CartRequest(
        track("Signal"), (("beatport", "https://www.beatport.com/release/signal/1"),)
    )
    session = cart.CartBrowserSession()

    async def approve(_plan):
        pytest.fail("nothing was looked up")

    with pytest.raises(ExtensionMissing, match="not connected") as caught:
        asyncio.run(session.run_batch((release,), asyncio.Event(), approve=approve))

    assert isinstance(caught.value, automation_errors.AutomationError)
    result = cart._preflight_failure(
        release, "Artist - Signal", "beatport", release.links[0][1], missing
    )
    assert (result.status, result.code) == ("failed", "browser_failure")
    assert "not connected" in result.reason


def test_a_cancelled_verification_stage_does_not_overlap_the_next_one(monkeypatch, request):
    """Real adapter, fake extension: stage two is only sent once stage one's late reply is in."""

    listener, ext = request.getfixturevalue("webext_world")
    item = resolved_item("bandcamp")
    monkeypatch.setattr(
        bandcamp_adapter, "VERIFY_STAGES", (("count", 0.05), ("sidecart", 2.0), ("reload", 0.05))
    )
    late = threading.Event()
    overlapped = []
    ext.handlers["dom.text"] = lambda args: HOLD  # the count stage hangs
    ext.handlers["dom.count"] = lambda args: overlapped.append(not late.is_set()) or 1

    async def count(page):
        await page.locator("#cart-count").inner_text()

    async def contains(page, _item):
        return await page.locator("#side-cart .item").count() > 0

    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(bandcamp_adapter, "_bandcamp_cart_contains_async", contains)

    async def scenario():
        async with webpage.async_session(False, listener) as context:
            page = await context.new_page()

            async def release_late_reply():
                await asyncio.sleep(0.3)
                late.set()
                ext.fail(ext.commands("dom.text")[0]["id"], "cancelled", "command cancelled")

            releasing = asyncio.ensure_future(release_late_reply())
            outcome = await bandcamp_adapter._verify_bandcamp_click_async(page, item, 0)
            await releasing
            return outcome

    outcome = asyncio.run(scenario())

    assert outcome.verified and outcome.stage == "sidecart"
    assert overlapped == [False], "the sidecart command waited for the cancelled stage's reply"
    assert len(ext.cancels) == 1

import asyncio
import html
import json
import logging
from dataclasses import replace
from decimal import Decimal

import pytest

from dj_digger import cart
from dj_digger.models import Track


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
    assert cart.is_store_url(url, store)


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
    assert not cart.is_store_url(url, store)


def test_plain_http_store_link_is_upgraded_only_after_domain_validation():
    assert (
        cart.canonical_store_url(
            "http://artist.bandcamp.com/album/release?from=embed", "bandcamp"
        )
        == "https://artist.bandcamp.com/album/release?from=embed"
    )
    assert cart.canonical_store_url("http://bandcamp.com.evil.test/a", "bandcamp") is None
    assert cart.canonical_store_url("http://user:pass@bandcamp.com/a", "bandcamp") is None


def test_cart_log_text_redacts_queries_and_obvious_secret_fields():
    safe = cart.log_safe_text(
        "failed https://www.beatport.com/login?state=secret oauth_token=also-secret"
    )

    assert safe == "failed https://www.beatport.com/login oauth_token=<redacted>"


def product(title, *, artist="Artist", product_id="1"):
    return cart.StoreProduct(
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
    chosen = cart.match_product(
        track("Artist - Signal (VIP) [PREMIERE]"),
        [product("Signal"), product("Signal (VIP)", product_id="2")],
    )

    assert chosen.product_id == "2"


def test_a_different_version_is_never_treated_as_the_same_track():
    with pytest.raises(cart.UnsafeMatch, match="version"):
        cart.match_product(track("Signal (VIP)"), [product("Signal (Original Mix)")])


def test_duplicate_exact_titles_need_an_artist_tie_breaker():
    chosen = cart.match_product(
        track("Signal", artist="Right Artist"),
        [
            product("Signal", artist="Other Artist", product_id="1"),
            product("Signal", artist="Right Artist", product_id="2"),
        ],
    )

    assert chosen.product_id == "2"


def test_an_unresolved_duplicate_is_skipped_instead_of_guessed():
    with pytest.raises(cart.UnsafeMatch, match="ambiguous"):
        cart.match_product(
            track("Signal", artist="Uploader"),
            [product("Signal", artist="One"), product("Signal", artist="Two", product_id="2")],
        )


def test_a_common_artist_word_cannot_break_a_title_tie():
    with pytest.raises(cart.UnsafeMatch, match="ambiguous"):
        cart.match_product(
            track("Signal", artist="The Right Artist"),
            [
                product("Signal", artist="The Wrong Artist", product_id="1"),
                product("Signal", artist="Someone Else", product_id="2"),
            ],
        )


def test_no_exact_title_is_a_business_unavailability():
    with pytest.raises(cart.ProductUnavailable, match="exact"):
        cart.match_product(track("Signal"), [product("Another Track")])


def test_a_unique_trailing_title_survives_different_artist_aliases():
    chosen = cart.match_product(
        track("Phil:osophy - Remember", artist="UKF"),
        [product("Philth Tangent - Remember", artist="Philth Tangent")],
    )

    assert chosen.title == "Philth Tangent - Remember"


def test_double_slash_promo_title_can_match_one_exact_segment():
    chosen = cart.match_product(
        track("Impak // Fractals // C4CDIGUK045", artist="Cause4Concern"),
        [product("No Time"), product("Fractals", product_id="2")],
    )

    assert chosen.product_id == "2"


def test_quoted_premiere_title_ignores_uploader_and_label_context():
    chosen = cart.match_product(
        track("PREMIERE: Rohaan 'I Found You' [Mad Zoo]", artist="dtdnb"),
        [product("City of Ezra"), product("I Found You", product_id="2")],
    )

    assert chosen.product_id == "2"


def test_catalogue_context_does_not_hide_an_exact_trailing_title():
    chosen = cart.match_product(
        track("Aristide - Check It Out [TX006]", artist="Tx Records"),
        [product("Check It Out")],
    )

    assert chosen.product_id == "1"


def test_feat_and_ft_are_equivalent_in_an_exact_trailing_title():
    chosen = cart.match_product(
        track("Philth - Ravanelli (ft. Sense MC)", artist="Drum&BassArena"),
        [product("Ravanelli (feat. Sense MC)")],
    )

    assert chosen.product_id == "1"


def test_radio_cut_is_not_mistaken_for_the_full_store_track():
    with pytest.raises(cart.UnsafeMatch, match="version"):
        cart.match_product(
            track("Arkaik & Creatures - Stroboscope (NOISIA RADIO S6E29 Cut)"),
            [product("Stroboscope")],
        )


def test_duplicate_trailing_titles_are_still_ambiguous():
    with pytest.raises(cart.UnsafeMatch, match="trailing"):
        cart.match_product(
            track("Alias - Remember"),
            [
                product("First Artist - Remember", product_id="1"),
                product("Second Artist - Remember", product_id="2"),
            ],
        )


def test_trailing_title_never_hides_a_version_mismatch():
    with pytest.raises(cart.UnsafeMatch, match="version"):
        cart.match_product(
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
    products = cart.products_from_html(
        bandcamp_html(), "https://right.bandcamp.com/album/release", "bandcamp"
    )

    chosen = cart.match_product(track("Signal (VIP)", artist="Right Artist"), products)
    assert chosen.product_id == "13"
    assert chosen.url == "https://right.bandcamp.com/track/signal-vip"


def test_bandcamp_price_is_decimal_and_cross_checked_on_the_track_page():
    products = cart.products_from_html(
        bandcamp_html(), "https://right.bandcamp.com/track/signal", "bandcamp"
    )

    chosen = cart.match_product(track("Signal", artist="Right Artist"), products)
    assert chosen.price == Decimal("1.10")
    assert chosen.currency == "GBP"


def test_bandcamp_conflicting_visible_and_structured_prices_are_refused():
    with pytest.raises(cart.AutomationError, match="price"):
        cart.products_from_html(
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

    products = cart.products_from_html(
        f'<script type="application/ld+json">{json.dumps(structured)}</script>',
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    assert products == [
        cart.StoreProduct(
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

    products = cart.products_from_html(
        f'<script type="application/ld+json">{json.dumps(structured)}</script>',
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    assert products[0].price is None
    assert products[0].currency == ""


def test_beatport_release_can_resolve_semantic_track_links_before_visiting_the_track():
    products = cart.products_from_html(
        '<a href="/track/signal/987" aria-label="Signal">Signal</a>',
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    assert products == [
        cart.StoreProduct(
            store="beatport",
            url="https://www.beatport.com/track/signal/987",
            product_id="987",
            title="Signal",
        )
    ]


def test_beatport_release_uses_the_track_slug_to_keep_the_exact_remix():
    products = cart.products_from_html(
        """
        <a href="/track/signal-original-mix/986" aria-label="Signal">Signal</a>
        <a href="/track/signal-rido-remix/987" aria-label="Signal">Signal</a>
        """,
        "https://www.beatport.com/release/release/123",
        "beatport",
    )

    chosen = cart.match_product(track("Signal (Rido Remix)"), products)

    assert chosen.product_id == "987"


def test_store_security_challenge_is_a_technical_failure_not_missing_product():
    html = """
        <html><head><title>Just a moment...</title></head>
        <body>Performing security verification. Ray ID: public-challenge-id</body></html>
    """

    with pytest.raises(cart.SecurityChallengeBlocked, match="automated browser"):
        cart.products_from_html(
            html,
            "https://www.beatport.com/release/release/123",
            "beatport",
        )


def test_name_your_price_uses_a_positive_default_then_an_explicit_step():
    assert cart.purchase_price(Decimal("0"), Decimal("1.00"), Decimal("0.01")) == Decimal("1.00")
    assert cart.purchase_price(Decimal("0"), None, Decimal("0.01")) == Decimal("0.01")


def test_name_your_price_without_a_positive_value_is_not_guessed():
    with pytest.raises(cart.AutomationError, match="positive"):
        cart.purchase_price(Decimal("0"), None, None)


def request(*stores):
    return cart.CartRequest(
        track=track("Signal"),
        links=tuple(
            (store, f"https://{'artist.bandcamp.com' if store == 'bandcamp' else 'www.beatport.com'}/track/signal/1")
            for store in stores
        ),
    )


def resolved_item(store, *, already=False, price="1.25", currency="GBP"):
    return cart.CartItem(
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
    plan = cart.CartPlan(
        items=(
            resolved_item("bandcamp", price="1.25", currency="GBP"),
            resolved_item("beatport", price="2.49", currency="EUR"),
            resolved_item("bandcamp", already=True, price="9.00", currency="GBP"),
        ),
        results=(cart.CartResult("20", "Missing", "bandcamp", "skipped", "no exact track"),),
    )

    summary = plan.summary()

    assert "GBP 1.25" in summary
    assert "EUR 2.49" in summary
    assert "GBP 10.25" not in summary
    assert "already in cart" in summary
    assert "Missing" in summary


def test_preflight_summary_flattens_untrusted_track_labels():
    item = replace(resolved_item("bandcamp"), track_label="Track\nGBP 999 [bold]")

    summary = cart.CartPlan(items=(item,)).summary()

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

    def is_visible(self):
        return self.visible


class MultiLocator:
    def __init__(self, visibility):
        self.visibility = visibility

    def count(self):
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
    chosen = cart._first_visible(MultiLocator([False, True]))

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
    return cart.CartPlan(items=(first, second))


def async_requests(count=6):
    return tuple(
        cart.CartRequest(
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

    monkeypatch.setattr(cart, "_resolve_cart_item_async", resolve)
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
        raise cart.StoreStructureError("same missing product marker")

    async def replace_page(_page):
        return object()

    monkeypatch.setattr(cart, "_resolve_cart_item_async", broken)
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
        raise cart.SecurityChallengeBlocked("automated browser rejected")

    candidate = cart.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/track/signal/1?token=secret"),),
    )
    monkeypatch.setattr(cart, "_resolve_cart_item_async", blocked)
    session = cart.CartBrowserSession()

    with caplog.at_level(logging.INFO, logger="dj_digger.cart"):
        plan = asyncio.run(
            session._preflight((candidate,), [object(), object()], asyncio.Event(), None)
        )

    assert plan.results[0].code == "playlist_ready"
    assert plan.results[0].status == "playlist_ready"
    assert "Cart preflight result" in caplog.text
    assert "https://www.beatport.com/track/signal/1" in caplog.text
    assert "token=secret" not in caplog.text


def test_bandcamp_preflight_does_not_require_an_account_login(monkeypatch):
    async def no_login(*_args, **_kwargs):
        pytest.fail("Bandcamp cart cookies must not be gated on account login")

    async def resolve(_page, candidate, store, url, _cancel):
        return replace(
            resolved_item(store),
            track_key=candidate.key,
            track_label=candidate.label,
            source_url=url,
            product_url=url,
        )

    monkeypatch.setattr(cart, "_ensure_logins_async", no_login)
    monkeypatch.setattr(cart, "_resolve_cart_item_async", resolve)
    session = cart.CartBrowserSession()

    plan = asyncio.run(
        session._preflight(async_requests(1), [object(), object()], asyncio.Event(), None)
    )

    assert len(plan.items) == 1


def test_cart_session_relaunches_the_same_profile_visible_only_when_requested(
    tmp_path, monkeypatch
):
    import playwright.async_api

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("DISPLAY", ":test")
    calls = []
    contexts = []

    class Context:
        def __init__(self):
            self.pages = [object()]
            self.closed = False

        def is_closed(self):
            return self.closed

        def set_default_timeout(self, _timeout):
            pass

        def on(self, _event, _callback):
            pass

        async def close(self, **_kwargs):
            self.closed = True

    class Chromium:
        executable_path = str(executable)

        async def launch_persistent_context(self, profile, **options):
            context = Context()
            calls.append((profile, options["headless"]))
            contexts.append(context)
            return context

    class Playwright:
        chromium = Chromium()

        async def stop(self):
            pass

    class Starter:
        async def start(self):
            return Playwright()

    monkeypatch.setattr(playwright.async_api, "async_playwright", Starter)
    profile = tmp_path / "store-browser"
    session = cart.CartBrowserSession(profile)

    async def scenario():
        await session._ensure_context()
        await session._ensure_context()
        await session.close()

    asyncio.run(scenario())

    assert calls == [(str(profile), False)], "one headed window, reused, never relaunched"
    assert contexts[0].closed


def test_beatport_preflight_reads_public_metadata_before_requesting_login(monkeypatch):
    async def no_login(*_args, **_kwargs):
        pytest.fail("preflight must not enter the Beatport login challenge")

    async def resolve(_page, candidate, store, url, _cancel):
        return replace(
            resolved_item(store, price="2.49", currency="EUR"),
            track_key=candidate.key,
            track_label=candidate.label,
            source_url=url,
            product_url=url,
        )

    candidate = cart.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/track/signal/1"),),
    )
    monkeypatch.setattr(cart, "_ensure_logins_async", no_login)
    monkeypatch.setattr(cart, "_resolve_cart_item_async", resolve)
    session = cart.CartBrowserSession()

    plan = asyncio.run(
        session._preflight((candidate,), [object(), object()], asyncio.Event(), None)
    )

    assert plan.items[0].store == "beatport"


def test_beatport_lookup_failure_still_prepares_a_metadata_playlist(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise cart.ProductUnavailable("linked release has no exact track")

    candidate = cart.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/release/signal/1"),),
    )
    monkeypatch.setattr(cart, "_resolve_cart_item_async", unavailable)
    session = cart.CartBrowserSession()

    plan = asyncio.run(
        session._preflight((candidate,), [object(), object()], asyncio.Event(), None)
    )

    assert not plan.items
    assert plan.results == (
        cart.CartResult(
            candidate.track.key,
            candidate.track.label,
            "beatport",
            "playlist_ready",
            "Beatport will match this track by artist and title",
            "playlist_ready",
            candidate.links[0][1],
        ),
    )


def test_direct_beatport_track_becomes_a_playlist_without_starting_chromium(
    monkeypatch,
):
    candidate = cart.CartRequest(
        track("Signal"),
        (("beatport", "https://www.beatport.com/track/signal/987?token=secret"),),
    )
    session = cart.CartBrowserSession()

    async def no_pages(*_args, **_kwargs):
        pytest.fail("an exact Beatport track URL must not start Chromium")

    async def no_approval(_plan):
        pytest.fail("playlist preparation remains the explicit user approval")

    monkeypatch.setattr(session, "_work_pages", no_pages)

    outcome = asyncio.run(
        session.run_batch(
            (candidate,), asyncio.Event(), approve=no_approval
        )
    )

    assert outcome.results == (
        cart.CartResult(
            candidate.track.key,
            candidate.track.label,
            "beatport",
            "playlist_ready",
            "ready for Beatport playlist transfer",
            "playlist_ready",
            "https://www.beatport.com/track/signal/987",
        ),
    )


def test_a_known_security_challenge_stops_without_looping_or_structure_retry():
    class ChallengePage:
        url = "https://www.beatport.com/track/signal/1"
        focused = False

        async def content(self):
            return "<title>Just a moment...</title>Performing security verification"

        async def bring_to_front(self):
            self.focused = True

    page = ChallengePage()
    with pytest.raises(cart.SecurityChallengeBlocked, match="automated browser"):
        asyncio.run(cart._page_products_async(page, "beatport", asyncio.Event()))

    assert page.focused


def test_bandcamp_storefront_without_a_release_is_not_a_structure_failure(monkeypatch):
    class StorefrontPage:
        url = "https://label.bandcamp.com/"

        async def content(self):
            return "<html><title>Label</title><body>music</body></html>"

    async def no_dom_products(_page):
        return []

    monkeypatch.setattr(cart, "_bandcamp_dom_products", no_dom_products)

    with pytest.raises(cart.ProductUnavailable, match="storefront"):
        asyncio.run(
            cart._page_products_async(StorefrontPage(), "bandcamp", asyncio.Event())
        )


def test_bandcamp_dom_price_merges_by_product_path_not_download_query(monkeypatch):
    class ProductPage:
        url = "https://label.bandcamp.com/track/signal"

        async def content(self):
            return "<html><title>Signal</title></html>"

    parsed = cart.StoreProduct(
        "bandcamp",
        "https://label.bandcamp.com/track/signal?action=download",
        "999",
        "full digital discography",
    )
    current = cart.StoreProduct(
        "bandcamp",
        "https://label.bandcamp.com/track/signal",
        "123",
        "Signal",
        price=Decimal("1.99"),
        currency="GBP",
    )

    monkeypatch.setattr(cart, "products_from_html", lambda *_args: [parsed])

    async def dom_products(_page):
        return [current]

    monkeypatch.setattr(cart, "_bandcamp_dom_products", dom_products)

    products = asyncio.run(
        cart._page_products_async(ProductPage(), "bandcamp", asyncio.Event())
    )

    assert products == [current]


def test_bandcamp_dead_source_can_resolve_an_exact_autocomplete_track(monkeypatch):
    candidate = track("Revan & Ollie Norton - Lights On", artist="Flexout Audio")
    found = cart.StoreProduct(
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

    monkeypatch.setattr(cart, "_navigate_async", navigate)
    monkeypatch.setattr(cart, "_bandcamp_search_candidates_async", search)

    chosen = asyncio.run(
        cart._resolve_bandcamp_product_async(
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

        def nth(self, _index):
            return Anchor()

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

    monkeypatch.setattr(cart, "_first_visible_match_async", visible)
    monkeypatch.setattr(cart, "_navigate_async", navigate)

    _tracks, albums = asyncio.run(
        cart._bandcamp_search_candidates_async(
            Page(), track("Artist - Signal"), asyncio.Event()
        )
    )

    assert visited == [cart.STORE_HOME["bandcamp"]]
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

    monkeypatch.setattr(cart, "_navigate_async", navigate)
    monkeypatch.setattr(cart, "_bandcamp_search_candidates_async", search)
    monkeypatch.setattr(cart, "_page_products_async", products)

    chosen = asyncio.run(
        cart._resolve_bandcamp_product_async(
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


def test_beatport_release_resolution_keeps_the_exact_track_without_opening_cart(
    monkeypatch,
):
    candidate = track("Signal (Rido Remix)")
    product = cart.StoreProduct(
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
        return cart.PriceQuote("EUR", Decimal("2.49"), Decimal("2.49"))

    async def no_cart(*_args, **_kwargs):
        pytest.fail("Beatport playlist lookup must not inspect the cart")

    monkeypatch.setattr(cart, "_navigate_async", navigate)
    monkeypatch.setattr(cart, "_page_products_async", products)
    monkeypatch.setattr(cart, "_quote_async", quote)
    monkeypatch.setattr(cart, "_cart_contains_async", no_cart)

    item = asyncio.run(
        cart._resolve_cart_item_async(
            page,
            candidate,
            "beatport",
            "https://www.beatport.com/release/signal/123",
            asyncio.Event(),
        )
    )

    assert item.product_url == product.url
    assert not item.already_in_cart


def test_beatport_release_rejects_a_different_mix_on_the_target_page(monkeypatch):
    candidate = track("Signal (Rido Remix)")
    release_product = cart.StoreProduct(
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

    monkeypatch.setattr(cart, "_navigate_async", navigate)
    monkeypatch.setattr(cart, "_page_products_async", products)

    with pytest.raises(cart.UnsafeMatch, match="version"):
        asyncio.run(
            cart._resolve_cart_item_async(
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

    monkeypatch.setattr(cart, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(cart, "_navigate_async", no_reload)

    assert asyncio.run(
        cart._verify_bandcamp_click_async(object(), resolved_item("bandcamp"), 2)
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
            if selector == cart.SIDECART_REMOVE:
                return Nodes([Element(shown=True)]) if removable else Nodes([])
            return Nodes([])

        def get_by_role(self, _role, *, name):
            return Nodes([])

    class Page:
        url = "https://artist.bandcamp.com/track/signal/1"

        def locator(self, selector):
            if selector == cart.SIDECART_ROWS:
                return Nodes([Row(shown=visible)])
            return Nodes([])

    async def cart_closed(_page):
        return False

    monkeypatch.setattr(cart, "_open_bandcamp_cart_async", cart_closed)

    assert (
        asyncio.run(cart._bandcamp_cart_contains_async(Page(), resolved_item("bandcamp")))
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

    monkeypatch.setattr(cart, "_first_visible_async", first)
    monkeypatch.setattr(cart, "_only_visible_async", no_input)
    candidate = replace(product("Signal"), price=Decimal("1.00"), currency="GBP")

    quote = asyncio.run(cart._bandcamp_quote_async(Page(), candidate))

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

    monkeypatch.setattr(cart, "_first_visible_async", first)
    monkeypatch.setattr(cart, "_only_visible_async", price_input)
    candidate = replace(product("Signal"), price=Decimal("0"), currency="GBP")

    quote = asyncio.run(cart._bandcamp_quote_async(Page(), candidate))

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

    monkeypatch.setattr(cart, "_only_visible_async", no_input)
    monkeypatch.setattr(cart, "_first_visible_async", buy)

    with pytest.raises(cart.StoreStructureError, match="editable price field"):
        asyncio.run(cart._add_to_cart_async(Page(), item, asyncio.Event()))


def test_login_challenge_is_detected_once_without_a_verification_loop():
    class Response:
        status = 403

    class ChallengePage:
        url = "https://www.beatport.com/"
        content_calls = 0
        focused = 0

        async def goto(self, url, **_kwargs):
            self.url = url
            return Response()

        async def content(self):
            self.content_calls += 1
            return "<title>Just a moment...</title>Performing security verification"

        async def bring_to_front(self):
            self.focused += 1

        def get_by_role(self, *_args, **_kwargs):
            class NoBanner:
                @property
                def first(self):
                    return self

                async def wait_for(self, **_kwargs):
                    raise RuntimeError("no cookie banner on a challenge page")

            return NoBanner()

    page = ChallengePage()

    with pytest.raises(cart.SecurityChallengeBlocked, match="stopped safely"):
        asyncio.run(cart._ensure_logins_async({"bandcamp": page}, asyncio.Event()))

    assert page.content_calls == 1
    assert page.focused == 1


def test_beatport_becomes_playlist_while_bandcamp_continues_without_login(
    monkeypatch,
):
    bandcamp = resolved_item("bandcamp")
    beatport = resolved_item("beatport", price="2.49", currency="EUR")
    plan = cart.CartPlan((bandcamp, beatport))
    session = cart.CartBrowserSession()

    async def pages(_count=2):
        return [object(), object()]

    async def preflight(_requests, _pages, _cancel, _progress):
        return plan

    async def approve(candidate):
        return candidate

    async def no_login(*_args, **_kwargs):
        pytest.fail("the Beatport playlist flow must not attempt managed login")

    async def execute(store, items, *_args):
        assert store == "bandcamp"
        assert items == [bandcamp]
        return [cart.CartResult("10", bandcamp.track_label, store, "added")]

    async def final(_pages, successful, _uncertain=None):
        assert successful == {"bandcamp": [bandcamp]}
        return ("bandcamp",), ()

    monkeypatch.setattr(session, "_work_pages", pages)
    monkeypatch.setattr(session, "_preflight", preflight)
    monkeypatch.setattr(session, "_execute_store", execute)
    monkeypatch.setattr(session, "_open_final_carts", final)
    monkeypatch.setattr(cart, "_ensure_logins_async", no_login)

    outcome = asyncio.run(
        session.run_batch(
            (request("bandcamp", "beatport"),),
            asyncio.Event(),
            approve=approve,
        )
    )

    assert any(
        result.status == "added" and result.store == "bandcamp"
        for result in outcome.results
    )
    playlist = next(result for result in outcome.results if result.code == "playlist_ready")
    assert playlist.store == "beatport"
    assert playlist.status == "playlist_ready"
    assert playlist.url == beatport.product_url
    assert outcome.beatport_playlist_ready


def test_retry_targets_do_not_repeat_a_successful_store_for_the_same_track():
    outcome = cart.CartBatchOutcome(
        (
            cart.CartResult("10", "Signal", "bandcamp", "added"),
            cart.CartResult(
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
    plan = cart.CartPlan((item,))
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
            cart.CartResult(
                item.track_key,
                item.track_label,
                "bandcamp",
                "failed",
                "uncertain",
                "cart_unverified",
            )
        ]

    async def final(_pages, successful, uncertain):
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


def test_final_bandcamp_cart_is_the_first_visible_work_page(monkeypatch):
    item = resolved_item("bandcamp")
    session = cart.CartBrowserSession()
    launches = []

    class Page:
        url = "about:blank"
        focused = 0

        async def bring_to_front(self):
            self.focused += 1

        def is_closed(self):
            return False

    page = Page()

    async def work_pages(count):
        launches.append(count)
        return [page]

    async def navigate(_page, url, _store):
        _page.url = url
        return 200

    async def opened(_page):
        return True

    async def contains(_page, _item):
        return True

    monkeypatch.setattr(session, "_work_pages", work_pages)
    monkeypatch.setattr(cart, "_navigate_async", navigate)
    monkeypatch.setattr(cart, "_open_bandcamp_cart_async", opened)
    monkeypatch.setattr(cart, "_bandcamp_cart_contains_async", contains)

    stores, warnings = asyncio.run(
        session._open_final_carts({}, {"bandcamp": [item]})
    )

    assert launches == [1]
    assert stores == ("bandcamp",)
    assert not warnings
    assert page.focused == 1


def test_final_cart_view_failure_keeps_verified_results(monkeypatch):
    """A relaunch race used to throw away a batch whose clicks had all verified."""

    item = resolved_item("bandcamp")
    plan = cart.CartPlan((item,))
    session = cart.CartBrowserSession()

    async def pages(_count=2):
        return [object(), object()]

    async def preflight(_requests, _pages, _cancel, _progress):
        return plan

    async def approve(candidate):
        return candidate

    async def execute(store, items, *_args):
        return [cart.CartResult(item.track_key, item.track_label, store, "added")]

    async def navigate(_page, _url, _store):
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(session, "_work_pages", pages)
    monkeypatch.setattr(session, "_preflight", preflight)
    monkeypatch.setattr(session, "_execute_store", execute)
    monkeypatch.setattr(cart, "_navigate_async", navigate)

    outcome = asyncio.run(
        session.run_batch((request("bandcamp"),), asyncio.Event(), approve=approve)
    )

    statuses = {(r.track_key, r.code): r.status for r in outcome.results}
    assert statuses[(item.track_key, "")] == "added"
    assert any(r.code == "cart_view_failed" for r in outcome.results)
    assert outcome.cart_stores == ()


def test_verification_stage_timeouts_fit_inside_the_outer_budget():
    assert sum(seconds for _name, seconds in cart.VERIFY_STAGES) <= cart.VERIFY_BUDGET_SECONDS


def test_slow_reload_stage_reports_its_stage_name(monkeypatch):
    item = resolved_item("bandcamp")
    monkeypatch.setattr(cart, "VERIFY_STAGES", (("count", 0.01), ("sidecart", 0.01), ("reload", 0.05)))

    async def count(_page):
        return None

    async def contains(_page, _item):
        return False

    async def slow_navigate(_page, _url, _store):
        await asyncio.sleep(1)

    monkeypatch.setattr(cart, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(cart, "_bandcamp_cart_contains_async", contains)
    monkeypatch.setattr(cart, "_navigate_async", slow_navigate)

    outcome = asyncio.run(cart._verify_bandcamp_click_async(object(), item, None))

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
        cart.save_cart_diagnostics(page, "bandcamp", page.url, "cart_unverified")
    )

    assert folder is not None and folder.parent == tmp_path / "dj-digger" / "cart-diagnostics"
    html_text = (folder / "page.html").read_text()
    assert "fan" not in html_text and "token=x" not in html_text
    assert "<script></script>" in html_text
    meta = json.loads((folder / "meta.json").read_text())
    assert "fan_id" not in meta["product_url"] and meta["code"] == "cart_unverified"
    assert (folder / "page.png").exists()


def test_diagnostics_keep_only_the_last_ten(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    root = tmp_path / "dj-digger" / "cart-diagnostics"
    root.mkdir(parents=True)
    for index in range(12):
        (root / f"2026010{index // 10}-00000{index % 10}-bandcamp-old").mkdir()

    asyncio.run(
        cart.save_cart_diagnostics(DiagnosticPage(), "bandcamp", DiagnosticPage.url, "x")
    )

    assert len([p for p in root.iterdir() if p.is_dir()]) == cart.CART_DIAGNOSTICS_KEEP


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
        return cart.VerifyOutcome(False, "reload", 1.0)

    async def no_diagnostics(*_args, **_kwargs):
        return None

    monkeypatch.setattr(cart, "_refresh_item_async", refresh)
    monkeypatch.setattr(cart, "_cart_contains_async", contains)
    monkeypatch.setattr(cart, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(cart, "_add_to_cart_async", add)
    monkeypatch.setattr(cart, "_verify_bandcamp_click_async", verify)
    monkeypatch.setattr(cart, "save_cart_diagnostics", no_diagnostics)

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

    monkeypatch.setattr(session, "_ensure_context", context)
    monkeypatch.setattr(cart, "_navigate_async", navigate)
    monkeypatch.setattr(cart, "_dismiss_bandcamp_cookie_banner", banner)
    monkeypatch.setattr(cart, "_first_visible_async", nothing_visible)
    monkeypatch.setattr(cart, "_only_visible_async", nothing_visible)
    monkeypatch.setattr(cart, "_cart_contains_async", contains)

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
        return cart.VerifyOutcome(True, "count", 0.1)

    async def count(_page):
        return 0

    monkeypatch.setattr(cart, "_refresh_item_async", refresh)
    monkeypatch.setattr(cart, "_cart_contains_async", contains)
    monkeypatch.setattr(cart, "_add_to_cart_async", add)
    monkeypatch.setattr(cart, "_bandcamp_cart_count_async", count)
    monkeypatch.setattr(cart, "_verify_bandcamp_click_async", verify)
    session = cart.CartBrowserSession()

    results = asyncio.run(
        session._execute_store(
            "bandcamp", [item], object(), cancel, None, [0], 1
        )
    )

    assert clicks == 1
    assert results[0].status == "added"


def test_profile_reset_refuses_a_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    parent = tmp_path / "dj-digger"
    parent.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (parent / "store-browser").symlink_to(elsewhere, target_is_directory=True)
    session = cart.CartBrowserSession(parent / "store-browser")

    with pytest.raises(cart.AutomationError, match="symlinked"):
        asyncio.run(session.reset_profile())


def test_profile_reset_recreates_only_the_exact_private_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    profile = tmp_path / "dj-digger" / "store-browser"
    profile.mkdir(parents=True)
    (profile / "cookie").write_text("private", encoding="utf-8")
    session = cart.CartBrowserSession(profile)

    asyncio.run(session.reset_profile())

    assert profile.is_dir()
    assert not (profile / "cookie").exists()

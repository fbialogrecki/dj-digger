import html
import json
import os
import signal
from dataclasses import replace
from decimal import Decimal
from threading import Event

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


def test_store_security_challenge_is_a_technical_failure_not_missing_product():
    html = """
        <html><head><title>Just a moment...</title></head>
        <body>Performing security verification. Ray ID: public-challenge-id</body></html>
    """

    with pytest.raises(cart.AutomationError, match="security challenge"):
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


def test_only_business_unavailability_falls_back_from_bandcamp_to_beatport():
    calls = []

    def resolve(_track, store, _url):
        calls.append(store)
        if store == "bandcamp":
            raise cart.ProductUnavailable("not sold separately")
        return resolved_item(store, price="2.49", currency="EUR")

    plan = cart.plan_requests([request("bandcamp", "beatport")], resolve)

    assert calls == ["bandcamp", "beatport"]
    assert plan.items[0].store == "beatport"


@pytest.mark.parametrize("failure", [cart.AutomationError("network"), cart.UnsafeMatch("ambiguous")])
def test_technical_or_unsafe_bandcamp_failure_never_falls_back(failure):
    calls = []

    def resolve(_track, store, _url):
        calls.append(store)
        raise failure

    plan = cart.plan_requests([request("bandcamp", "beatport")], resolve)

    assert calls == ["bandcamp"]
    assert plan.results[0].status in {"failed", "skipped"}


def test_an_unexpected_store_error_is_redacted_and_never_falls_back():
    calls = []

    def resolve(_track, store, _url):
        calls.append(store)
        raise RuntimeError("secret cookie and https://store.test/?token=secret")

    plan = cart.plan_requests([request("bandcamp", "beatport")], resolve)

    assert calls == ["bandcamp"]
    assert not plan.items
    assert plan.results[0].status == "failed"
    assert plan.results[0].reason == "unexpected store interaction failure"


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


def test_execution_revalidates_price_before_any_cart_click():
    original = resolved_item("bandcamp", price="1.25")
    changed = resolved_item("bandcamp", price="1.50")
    clicks = []

    results = cart.execute_items(
        cart.CartPlan(items=(original,)),
        refresh=lambda _item: changed,
        in_cart=lambda _item: False,
        add=lambda item: clicks.append(item),
    )

    assert clicks == []
    assert results[0].status == "skipped"
    assert "changed" in results[0].reason


def test_execution_rechecks_price_again_after_inspecting_the_cart():
    original = resolved_item("bandcamp", price="1.25")
    refreshes = iter([original, resolved_item("bandcamp", price="1.50")])
    clicks = []

    results = cart.execute_items(
        cart.CartPlan(items=(original,)),
        refresh=lambda _item: next(refreshes),
        in_cart=lambda _item: False,
        add=lambda item: clicks.append(item),
    )

    assert clicks == []
    assert results[0].status == "skipped"
    assert "changed" in results[0].reason


def test_an_existing_exact_product_is_success_without_clicking():
    item = resolved_item("beatport", price="2.49", currency="EUR")
    clicks = []

    results = cart.execute_items(
        cart.CartPlan(items=(item,)),
        refresh=lambda current: current,
        in_cart=lambda _item: True,
        add=lambda current: clicks.append(current),
    )

    assert clicks == []
    assert results[0].status == "already_in_cart"


def test_an_unverified_click_is_not_retried():
    item = resolved_item("bandcamp")
    checks = iter([False, False])
    clicks = []

    results = cart.execute_items(
        cart.CartPlan(items=(item,)),
        refresh=lambda current: current,
        in_cart=lambda _item: next(checks),
        add=lambda current: clicks.append(current),
    )

    assert clicks == [item]
    assert results[0].status == "failed"
    assert "not verified" in results[0].reason


def test_an_unverified_click_stops_further_clicks_for_that_store():
    first = resolved_item("bandcamp")
    second = replace(first, track_key="20", track_label="Artist - Second", product_id="2")
    checks = iter([False, False])
    clicks = []

    results = cart.execute_items(
        cart.CartPlan(items=(first, second)),
        refresh=lambda current: current,
        in_cart=lambda _item: next(checks),
        add=lambda current: clicks.append(current),
    )

    assert clicks == [first]
    assert [result.status for result in results] == ["failed", "failed"]
    assert "stopped" in results[1].reason


def test_store_profile_lives_in_private_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    path = cart.store_profile_path()

    assert path == tmp_path / "dj-digger" / "store-browser"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o700


class FakeBrowserContext:
    def set_default_timeout(self, _timeout):
        pass

    def close(self):
        pass


class FakeChromium:
    def __init__(self, executable_path):
        self.executable_path = str(executable_path)
        self.launched = False

    def launch_persistent_context(self, *_args, **_kwargs):
        self.launched = True
        return FakeBrowserContext()


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class FakePlaywrightManager:
    def __init__(self, chromium):
        self.playwright = FakePlaywright(chromium)

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


def test_browser_context_does_not_reclassify_errors_after_launch(tmp_path, monkeypatch):
    import playwright.sync_api

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(FakeChromium(executable)),
    )

    with pytest.raises(RuntimeError, match="downstream mentioned playwright install"):
        with cart._browser_context(tmp_path):
            raise RuntimeError("downstream mentioned playwright install")


def test_browser_context_reports_a_missing_chromium_before_launch(tmp_path, monkeypatch):
    import playwright.sync_api

    chromium = FakeChromium(tmp_path / "missing-chromium")
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(chromium),
    )

    with pytest.raises(cart.ChromiumMissing):
        with cart._browser_context(tmp_path):
            pass

    assert not chromium.launched


def test_chromium_installer_uses_the_current_python_without_a_shell(monkeypatch):
    calls = []

    class FinishedInstaller:
        returncode = 0

        def poll(self):
            return self.returncode

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return FinishedInstaller()

    monkeypatch.setattr(cart.subprocess, "Popen", popen)
    monkeypatch.setattr(
        cart.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("the cancellable installer must use Popen"),
    )

    cart.install_chromium(Event())

    popen_options = {
        "stdout": cart.subprocess.DEVNULL,
        "stderr": cart.subprocess.DEVNULL,
        "shell": False,
    }
    if os.name == "nt":
        popen_options["creationflags"] = cart.subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    assert calls == [
        (
            [cart.sys.executable, "-m", "playwright", "install", "chromium"],
            popen_options,
        )
    ]


def test_chromium_installer_terminates_its_child_when_cancelled(monkeypatch):
    cancel = Event()

    class RunningInstaller:
        returncode = None
        terminated = False
        polls = 0
        pid = 123
        signals = []

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                cancel.set()
                return None
            self.returncode = 0
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def send_signal(self, sent):
            self.signals.append(sent)
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    process = RunningInstaller()
    monkeypatch.setattr(cart.subprocess, "Popen", lambda *_args, **_kwargs: process)
    group_signals = []
    if os.name != "nt":
        def killpg(pid, sent):
            group_signals.append((pid, sent))
            process.returncode = -15

        monkeypatch.setattr(cart.os, "killpg", killpg)

    with pytest.raises(cart.AutomationError, match="cancelled"):
        cart.install_chromium(cancel)

    assert not process.terminated
    if os.name == "nt":
        assert process.signals == [signal.CTRL_BREAK_EVENT]
    else:
        assert group_signals == [(process.pid, signal.SIGTERM)]


class BrokenChromium(FakeChromium):
    def launch_persistent_context(self, *_args, **_kwargs):
        raise RuntimeError("missing shared library")


class MissingLibrariesChromium(FakeChromium):
    def launch_persistent_context(self, *_args, **_kwargs):
        raise RuntimeError("please run playwright install-deps")


def test_linux_launch_failure_explains_how_to_install_system_dependencies(
    tmp_path, monkeypatch
):
    import playwright.sync_api

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(cart.sys, "platform", "linux")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(BrokenChromium(executable)),
    )

    with pytest.raises(cart.AutomationError, match=r"install --with-deps chromium"):
        with cart._browser_context(tmp_path):
            pass


def test_install_deps_message_is_not_mistaken_for_a_missing_browser(tmp_path, monkeypatch):
    import playwright.sync_api

    executable = tmp_path / "chromium"
    executable.touch()
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setattr(cart.sys, "platform", "linux")
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: FakePlaywrightManager(MissingLibrariesChromium(executable)),
    )

    with pytest.raises(cart.AutomationError) as caught:
        with cart._browser_context(tmp_path):
            pass

    assert not isinstance(caught.value, cart.ChromiumMissing)
    assert "install --with-deps chromium" in str(caught.value)


class RedirectPage:
    def __init__(self, target):
        self.url = "about:blank"
        self.target = target
        self.calls = []

    def goto(self, url, **_kwargs):
        self.calls.append(url)
        self.url = self.target


def test_navigation_rechecks_the_final_redirect_host():
    page = RedirectPage("https://bandcamp.com.evil.test/phish")

    with pytest.raises(cart.AutomationError, match="redirected"):
        cart.navigate_store(page, "https://artist.bandcamp.com/track/a", "bandcamp")

    assert page.calls == ["https://artist.bandcamp.com/track/a"]


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


def test_explicit_bandcamp_album_only_copy_is_business_unavailability():
    page = BodyTextPage("Not available for individual purchase. Included with the album.")

    assert cart._bandcamp_individual_unavailable(page)


def test_missing_bandcamp_purchase_control_is_a_technical_selector_failure():
    with pytest.raises(cart.AutomationError, match="control changed"):
        cart._bandcamp_positive_price(NoControlsPage(), Decimal("1.25"))


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


def test_beatport_cart_id_needs_a_remove_control_not_a_recommendation_link():
    assert cart._beatport_cart_contains(BeatportCartPage(), "1")


def test_beatport_can_use_one_exact_visible_price_button():
    page = PriceButtonsPage(
        "https://www.beatport.com/track/signal/1",
        {"$1.69", "$2.49"},
    )

    chosen = cart._beatport_add_control(
        page,
        resolved_item("beatport", price="1.69", currency="USD"),
    )

    assert chosen is not None


def test_beatport_scopes_duplicate_prices_to_the_exact_track_heading():
    chosen = cart._beatport_add_control(
        ScopedPriceButtonsPage(),
        resolved_item("beatport", price="1.69", currency="USD"),
    )

    assert chosen is not None


def test_public_my_beatport_navigation_is_not_mistaken_for_a_logged_in_account():
    page = RolePage(
        "https://www.beatport.com/",
        {"Login", "My Beatport"},
    )

    assert not cart._is_logged_in(page, "beatport")


class BandcampLoginRedirectPage(RolePage):
    def __init__(self):
        super().__init__("about:blank", set())
        self.calls = []

    def goto(self, url, **_kwargs):
        self.calls.append(url)
        self.url = "https://bandcamp.com/" if url.endswith("/login") else url


def test_bandcamp_login_redirect_confirms_an_existing_session(monkeypatch):
    page = BandcampLoginRedirectPage()
    monkeypatch.setattr(cart, "LOGIN_TIMEOUT_SECONDS", 0)

    cart.ensure_login(page, "bandcamp", Event())

    assert page.calls == [cart.STORE_HOME["bandcamp"], cart.STORE_LOGIN["bandcamp"]]


class DuplicateLoginPage(RolePage):
    def __init__(self):
        super().__init__("https://bandcamp.com/", set())

    def get_by_role(self, role, *, name):
        if role == "link" and name.fullmatch("Log in"):
            return MultiLocator([True, True])
        return EmptyLocator()


def test_multiple_visible_bandcamp_login_links_fail_closed():
    assert not cart._login_complete(DuplicateLoginPage(), "bandcamp")


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


def test_bandcamp_cart_verification_is_scoped_to_the_sidecart_on_the_product_page():
    page = CartPage()
    item = resolved_item("bandcamp")

    assert not cart._cart_contains(page, item, Event())

    assert page.calls == [item.product_url]
    assert all("#sidecartContents #item_list" in selector for selector in page.selectors)
    assert any(item.product_id in selector for selector in page.selectors)


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


def test_bandcamp_cart_uses_canonical_track_url_within_a_removable_cart_row():
    assert cart._bandcamp_cart_contains(
        BandcampCartPage(),
        resolved_item("bandcamp"),
    )


def test_preflight_logs_in_only_when_a_store_is_actually_needed(monkeypatch):
    logins = []
    monkeypatch.setattr(cart, "ensure_login", lambda _page, store, _cancel: logins.append(store))
    monkeypatch.setattr(
        cart,
        "resolve_cart_item",
        lambda _page, _track, store, _url, _cancel: resolved_item(store),
    )

    plan = cart.prepare_on_page(object(), [request("bandcamp", "beatport")], Event())

    assert plan.items[0].store == "bandcamp"
    assert logins == ["bandcamp"]


def test_preflight_logs_in_to_fallback_store_after_business_unavailability(monkeypatch):
    logins = []
    monkeypatch.setattr(cart, "ensure_login", lambda _page, store, _cancel: logins.append(store))

    def resolve(_page, _track, store, _url, _cancel):
        if store == "bandcamp":
            raise cart.ProductUnavailable("not sold separately")
        return resolved_item(store, price="2.49", currency="EUR")

    monkeypatch.setattr(cart, "resolve_cart_item", resolve)

    plan = cart.prepare_on_page(object(), [request("bandcamp", "beatport")], Event())

    assert plan.items[0].store == "beatport"
    assert logins == ["bandcamp", "beatport"]

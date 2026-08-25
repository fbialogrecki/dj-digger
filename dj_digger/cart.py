"""Safe, user-initiated store cart automation."""

import json
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Event
from typing import Any, Literal
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .models import Track

STORE_HOSTS = {"bandcamp": "bandcamp.com", "beatport": "beatport.com"}
VERSION_PHRASES = (
    "original mix",
    "instrumental",
    "bootleg",
    "remix",
    "vip",
    "edit",
    "dub",
)
ARTIST_STOP_WORDS = {"and", "feat", "featuring", "ft", "the", "versus", "vs", "with"}
PROMO_TAG = re.compile(
    r"[\[(](?:premiere|free\s+(?:dl|download)|official\s+(?:audio|video)|out\s+now)[^\])]*[\])]",
    re.IGNORECASE,
)
MAX_HTML_BYTES = 2_000_000
NAVIGATION_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 15_000
LOGIN_TIMEOUT_SECONDS = 300
STORE_HOME = {
    "bandcamp": "https://bandcamp.com/",
    "beatport": "https://www.beatport.com/",
}
STORE_LOGIN = {
    "bandcamp": "https://bandcamp.com/login",
    "beatport": "https://www.beatport.com/login",
}
STORE_CART = {
    "beatport": "https://www.beatport.com/cart",
}


class ProductUnavailable(RuntimeError):
    """The linked release has no exact, individually purchasable track."""


class UnsafeMatch(RuntimeError):
    """The candidates are too ambiguous to mutate a cart safely."""


class AutomationError(RuntimeError):
    """A technical or structural failure which must never trigger store fallback."""


class ChromiumMissing(AutomationError):
    """The Playwright browser required by store carts has not been downloaded."""


@dataclass(frozen=True)
class StoreProduct:
    store: str
    url: str
    product_id: str
    title: str
    artist: str = ""
    price: Decimal | None = None
    currency: str = ""


@dataclass(frozen=True)
class CartRequest:
    track: Track
    links: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CartItem:
    track_key: str
    track_label: str
    store: str
    source_url: str
    product_url: str
    product_id: str
    product_title: str
    price: Decimal
    currency: str
    already_in_cart: bool = False


CartStatus = Literal["added", "already_in_cart", "skipped", "failed"]


def _display_text(value: str) -> str:
    return " ".join((value or "").split())


@dataclass(frozen=True)
class CartResult:
    track_key: str
    track_label: str
    store: str
    status: CartStatus
    reason: str = ""


@dataclass(frozen=True)
class CartPlan:
    items: tuple[CartItem, ...] = ()
    results: tuple[CartResult, ...] = ()

    def summary(self) -> str:
        totals: dict[str, Decimal] = defaultdict(Decimal)
        lines = ["Cart preflight", ""]
        for item in self.items:
            suffix = " — already in cart" if item.already_in_cart else ""
            lines.append(
                f"{_display_text(item.track_label)} — {item.store} — "
                f"{item.currency} {item.price:.2f}{suffix}"
            )
            if not item.already_in_cart:
                totals[item.currency] += item.price
        for result in self.results:
            lines.append(
                f"{_display_text(result.track_label)} — {result.store or 'no store'} — "
                f"{result.status}: {_display_text(result.reason)}"
            )
        if totals:
            lines.extend(["", "To add (taxes and checkout fees excluded):"])
            lines.extend(f"{currency} {amount:.2f}" for currency, amount in sorted(totals.items()))
        return "\n".join(lines)


def is_store_url(url: str, store: str) -> bool:
    """Whether *url* is an HTTPS page owned by the requested store."""

    base_host = STORE_HOSTS.get(store)
    if base_host is None:
        return False
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and (host == base_host or host.endswith("." + base_host))
    )


def redact_url(url: str) -> str:
    """A log-safe URL without credentials, query data, or fragments."""

    try:
        parsed = urlparse(url or "")
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "[invalid URL]"
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def store_profile_path() -> Path:
    """Create the private, persistent Chromium profile outside the repository."""

    data_home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    path = data_home / "dj-digger" / "store-browser"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def navigate_store(page: Any, url: str, store: str) -> None:
    """Navigate read-only once (one network retry) and validate the final origin."""

    if not is_store_url(url, store):
        raise AutomationError("refusing a non-canonical store URL")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    except Exception:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            raise AutomationError(f"could not load {store} product page") from exc
    if not is_store_url(page.url, store):
        raise AutomationError(f"{store} redirected outside its canonical HTTPS domain")


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = PROMO_TAG.sub(" ", value)
    value = value.replace("–", "-").replace("—", "-")
    return " ".join(re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).split())


def _title_variants(title: str, artist: str = "") -> set[str]:
    cleaned = PROMO_TAG.sub(" ", title or "").strip()
    variants = {_normalise(cleaned)}
    if artist:
        for separator in (" - ", " – ", " — ", " | "):
            if separator not in cleaned:
                continue
            left, right = cleaned.split(separator, 1)
            if _artist_tokens(left) & _artist_tokens(artist):
                variants.add(_normalise(right))
    return {variant for variant in variants if variant}


def _version_tokens(title: str) -> frozenset[str]:
    normalised = _normalise(title)
    return frozenset(
        phrase
        for phrase in VERSION_PHRASES
        if re.search(rf"\b{re.escape(phrase)}\b", normalised)
    )


def _base_title(title: str) -> str:
    normalised = _normalise(title)
    for phrase in VERSION_PHRASES:
        normalised = re.sub(rf"\b{re.escape(phrase)}\b", " ", normalised)
    return " ".join(normalised.split())


def _artist_tokens(artist: str) -> set[str]:
    return {
        token
        for token in _normalise(artist).split()
        if len(token) > 1 and token not in ARTIST_STOP_WORDS
    }


def _artists_compatible(source: str, candidate: str) -> bool:
    source_tokens = _artist_tokens(source)
    candidate_tokens = _artist_tokens(candidate)
    return bool(
        source_tokens
        and candidate_tokens
        and (source_tokens <= candidate_tokens or candidate_tokens <= source_tokens)
    )


def match_product(track: Track, products: list[StoreProduct]) -> StoreProduct:
    """Return the one exact product, refusing fuzzy or version-incompatible matches."""

    targets = _title_variants(track.title, track.artist)
    exact = [
        product
        for product in products
        if targets & _title_variants(product.title, product.artist)
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        source_artist = _artist_tokens(track.artist)
        same_artist = [
            product for product in exact if _normalise(product.artist) == _normalise(track.artist)
        ]
        if len(same_artist) == 1:
            return same_artist[0]
        by_artist = [
            product
            for product in exact
            if source_artist and _artists_compatible(track.artist, product.artist)
        ]
        if len(by_artist) == 1:
            return by_artist[0]
        raise UnsafeMatch("ambiguous exact product title")

    target_bases = {_base_title(variant) for variant in targets}
    version_conflicts = [
        product
        for product in products
        if _base_title(product.title) in target_bases
        and _version_tokens(product.title) != _version_tokens(track.title)
    ]
    if version_conflicts:
        raise UnsafeMatch("version qualifier does not match")
    raise ProductUnavailable("linked release has no exact track")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def purchase_price(
    minimum: Decimal, default: Decimal | None, step: Decimal | None
) -> Decimal:
    """Choose a declared positive price without probing the seller's form."""

    if minimum > 0:
        return minimum
    if default is not None and default > 0:
        return default
    if step is not None and step > 0:
        return step
    raise AutomationError("store did not declare a positive purchase price")


def _json_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_objects(child)


def _property_value(item: dict[str, Any], *names: str) -> str:
    wanted = set(names)
    properties = item.get("additionalProperty") or []
    if isinstance(properties, dict):
        properties = [properties]
    for prop in properties:
        if isinstance(prop, dict) and prop.get("name") in wanted:
            value = prop.get("value")
            if value is not None:
                return str(value)
    return ""


def _artist_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    if isinstance(value, list):
        return ", ".join(filter(None, (_artist_name(item) for item in value)))
    return ""


def _offer_values(offer: dict[str, Any]) -> tuple[Decimal | None, str]:
    specification = offer.get("priceSpecification") or {}
    minimum = specification.get("minPrice") if isinstance(specification, dict) else None
    price = _decimal(minimum if minimum is not None else offer.get("price"))
    currency = str(offer.get("priceCurrency") or "").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        currency = ""
    return price, currency


def _offer(item: dict[str, Any]) -> tuple[Decimal | None, str]:
    offers = item.get("offers") or {}
    if isinstance(offers, list):
        values = {_offer_values(offer) for offer in offers if isinstance(offer, dict)}
        return values.pop() if len(values) == 1 else (None, "")
    if not isinstance(offers, dict):
        return None, ""
    return _offer_values(offers)


def _structured_products(soup: BeautifulSoup, store: str) -> list[StoreProduct]:
    products: list[StoreProduct] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.get_text() or "")
        except (TypeError, ValueError):
            continue
        for item in _json_objects(data):
            url = str(item.get("url") or item.get("@id") or "").split("#", 1)[0]
            if not is_store_url(url, store) or "/track/" not in urlparse(url).path:
                continue
            if store == "beatport":
                product_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
                if not product_id.isdigit():
                    continue
            else:
                product_id = _property_value(item, "track_id", "item_id")
            title = str(item.get("name") or "").strip()
            if not product_id.isdigit() or not title:
                continue
            price, currency = _offer(item)
            products.append(
                StoreProduct(
                    store=store,
                    url=url,
                    product_id=product_id,
                    title=title,
                    artist=_artist_name(item.get("byArtist")),
                    price=price,
                    currency=currency,
                )
            )
    return products


def products_from_html(html: str, page_url: str, store: str) -> list[StoreProduct]:
    """Extract bounded, public product metadata from an already loaded store page."""

    if not is_store_url(page_url, store):
        raise AutomationError("store redirected outside its canonical HTTPS domain")
    if len((html or "").encode("utf-8")) > MAX_HTML_BYTES:
        raise AutomationError("store page is too large to inspect safely")

    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
    visible_text = soup.get_text(" ", strip=True).casefold()
    if (
        "just a moment" in title
        or "captcha" in title
        or "performing security verification" in visible_text
        or "verify you are human" in visible_text
    ):
        raise AutomationError("store presented a security challenge; complete it manually")
    by_id: dict[str, StoreProduct] = {}
    tralbum_minimum: Decimal | None = None
    if store == "bandcamp":
        tralbum_node = soup.find(attrs={"data-tralbum": True})
        if tralbum_node is not None:
            try:
                tralbum = json.loads(tralbum_node.get("data-tralbum") or "{}")
            except (TypeError, ValueError) as exc:
                raise AutomationError("Bandcamp product metadata is invalid") from exc
            current = tralbum.get("current") or {}
            tralbum_minimum = _decimal(current.get("minimum_price"))
            artist = str(current.get("artist") or "")
            for item in tralbum.get("trackinfo") or []:
                product_id = str(item.get("track_id") or item.get("id") or "")
                url = urljoin(page_url, str(item.get("title_link") or ""))
                title = str(item.get("title") or "").strip()
                if product_id.isdigit() and title and is_store_url(url, store) and "/track/" in urlparse(url).path:
                    by_id[product_id] = StoreProduct(store, url, product_id, title, artist)

    for product in _structured_products(soup, store):
        earlier = by_id.get(product.product_id)
        if earlier is not None:
            product = StoreProduct(
                store=store,
                url=product.url or earlier.url,
                product_id=product.product_id,
                title=product.title or earlier.title,
                artist=product.artist or earlier.artist,
                price=product.price,
                currency=product.currency,
            )
        by_id[product.product_id] = product

    if store == "beatport":
        for anchor in soup.find_all("a", href=True):
            url = urljoin(page_url, str(anchor.get("href") or "")).split("#", 1)[0]
            if not is_store_url(url, store) or "/track/" not in urlparse(url).path:
                continue
            product_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            title = str(
                anchor.get("aria-label") or anchor.get("title") or anchor.get_text(" ", strip=True)
            ).strip()
            if product_id.isdigit() and title and product_id not in by_id:
                by_id[product_id] = StoreProduct(store, url, product_id, title)

    products = list(by_id.values())
    if store == "bandcamp" and "/track/" in urlparse(page_url).path and tralbum_minimum is not None:
        current = next((item for item in products if urlparse(item.url).path == urlparse(page_url).path), None)
        if current is not None and current.price is not None and current.price != tralbum_minimum:
            raise AutomationError("Bandcamp price metadata disagrees with the product page")
    return products


def plan_requests(
    requests: Iterable[CartRequest], resolve: Callable[[Track, str, str], CartItem]
) -> CartPlan:
    """Resolve requests in preference order, allowing only business fallback."""

    items: list[CartItem] = []
    results: list[CartResult] = []
    broken_stores: set[str] = set()
    for request in requests:
        track_label = _display_text(request.track.label)
        unavailable: list[str] = []
        for store, url in request.links:
            if store in broken_stores:
                results.append(
                    CartResult(
                        request.track.key,
                        track_label,
                        store,
                        "failed",
                        "store automation stopped after an earlier structural failure",
                    )
                )
                break
            try:
                item = resolve(request.track, store, url)
            except ProductUnavailable as exc:
                unavailable.append(str(exc))
                continue
            except UnsafeMatch as exc:
                results.append(
                    CartResult(request.track.key, track_label, store, "skipped", str(exc))
                )
                break
            except AutomationError as exc:
                broken_stores.add(store)
                results.append(
                    CartResult(request.track.key, track_label, store, "failed", str(exc))
                )
                break
            except Exception:
                broken_stores.add(store)
                results.append(
                    CartResult(
                        request.track.key,
                        track_label,
                        store,
                        "failed",
                        "unexpected store interaction failure",
                    )
                )
                break
            items.append(item)
            break
        else:
            results.append(
                CartResult(
                    request.track.key,
                    track_label,
                    request.links[-1][0] if request.links else "",
                    "skipped",
                    unavailable[-1] if unavailable else "no eligible Bandcamp or Beatport link",
                )
            )
    return CartPlan(tuple(items), tuple(results))


def _same_snapshot(expected: CartItem, current: CartItem) -> bool:
    return (
        expected.store == current.store
        and expected.product_id == current.product_id
        and _normalise(expected.product_title) == _normalise(current.product_title)
        and expected.price == current.price
        and expected.currency == current.currency
    )


def execute_items(
    plan: CartPlan,
    *,
    refresh: Callable[[CartItem], CartItem],
    in_cart: Callable[[CartItem], bool],
    add: Callable[[CartItem], None],
) -> tuple[CartResult, ...]:
    """Execute a preflight snapshot once, verifying identity before and after mutation."""

    results = list(plan.results)
    broken_stores: set[str] = set()
    for item in plan.items:
        if item.store in broken_stores:
            results.append(
                CartResult(
                    item.track_key,
                    item.track_label,
                    item.store,
                    "failed",
                    "store automation stopped after an earlier structural failure",
                )
            )
            continue
        try:
            current = refresh(item)
            if not _same_snapshot(item, current):
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        item.store,
                        "skipped",
                        "product identity or price changed after preflight",
                    )
                )
                continue
            if in_cart(current):
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        item.store,
                        "already_in_cart",
                    )
                )
                continue
            ready = refresh(item)
            if not _same_snapshot(item, ready):
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        item.store,
                        "skipped",
                        "product identity or price changed after cart inspection",
                    )
                )
                continue
            add(ready)
            if in_cart(ready):
                results.append(
                    CartResult(item.track_key, item.track_label, item.store, "added")
                )
            else:
                broken_stores.add(item.store)
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        item.store,
                        "failed",
                        "cart click was not verified; it was not retried",
                    )
                )
        except AutomationError as exc:
            broken_stores.add(item.store)
            results.append(
                CartResult(item.track_key, item.track_label, item.store, "failed", str(exc))
            )
        except Exception:
            broken_stores.add(item.store)
            results.append(
                CartResult(
                    item.track_key,
                    item.track_label,
                    item.store,
                    "failed",
                    "unexpected store interaction failure",
                )
            )
    return tuple(results)


def _cancelled(cancel: Event) -> None:
    if cancel.is_set():
        raise AutomationError("cart operation was cancelled")


def _only_visible(locator: Any) -> Any | None:
    try:
        matches = [locator.nth(index) for index in range(locator.count())]
        visible = [match for match in matches if match.is_visible()]
        return visible[0] if len(visible) == 1 else None
    except Exception:
        return None


def _first_visible(*locators: Any) -> Any | None:
    for locator in locators:
        visible = _only_visible(locator)
        if visible is not None:
            return visible
    return None


def _login_visible(page: Any) -> bool:
    login_name = re.compile(r"^(?:log ?in|sign in)$", re.IGNORECASE)
    try:
        for locator in (
            page.get_by_role("link", name=login_name),
            page.get_by_role("button", name=login_name),
        ):
            for index in range(locator.count()):
                if locator.nth(index).is_visible():
                    return True
    except Exception:
        return True
    return False


def _is_logged_in(page: Any, store: str) -> bool:
    if not is_store_url(page.url, store):
        return False
    if _login_visible(page):
        return False
    if store == "bandcamp":
        names = re.compile(r"^(collection|wishlist|log out)$", re.IGNORECASE)
    else:
        names = re.compile(
            r"^(?:account(?: settings)?|profile|log ?out)$", re.IGNORECASE
        )
    return _first_visible(
        page.get_by_role("link", name=names),
        page.get_by_role("button", name=names),
    ) is not None


def _login_complete(page: Any, store: str) -> bool:
    if _is_logged_in(page, store):
        return True
    return (
        store == "bandcamp"
        and urlparse(page.url).path in ("", "/")
        and not _login_visible(page)
    )


def ensure_login(page: Any, store: str, cancel: Event) -> None:
    """Wait for a user-driven login without reading or filling credentials."""

    _cancelled(cancel)
    navigate_store(page, STORE_HOME[store], store)
    if _is_logged_in(page, store):
        return
    navigate_store(page, STORE_LOGIN[store], store)
    if _login_complete(page, store):
        return
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        _cancelled(cancel)
        if page.is_closed():
            raise AutomationError(f"{store} login window was closed")
        # The user may temporarily visit an external SSO origin. We do not inspect
        # or touch it; only the canonical store page can complete this wait.
        if is_store_url(page.url, store) and _login_complete(page, store):
            return
        cancel.wait(0.25)
    raise AutomationError(f"timed out waiting for manual {store} login")


def _page_products(page: Any, store: str) -> list[StoreProduct]:
    if not is_store_url(page.url, store):
        raise AutomationError(f"{store} page left its canonical domain")
    try:
        content = page.content()
    except Exception as exc:
        raise AutomationError(f"could not inspect {store} product page") from exc
    products = products_from_html(content, page.url, store)
    if not products:
        raise AutomationError(f"{store} product structure changed or is unavailable")
    return products


def _buy_digital_control(page: Any) -> Any:
    name = re.compile(r"^buy digital track$", re.IGNORECASE)
    control = _first_visible(
        page.get_by_role("button", name=name),
        page.get_by_role("link", name=name),
        page.get_by_text(name, exact=True),
    )
    if control is None:
        raise AutomationError("Bandcamp purchase control changed or is unavailable")
    return control


def _bandcamp_individual_unavailable(page: Any) -> bool:
    pattern = re.compile(
        r"not available for individual purchase|"
        r"(?:only\s+)?available\s+(?:only\s+)?with\s+purchase\s+of\s+"
        r"(?:the\s+)?(?:(?:entire|whole|full)\s+)?album",
        re.IGNORECASE,
    )
    try:
        text = page.locator("body").inner_text(timeout=ACTION_TIMEOUT_MS)
    except Exception as exc:
        raise AutomationError("could not verify Bandcamp purchase availability") from exc
    return bool(pattern.search(text))


def _bandcamp_positive_price(page: Any, minimum: Decimal) -> Decimal:
    buy_control = _buy_digital_control(page)
    if minimum > 0:
        return minimum
    buy_control.click(timeout=ACTION_TIMEOUT_MS)
    price_input = _only_visible(page.locator('input[name="userPrice"]'))
    if price_input is None:
        raise AutomationError("Bandcamp did not expose a price field for name-your-price")
    default = _decimal(price_input.input_value())
    step = _decimal(price_input.get_attribute("step"))
    return purchase_price(minimum, default, step)


def _product_by_id(products: Iterable[StoreProduct], product_id: str) -> StoreProduct | None:
    return next((product for product in products if product.product_id == product_id), None)


def _beatport_cart_contains(page: Any, product_id: str) -> bool:
    anchors = page.locator(
        f'a[href*="/track/"][href$="/{product_id}"], '
        f'a[href*="/track/"][href$="/{product_id}/"]'
    )
    remove_name = re.compile(r"^remove(?: track)?(?: from cart)?$", re.IGNORECASE)
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        if not anchor.is_visible():
            continue
        region = anchor.locator("xpath=ancestor::*[.//button][1]")
        if _first_visible(region.get_by_role("button", name=remove_name)) is not None:
            return True
    return False


def _bandcamp_cart_contains(page: Any, item: CartItem) -> bool:
    product_id = item.product_id
    by_id = page.locator(
        f'#sidecartContents #item_list [data-item-id="{product_id}"], '
        f'#sidecartContents #item_list [data-track-id="{product_id}"]'
    )
    if by_id.count() > 0:
        return True

    expected_path = urlparse(item.product_url).path
    anchors = page.locator("#sidecartContents #item_list a[href]")
    remove_name = re.compile(r"^remove$", re.IGNORECASE)
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        if not anchor.is_visible():
            continue
        url = urljoin(item.product_url, anchor.get_attribute("href") or "")
        if not is_store_url(url, "bandcamp") or urlparse(url).path != expected_path:
            continue
        region = anchor.locator("xpath=ancestor::*[.//a][1]")
        if _first_visible(region.get_by_role("link", name=remove_name)) is not None:
            return True
    return False


def _cart_contains(page: Any, item: CartItem, cancel: Event) -> bool:
    _cancelled(cancel)
    destination = item.product_url if item.store == "bandcamp" else STORE_CART[item.store]
    navigate_store(page, destination, item.store)
    product_id = item.product_id
    if not product_id.isdigit():
        raise AutomationError(f"{item.store} product has no stable numeric ID")
    if item.store == "beatport":
        try:
            return _beatport_cart_contains(page, product_id)
        except Exception as exc:
            raise AutomationError("could not verify the beatport cart") from exc
    else:
        try:
            return _bandcamp_cart_contains(page, item)
        except Exception as exc:
            raise AutomationError("could not verify the bandcamp cart") from exc


def resolve_cart_item(
    page: Any, track: Track, store: str, source_url: str, cancel: Event | None = None
) -> CartItem:
    """Resolve one linked release to an exact priced product and inspect the cart."""

    navigate_store(page, source_url, store)
    chosen = match_product(track, _page_products(page, store))
    if urlparse(page.url).path != urlparse(chosen.url).path or chosen.price is None:
        navigate_store(page, chosen.url, store)
        chosen = _product_by_id(_page_products(page, store), chosen.product_id) or chosen
    if chosen.price is None or not chosen.currency:
        if store == "bandcamp" and _bandcamp_individual_unavailable(page):
            raise ProductUnavailable("exact Bandcamp track is not sold individually")
        raise AutomationError(f"{store} did not expose a verifiable price and currency")
    price = (
        _bandcamp_positive_price(page, chosen.price)
        if store == "bandcamp"
        else purchase_price(chosen.price, None, None)
    )
    unresolved = CartItem(
        track_key=track.key,
        track_label=_display_text(track.label),
        store=store,
        source_url=source_url,
        product_url=chosen.url,
        product_id=chosen.product_id,
        product_title=chosen.title,
        price=price,
        currency=chosen.currency,
    )
    return replace(unresolved, already_in_cart=_cart_contains(page, unresolved, cancel or Event()))


def prepare_on_page(page: Any, requests: Iterable[CartRequest], cancel: Event) -> CartPlan:
    logged_in: set[str] = set()

    def resolve(track: Track, store: str, url: str) -> CartItem:
        _cancelled(cancel)
        if store not in logged_in:
            ensure_login(page, store, cancel)
            logged_in.add(store)
        return resolve_cart_item(page, track, store, url, cancel)

    return plan_requests(requests, resolve)


def _refresh_item(page: Any, expected: CartItem, cancel: Event) -> CartItem:
    _cancelled(cancel)
    navigate_store(page, expected.product_url, expected.store)
    product = _product_by_id(_page_products(page, expected.store), expected.product_id)
    if product is None or product.price is None or not product.currency:
        raise AutomationError(f"{expected.store} product can no longer be verified")
    price = (
        _bandcamp_positive_price(page, product.price)
        if expected.store == "bandcamp"
        else purchase_price(product.price, None, None)
    )
    return CartItem(
        track_key=expected.track_key,
        track_label=expected.track_label,
        store=expected.store,
        source_url=expected.source_url,
        product_url=product.url,
        product_id=product.product_id,
        product_title=product.title,
        price=price,
        currency=product.currency,
        already_in_cart=expected.already_in_cart,
    )


def _beatport_add_control(page: Any, item: CartItem) -> Any | None:
    named = re.compile(r"^add(?: track)? to cart$", re.IGNORECASE)
    control = _first_visible(
        page.get_by_role("button", name=named),
        page.get_by_text(named, exact=True),
    )
    if control is not None:
        return control
    amount = format(item.price, "f")
    if "." in amount:
        whole, fraction = amount.split(".", 1)
        amount_pattern = rf".*(?<!\d){re.escape(whole)}[.,]{re.escape(fraction)}(?!\d).*"
    else:
        amount_pattern = rf".*(?<!\d){re.escape(amount)}(?!\d).*"
    price_name = re.compile(amount_pattern, re.IGNORECASE)
    control = _first_visible(
        page.get_by_role("button", name=price_name)
    )
    if control is not None:
        return control
    heading = _first_visible(
        page.get_by_role(
            "heading", name=item.product_title, exact=True, level=1
        )
    )
    if heading is None:
        return None
    try:
        product_region = heading.locator("xpath=ancestor::*[.//button][1]")
        return _first_visible(
            product_region.get_by_role("button", name=price_name)
        )
    except Exception:
        return None


def _add_to_cart(page: Any, item: CartItem, cancel: Event) -> None:
    _cancelled(cancel)
    if not is_store_url(page.url, item.store) or urlparse(page.url).path != urlparse(
        item.product_url
    ).path:
        raise AutomationError(f"{item.store} product page changed before the cart click")
    if item.store == "bandcamp":
        price_input = _only_visible(page.locator('input[name="userPrice"]'))
        if price_input is None:
            buy_control = _buy_digital_control(page)
            buy_control.click(timeout=ACTION_TIMEOUT_MS)
            price_input = _only_visible(page.locator('input[name="userPrice"]'))
        if price_input is not None:
            price_input.fill(format(item.price, "f"), timeout=ACTION_TIMEOUT_MS)
        add_button = _first_visible(
            page.get_by_role("button", name=re.compile(r"^add to cart$", re.IGNORECASE)),
            page.get_by_text(re.compile(r"^add to cart$", re.IGNORECASE), exact=True),
        )
    else:
        add_button = _beatport_add_control(page, item)
    if add_button is None:
        raise AutomationError(f"{item.store} add-to-cart control changed or is unavailable")
    _cancelled(cancel)
    add_button.click(timeout=ACTION_TIMEOUT_MS)


def install_chromium(cancel: Event) -> None:
    """Download Playwright's matching Chromium build in the current environment."""

    _cancelled(cancel)
    popen_options = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            **popen_options,
        )
    except OSError as exc:
        raise AutomationError(
            "could not start Chromium installation; run "
            f"'{sys.executable} -m playwright install chromium'"
        ) from exc
    while process.poll() is None:
        if not cancel.wait(0.1):
            continue
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except OSError:
            pass
        _cancelled(cancel)
    _cancelled(cancel)
    if process.returncode:
        raise AutomationError(
            "Chromium installation failed; run "
            f"'{sys.executable} -m playwright install chromium'"
        )


@contextmanager
def _browser_context(profile: Path | None = None, *, accept_downloads: bool = False):
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        raise AutomationError("store cart needs a desktop display (on WSL, enable WSLg)")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AutomationError(
            "the required Playwright dependency is missing; reinstall dj-soundcloud-digger"
        ) from exc

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            raise ChromiumMissing("Chromium is required for store carts")
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile or store_profile_path()),
                headless=False,
                locale="en-US",
                accept_downloads=accept_downloads,
                chromium_sandbox=True,
            )
        except Exception as exc:
            message = str(exc).lower()
            if "executable doesn't exist" in message:
                raise ChromiumMissing("Chromium is required for store carts") from exc
            elif "singleton" in message or "user data directory is already in use" in message:
                detail = "the dedicated store browser profile is already open in another process"
            else:
                detail = "could not start the dedicated store browser"
                if sys.platform.startswith("linux"):
                    detail += (
                        "; install required system libraries with "
                        f"'{sys.executable} -m playwright install --with-deps chromium'"
                    )
            raise AutomationError(detail) from exc
        context.set_default_timeout(ACTION_TIMEOUT_MS)
        try:
            yield context
        finally:
            try:
                context.close()
            except Exception:
                pass


def prepare_cart(
    requests: Iterable[CartRequest], cancel: Event, *, profile: Path | None = None
) -> CartPlan:
    with _browser_context(profile) as context:
        page = context.pages[0] if context.pages else context.new_page()
        return prepare_on_page(page, requests, cancel)


def _wait_with_carts_open(context: Any, targets: dict[str, str], cancel: Event) -> None:
    pages = []
    for store, product_url in sorted(targets.items()):
        try:
            page = context.new_page()
            pages.append(page)
            destination = product_url if store == "bandcamp" else STORE_CART[store]
            navigate_store(page, destination, store)
            if store == "bandcamp":
                cart_control = _only_visible(page.locator("#menubar-cart-icon"))
                if cart_control is not None:
                    cart_control.click(timeout=ACTION_TIMEOUT_MS)
        except Exception:
            # Cart mutation is already verified. A changed checkout shortcut must
            # not turn successful item results into an ambiguous global failure.
            continue
    while not cancel.wait(0.25):
        try:
            any_open = any(not page.is_closed() for page in pages)
        except Exception:
            return
        if not any_open:
            return


def execute_cart(
    plan: CartPlan,
    cancel: Event,
    *,
    profile: Path | None = None,
) -> tuple[CartResult, ...]:
    with _browser_context(profile) as context:
        page = context.pages[0] if context.pages else context.new_page()
        logged_in: set[str] = set()

        def login(store: str) -> None:
            if store not in logged_in:
                ensure_login(page, store, cancel)
                logged_in.add(store)

        def refresh(item: CartItem) -> CartItem:
            login(item.store)
            return _refresh_item(page, item, cancel)

        def in_cart(item: CartItem) -> bool:
            _cancelled(cancel)
            return _cart_contains(page, item, cancel)

        def add(item: CartItem) -> None:
            _add_to_cart(page, item, cancel)

        results = execute_items(plan, refresh=refresh, in_cart=in_cart, add=add)
        successful_items = {
            (result.track_key, result.store)
            for result in results
            if result.status in {"added", "already_in_cart"} and result.store in STORE_HOSTS
        }
        cart_targets: dict[str, str] = {}
        for item in plan.items:
            if (item.track_key, item.store) in successful_items:
                cart_targets.setdefault(item.store, item.product_url)
        if cart_targets and not cancel.is_set():
            _wait_with_carts_open(context, cart_targets, cancel)
        return results

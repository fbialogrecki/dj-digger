"""Safe, user-initiated store cart automation."""

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Event
from typing import Any, Literal
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .links import redact_url
from .models import Track
from .paths import data_dir

LOGGER = logging.getLogger(__name__)
LOG_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
LOG_SECRET = re.compile(
    r"\b([a-z0-9_-]*(?:token|password|authorization|cookie|session)[a-z0-9_-]*)"
    r"\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)

STORE_HOSTS = {"bandcamp": "bandcamp.com", "beatport": "beatport.com"}
VERSION_PHRASES = (
    "original mix",
    "instrumental",
    "bootleg",
    "remix",
    "vip",
    "edit",
    "dub",
    "cut",
)
ARTIST_STOP_WORDS = {"and", "feat", "featuring", "ft", "the", "versus", "vs", "with"}
PROMO_TAG = re.compile(
    r"[\[(](?:premiere|free\s+(?:dl|download)|official\s+(?:audio|video)|out\s+now)[^\])]*[\])]",
    re.IGNORECASE,
)
PROMO_PREFIX = re.compile(
    r"^\s*(?:premiere|free\s+(?:dl|download)|official\s+(?:audio|video))\s*[:\-]\s*",
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
    "beatport": "https://account.beatport.com/",
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


class StoreStructureError(AutomationError):
    """A store page no longer exposes the bounded identity/control contract."""


class BrowserNavigationError(AutomationError):
    """A validated store page could not be loaded after the bounded retry."""


class CartUnverified(AutomationError):
    """A cart click may have happened and must never be repeated automatically."""


class UnsafeRedirect(AutomationError):
    """A store navigation escaped the canonical HTTPS boundary."""


class CartCancelled(AutomationError):
    """The user stopped a cart operation before its next mutation."""


class UserActionTimeout(AutomationError):
    """A manual login or challenge was not completed before its deadline."""


class SecurityChallengeBlocked(AutomationError):
    """A production anti-bot challenge refuses the automated browser."""


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
    minimum_price: Decimal | None = None
    suggested_price: Decimal | None = None
    price_step: Decimal | None = None
    price_editable: bool = False


CartStatus = Literal[
    "added",
    "already_in_cart",
    "playlist_ready",
    "skipped",
    "failed",
]
CartResultCode = Literal[
    "",
    "unavailable",
    "unsafe_match",
    "price_changed",
    "store_structure",
    "user_action_timeout",
    "browser_failure",
    "cart_unverified",
    "cancelled",
    "unsafe_redirect",
    "cart_view_incomplete",
    "not_selected",
    "playlist_ready",
]


def _display_text(value: str) -> str:
    return " ".join((value or "").split())


def log_safe_text(value: object) -> str:
    """Bound an external diagnostic and remove URL queries and obvious secrets."""

    text = _display_text(str(value))
    text = LOG_URL.sub(lambda match: redact_url(match.group(0)), text)
    text = LOG_SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return text[:1000]


@dataclass(frozen=True)
class CartResult:
    track_key: str
    track_label: str
    store: str
    status: CartStatus
    reason: str = ""
    code: CartResultCode = ""
    url: str = ""

    @property
    def retryable(self) -> bool:
        return self.code in {
            "price_changed",
            "user_action_timeout",
            "browser_failure",
            "cancelled",
        }


def _beatport_playlist_result(
    request: CartRequest,
    label: str,
    reason: str,
    url: str = "",
) -> CartResult:
    """Keep a Beatport request useful when read-only product lookup is blocked."""

    return CartResult(
        request.track.key,
        label,
        "beatport",
        "playlist_ready",
        reason,
        "playlist_ready",
        canonical_store_url(url, "beatport") or "",
    )


@dataclass(frozen=True)
class PriceQuote:
    currency: str
    minimum: Decimal
    selected: Decimal
    suggested: Decimal | None = None
    step: Decimal | None = None
    editable: bool = False


CartPhase = Literal["starting", "login", "preflight", "approval", "adding", "ready"]


@dataclass(frozen=True)
class CartProgress:
    phase: CartPhase
    completed: int
    total: int
    store: str = ""
    track_label: str = ""
    message: str = ""


@dataclass(frozen=True)
class CartBatchOutcome:
    results: tuple[CartResult, ...]
    cart_stores: tuple[str, ...] = ()
    cancelled: bool = False

    @property
    def beatport_playlist_ready(self) -> bool:
        return any(
            result.store == "beatport"
            and result.code == "playlist_ready"
            for result in self.results
        )

    @property
    def retryable_keys(self) -> frozenset[str]:
        return frozenset(result.track_key for result in self.results if result.retryable)

    @property
    def retryable_targets(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (result.track_key, result.store)
            for result in self.results
            if result.retryable
        )


@dataclass(frozen=True)
class CartPlan:
    items: tuple[CartItem, ...] = ()
    results: tuple[CartResult, ...] = ()

    def summary(self) -> str:
        totals: dict[str, Decimal] = defaultdict(Decimal)
        lines = ["Purchase preflight", ""]
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
            lines.extend(["", "Selected estimate (taxes and checkout fees excluded):"])
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


def canonical_store_url(url: str, store: str) -> str | None:
    """Return a validated HTTPS store URL, upgrading only a plain HTTP origin."""

    value = (url or "").strip()
    if is_store_url(value, store):
        return value
    base_host = STORE_HOSTS.get(store)
    if base_host is None:
        return None
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if not (
        parsed.scheme.lower() == "http"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 80)
        and (host == base_host or host.endswith("." + base_host))
    ):
        return None
    return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))


def store_profile_path() -> Path:
    """Create the private, persistent Chromium profile outside the repository."""

    path = data_dir() / "store-browser"
    # mkdir's mode is masked by the umask and ignored when the directory already
    # exists, so the explicit chmod is what actually guarantees 0700.
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def navigate_store(page: Any, url: str, store: str) -> None:
    """Navigate read-only once (one network retry) and validate the final origin."""

    destination = canonical_store_url(url, store)
    if destination is None:
        raise AutomationError("refusing a non-canonical store URL")
    try:
        page.goto(destination, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    except Exception:
        try:
            page.goto(destination, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        except Exception as exc:
            raise AutomationError(f"could not load {store} product page") from exc
    if not is_store_url(page.url, store):
        raise AutomationError(f"{store} redirected outside its canonical HTTPS domain")


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = PROMO_TAG.sub(" ", value)
    value = re.sub(r"\b(?:feat(?:uring)?|ft)\.?\b", "ft", value)
    value = value.replace("–", "-").replace("—", "-")
    return " ".join(re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).split())


def _without_nonversion_context(title: str) -> str:
    return re.sub(
        r"\[([^\]]+)\]",
        lambda match: (
            match.group(0)
            if any(phrase in _normalise(match.group(1)) for phrase in VERSION_PHRASES)
            else " "
        ),
        title or "",
    )


def _title_variants(title: str, artist: str = "") -> set[str]:
    cleaned = PROMO_PREFIX.sub("", PROMO_TAG.sub(" ", title or "")).strip()
    variants = {_normalise(cleaned)}
    without_context = _without_nonversion_context(cleaned)
    variants.add(_normalise(without_context))
    for quoted in re.findall(r"[\"'‘’“”]([^\"'‘’“”]{4,})[\"'‘’“”]", cleaned):
        variants.add(_normalise(quoted))
    for segment in re.split(r"\s+//\s+", cleaned):
        normalised = _normalise(segment)
        if len(normalised) >= 4 and not re.fullmatch(r"[a-z]{1,8}\d{2,}", normalised):
            variants.add(normalised)
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


def _without_version_context(title: str) -> str:
    return re.sub(
        r"[\[(]([^\])]+)[\])]",
        lambda match: " " if _version_tokens(match.group(1)) else match.group(0),
        title or "",
    )


def _base_title(title: str) -> str:
    normalised = _normalise(title)
    for phrase in VERSION_PHRASES:
        normalised = re.sub(rf"\b{re.escape(phrase)}\b", " ", normalised)
    return " ".join(normalised.split())


def _trailing_title(title: str) -> tuple[str, bool]:
    """A release title after a possible artist prefix, without fuzzy matching."""

    cleaned = _without_nonversion_context(PROMO_TAG.sub(" ", title or "")).strip()
    for separator in (" - ", " – ", " — ", " | "):
        if separator in cleaned:
            return _normalise(cleaned.rsplit(separator, 1)[1]), True
    return _normalise(cleaned), False


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


def _product_title_variants(product: StoreProduct) -> set[str]:
    variants = _title_variants(product.title, product.artist)
    if product.store == "beatport" and not _version_tokens(product.title):
        parts = [part for part in urlparse(product.url).path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "track":
            variants.update(_title_variants(parts[1].replace("-", " ")))
    return variants


def match_product(track: Track, products: list[StoreProduct]) -> StoreProduct:
    """Return the one exact product, refusing fuzzy or version-incompatible matches."""

    targets = _title_variants(track.title, track.artist)
    exact = [
        product
        for product in products
        if targets & _product_title_variants(product)
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

    # Promo uploaders and labels often name the same recording with different
    # artist aliases ("Phil:osophy - Remember" vs "Philth Tangent - Remember").
    # The linked release still gives us a safe exact fallback when one and only
    # one product has the same complete trailing title and version qualifier.
    target_tail, target_stripped = _trailing_title(track.title)
    trailing = [
        product
        for product in products
        if len(target_tail) >= 4
        and _trailing_title(product.title)[0] == target_tail
        and (target_stripped or _trailing_title(product.title)[1])
        and _version_tokens(product.title) == _version_tokens(track.title)
    ]
    if len(trailing) == 1:
        return trailing[0]
    if len(trailing) > 1:
        raise UnsafeMatch("ambiguous exact trailing product title")
    target_version_core = _trailing_title(_without_version_context(track.title))[0]
    version_conflicts = [
        product
        for product in products
        if len(target_tail) >= 4
        and _trailing_title(_without_version_context(product.title))[0]
        == target_version_core
        and _version_tokens(product.title) != _version_tokens(track.title)
    ]
    if version_conflicts:
        raise UnsafeMatch("version qualifier does not match")

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
                product_id = _beatport_track_id(url)
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
        or "client challenge" in title
        or "captcha" in title
        or "performing security verification" in visible_text
        or "verify you are human" in visible_text
        or "cf-turnstile" in (html or "").casefold()
        or "/_fs-ch-" in (html or "").casefold()
    ):
        raise SecurityChallengeBlocked(
            "store security verification does not support an automated browser"
        )
    by_id: dict[str, StoreProduct] = {}
    tralbum_minimum: Decimal | None = None
    if store == "bandcamp":
        tralbum_products, tralbum_minimum = _bandcamp_tralbum_products(soup, page_url)
        by_id.update(tralbum_products)

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
        for product_id, product in _beatport_anchor_products(soup, page_url).items():
            by_id.setdefault(product_id, product)

    products = list(by_id.values())
    if store == "bandcamp" and "/track/" in urlparse(page_url).path and tralbum_minimum is not None:
        current = next((item for item in products if urlparse(item.url).path == urlparse(page_url).path), None)
        if current is not None and current.price is not None and current.price != tralbum_minimum:
            raise AutomationError("Bandcamp price metadata disagrees with the product page")
    return products


def _bandcamp_tralbum_products(
    soup: Any, page_url: str
) -> tuple[dict[str, StoreProduct], Decimal | None]:
    """Products and the page minimum price out of Bandcamp's data-tralbum blob."""

    by_id: dict[str, StoreProduct] = {}
    tralbum_node = soup.find(attrs={"data-tralbum": True})
    if tralbum_node is None:
        return by_id, None
    try:
        tralbum = json.loads(tralbum_node.get("data-tralbum") or "{}")
    except (TypeError, ValueError) as exc:
        raise AutomationError("Bandcamp product metadata is invalid") from exc
    current = tralbum.get("current") or {}
    artist = str(current.get("artist") or "")
    for item in tralbum.get("trackinfo") or []:
        product_id = str(item.get("track_id") or item.get("id") or "")
        url = urljoin(page_url, str(item.get("title_link") or ""))
        title = str(item.get("title") or "").strip()
        if product_id.isdigit() and title and is_store_url(url, "bandcamp") and "/track/" in urlparse(url).path:
            by_id[product_id] = StoreProduct("bandcamp", url, product_id, title, artist)
    return by_id, _decimal(current.get("minimum_price"))


def _beatport_anchor_products(soup: Any, page_url: str) -> dict[str, StoreProduct]:
    """Track products scraped straight off Beatport anchors."""

    by_id: dict[str, StoreProduct] = {}
    for anchor in soup.find_all("a", href=True):
        url = urljoin(page_url, str(anchor.get("href") or "")).split("#", 1)[0]
        if not is_store_url(url, "beatport") or "/track/" not in urlparse(url).path:
            continue
        product_id = _beatport_track_id(url)
        title = str(
            anchor.get("aria-label") or anchor.get("title") or anchor.get_text(" ", strip=True)
        ).strip()
        if product_id.isdigit() and title and product_id not in by_id:
            by_id[product_id] = StoreProduct("beatport", url, product_id, title)
    return by_id


def plan_requests(
    requests: Iterable[CartRequest], resolve: Callable[[Track, str, str], CartItem]
) -> CartPlan:
    """Resolve requests in preference order, allowing only business fallback."""

    items: list[CartItem] = []
    results: list[CartResult] = []
    broken_stores: set[str] = set()
    for request in requests:
        track_label = _display_text(request.track.label)

        def record(store: str, status: str, reason: str = "") -> None:
            results.append(
                CartResult(request.track.key, track_label, store, status, reason)
            )

        unavailable: list[str] = []
        for store, url in request.links:
            if store in broken_stores:
                record(store, "failed", "store automation stopped after an earlier structural failure")
                break
            try:
                item = resolve(request.track, store, url)
            except ProductUnavailable as exc:
                unavailable.append(str(exc))
                continue
            except UnsafeMatch as exc:
                record(store, "skipped", str(exc))
                break
            except AutomationError as exc:
                broken_stores.add(store)
                record(store, "failed", str(exc))
                break
            except Exception:
                broken_stores.add(store)
                record(store, "failed", "unexpected store interaction failure")
                break
            items.append(item)
            break
        else:
            record(
                request.links[-1][0] if request.links else "",
                "skipped",
                unavailable[-1] if unavailable else "no eligible Bandcamp or Beatport link",
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

        def record(status: str, reason: str = "") -> None:
            results.append(
                CartResult(item.track_key, item.track_label, item.store, status, reason)
            )

        if item.store in broken_stores:
            record("failed", "store automation stopped after an earlier structural failure")
            continue
        try:
            current = refresh(item)
            if not _same_snapshot(item, current):
                record("skipped", "product identity or price changed after preflight")
                continue
            if in_cart(current):
                record("already_in_cart")
                continue
            # A second refresh, not paranoia: the cart check navigated the page
            # away to the cart, so the product page must be reloaded before the
            # add click has anything to land on.
            ready = refresh(item)
            if not _same_snapshot(item, ready):
                record("skipped", "product identity or price changed after cart inspection")
                continue
            add(ready)
            if in_cart(ready):
                record("added")
            else:
                broken_stores.add(item.store)
                record("failed", "cart click was not verified; it was not retried")
        except AutomationError as exc:
            broken_stores.add(item.store)
            record("failed", str(exc))
        except Exception:
            broken_stores.add(item.store)
            record("failed", "unexpected store interaction failure")
    return tuple(results)


def _cancelled(cancel: Event) -> None:
    if cancel.is_set():
        raise AutomationError("cart operation was cancelled")


def _each(locator: Any) -> Any:
    """Lazy Playwright locator iteration; failure handling stays at the caller."""

    return (locator.nth(index) for index in range(locator.count()))


def _beatport_track_id(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def _direct_beatport_track_url(url: str) -> str | None:
    canonical = canonical_store_url(url, "beatport")
    if canonical is None:
        return None
    parsed = urlparse(canonical)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[0] != "track" or not parts[2].isdigit():
        return None
    return urlunparse(("https", parsed.hostname or "", parsed.path, "", "", ""))


def _only_visible(locator: Any) -> Any | None:
    try:
        visible = [match for match in _each(locator) if match.is_visible()]
        # Exactly one, or nothing: two visible "Add to cart" controls mean the
        # page changed shape, and clicking either could charge the wrong
        # product - ambiguity has to degrade to "no control found".
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
            if any(match.is_visible() for match in _each(locator)):
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


def ensure_logins(pages: dict[str, Any], cancel: Event) -> None:
    """Open every required login before waiting for user-driven completion."""

    pending: dict[str, Any] = {}
    for store, page in pages.items():
        _cancelled(cancel)
        navigate_store(page, STORE_HOME[store], store)
        if _is_logged_in(page, store):
            continue
        navigate_store(page, STORE_LOGIN[store], store)
        if not _login_complete(page, store):
            pending[store] = page

    # One shared deadline starts only after every required store has its login
    # tab. The user can therefore complete Bandcamp and Beatport in either order.
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    while pending and time.monotonic() < deadline:
        _cancelled(cancel)
        for store, page in tuple(pending.items()):
            if page.is_closed():
                raise AutomationError(f"{store} login window was closed")
            # The user may temporarily visit an external SSO origin. We do not
            # inspect or touch it; only the canonical store can complete the wait.
            if is_store_url(page.url, store) and _login_complete(page, store):
                del pending[store]
        if pending:
            cancel.wait(0.25)
    if pending:
        stores = " and ".join(sorted(pending))
        raise UserActionTimeout(f"timed out waiting for manual {stores} login")


def ensure_login(page: Any, store: str, cancel: Event) -> None:
    """Wait for one user-driven login without reading or filling credentials."""

    ensure_logins({store: page}, cancel)


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
    for anchor in _each(anchors):
        if not anchor.is_visible():
            continue
        # Nearest ancestor containing any button = the smallest DOM region that
        # is one cart row, so a neighbouring row's Remove cannot be mistaken
        # for this product's.
        region = anchor.locator("xpath=ancestor::*[.//button][1]")
        if _first_visible(region.get_by_role("button", name=remove_name)) is not None:
            return True
    return False


def _bandcamp_cart_contains(page: Any, item: CartItem) -> bool:
    product_id = item.product_id
    remove_name = re.compile(r"^remove$", re.IGNORECASE)
    by_id = page.locator(
        f'#sidecartContents #item_list [data-item-id="{product_id}"], '
        f'#sidecartContents #item_list [data-track-id="{product_id}"]'
    )
    for node in _each(by_id):
        if not node.is_visible():
            continue
        region = node.locator("xpath=ancestor-or-self::*[.//a][1]")
        if _first_visible(region.get_by_role("link", name=remove_name)) is not None:
            return True

    expected = urlparse(item.product_url)
    anchors = page.locator("#sidecartContents #item_list a[href]")
    for anchor in _each(anchors):
        if not anchor.is_visible():
            continue
        url = urljoin(getattr(page, "url", item.product_url), anchor.get_attribute("href") or "")
        found = urlparse(url)
        if not is_store_url(url, "bandcamp") or (
            found.hostname,
            found.path,
        ) != (expected.hostname, expected.path):
            continue
        # Same smallest-region rule as the Beatport check above, with links.
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
    try:
        if item.store == "beatport":
            return _beatport_cart_contains(page, product_id)
        return _bandcamp_cart_contains(page, item)
    except Exception as exc:
        raise AutomationError(f"could not verify the {item.store} cart") from exc


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
    price = _verified_price(page, store, chosen.price)
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


def _prepare_on_pages(
    request_pages: Iterable[tuple[CartRequest, Any]], cancel: Event
) -> tuple[CartPlan, dict[tuple[str, str], Any]]:
    pairs = tuple(request_pages)
    page_by_track: dict[str, Any] = {}
    for request, page in pairs:
        if request.track.key in page_by_track:
            raise AutomationError("cart batch contains the same track more than once")
        page_by_track[request.track.key] = page

    logged_in: set[str] = set()

    def resolve(track: Track, store: str, url: str) -> CartItem:
        _cancelled(cancel)
        page = page_by_track[track.key]
        if store not in logged_in:
            ensure_login(page, store, cancel)
            logged_in.add(store)
        return resolve_cart_item(page, track, store, url, cancel)

    plan = plan_requests((request for request, _page in pairs), resolve)
    pages = {
        (item.track_key, item.store): page_by_track[item.track_key]
        for item in plan.items
    }
    return plan, pages


def prepare_on_page(page: Any, requests: Iterable[CartRequest], cancel: Event) -> CartPlan:
    pairs = ((request, page) for request in requests)
    plan, _pages = _prepare_on_pages(pairs, cancel)
    return plan


def _verified_price(page: Any, store: str, price: Any) -> Any:
    return (
        _bandcamp_positive_price(page, price)
        if store == "bandcamp"
        else purchase_price(price, None, None)
    )


def _refresh_item(page: Any, expected: CartItem, cancel: Event) -> CartItem:
    _cancelled(cancel)
    navigate_store(page, expected.product_url, expected.store)
    product = _product_by_id(_page_products(page, expected.store), expected.product_id)
    if product is None or product.price is None or not product.currency:
        raise AutomationError(f"{expected.store} product can no longer be verified")
    # Looked up by expected.product_id, so only the product-derived fields can
    # differ from the preflight snapshot.
    return replace(
        expected,
        product_url=product.url,
        product_title=product.title,
        price=_verified_price(page, expected.store, product.price),
        currency=product.currency,
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
        # [.,]: the button renders the price with a locale-dependent decimal
        # separator. The lookarounds stop 9.99 from matching inside 19.99.
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


def _tabs(context: Any, count: int) -> list[Any]:
    """Create the whole batch before its first tab starts navigating."""

    if count <= 0:
        return []
    pages = [context.pages[0] if context.pages else context.new_page()]
    while len(pages) < count:
        pages.append(context.new_page())
    return pages


def _prepare_cart_in_context(
    context: Any, requests: Iterable[CartRequest], cancel: Event
) -> tuple[CartPlan, dict[tuple[str, str], Any]]:
    request_list = tuple(requests)
    pages = _tabs(context, len(request_list))
    return _prepare_on_pages(zip(request_list, pages, strict=True), cancel)


def _stage_item_pages(
    context: Any, items: Iterable[CartItem], cancel: Event
) -> dict[tuple[str, str], Any]:
    item_list = tuple(items)
    pages = _tabs(context, len(item_list))
    staged: dict[tuple[str, str], Any] = {}
    for item, page in zip(item_list, pages, strict=True):
        _cancelled(cancel)
        key = (item.track_key, item.store)
        if key in staged:
            raise AutomationError("cart plan contains the same store item more than once")
        staged[key] = page
        navigate_store(page, item.product_url, item.store)
    return staged


def prepare_cart(
    requests: Iterable[CartRequest], cancel: Event, *, profile: Path | None = None
) -> CartPlan:
    with _browser_context(profile) as context:
        plan, _pages = _prepare_cart_in_context(context, requests, cancel)
        return plan


def _wait_with_carts_open(
    targets: Iterable[tuple[Any, CartItem]], cancel: Event
) -> None:
    pages: list[Any] = []
    for page, item in targets:
        pages.append(page)
        try:
            destination = (
                item.product_url
                if item.store == "bandcamp"
                else STORE_CART[item.store]
            )
            navigate_store(page, destination, item.store)
            if item.store == "bandcamp":
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


def _execute_cart_in_context(
    plan: CartPlan,
    cancel: Event,
    pages: dict[tuple[str, str], Any],
    *,
    login: bool,
) -> tuple[CartResult, ...]:
    def page_for(item: CartItem) -> Any:
        try:
            return pages[(item.track_key, item.store)]
        except KeyError as exc:
            raise AutomationError("cart plan is missing its browser tab") from exc

    if login:
        store_pages: dict[str, Any] = {}
        for item in plan.items:
            store_pages.setdefault(item.store, page_for(item))
        ensure_logins(store_pages, cancel)

    def refresh(item: CartItem) -> CartItem:
        return _refresh_item(page_for(item), item, cancel)

    def in_cart(item: CartItem) -> bool:
        _cancelled(cancel)
        return _cart_contains(page_for(item), item, cancel)

    def add(item: CartItem) -> None:
        _add_to_cart(page_for(item), item, cancel)

    results = execute_items(plan, refresh=refresh, in_cart=in_cart, add=add)
    successful_items = {
        (result.track_key, result.store)
        for result in results
        if result.status in {"added", "already_in_cart"}
        and result.store in STORE_HOSTS
    }
    cart_targets = [
        (page_for(item), item)
        for item in plan.items
        if (item.track_key, item.store) in successful_items
    ]
    if cart_targets and not cancel.is_set():
        _wait_with_carts_open(cart_targets, cancel)
    return results


def run_cart(
    requests: Iterable[CartRequest],
    cancel: Event,
    *,
    approve: Callable[[CartPlan], bool] | None = None,
    profile: Path | None = None,
) -> tuple[CartResult, ...] | None:
    """Preflight, approve, and execute in one browser context and worker thread."""

    with _browser_context(profile) as context:
        plan, pages = _prepare_cart_in_context(context, requests, cancel)
        if not plan.items:
            return plan.results
        if (
            approve is not None
            and any(not item.already_in_cart for item in plan.items)
            and not approve(plan)
        ):
            return None
        # Preflight already completed every required manual login in this same
        # persistent context, so execution can revalidate without closing tabs.
        return _execute_cart_in_context(plan, cancel, pages, login=False)


def execute_cart(
    plan: CartPlan,
    cancel: Event,
    *,
    profile: Path | None = None,
) -> tuple[CartResult, ...]:
    with _browser_context(profile) as context:
        pages = _stage_item_pages(context, plan.items, cancel)
        return _execute_cart_in_context(plan, cancel, pages, login=True)


# Async, persistent cart session -------------------------------------------------

ProgressCallback = Callable[[CartProgress], None]
ApprovalCallback = Callable[[CartPlan], Awaitable[CartPlan | None]]


def _emit_progress(callback: ProgressCallback | None, progress: CartProgress) -> None:
    if callback is not None:
        callback(progress)


def _log_cart_result(phase: str, result: CartResult) -> None:
    log = LOGGER.warning if result.status == "failed" else LOGGER.info
    log(
        "Cart %s result: store=%s status=%s code=%s track=%r reason=%s url=%s",
        phase,
        result.store or "none",
        result.status,
        result.code or "none",
        result.track_label,
        log_safe_text(result.reason) if result.reason else "none",
        redact_url(result.url) if result.url else "none",
    )


def _async_cancelled(cancel: asyncio.Event) -> None:
    if cancel.is_set():
        raise CartCancelled("cart operation was cancelled")


async def _visible_async(locator: Any) -> list[Any]:
    matches = []
    for index in range(min(await locator.count(), 500)):
        match = locator.nth(index)
        if await match.is_visible():
            matches.append(match)
    return matches


async def _only_visible_async(locator: Any) -> Any | None:
    try:
        matches = await _visible_async(locator)
    except Exception:
        return None
    return matches[0] if len(matches) == 1 else None


async def _first_visible_async(*locators: Any) -> Any | None:
    for locator in locators:
        match = await _only_visible_async(locator)
        if match is not None:
            return match
    return None


async def _first_visible_match_async(locator: Any) -> Any | None:
    try:
        matches = await _visible_async(locator)
    except Exception:
        return None
    return matches[0] if matches else None


async def _dismiss_bandcamp_cookie_banner(page: Any) -> None:
    """Choose necessary cookies so Bandcamp's footer cannot cover purchase controls."""

    necessary = page.get_by_role(
        "button", name=re.compile(r"^accept necessary only$", re.IGNORECASE)
    )
    try:
        await necessary.first.wait_for(state="visible", timeout=1500)
        await necessary.first.click(timeout=1500)
    except Exception:
        # An existing persistent profile normally has this choice already.
        pass


async def _navigate_async(page: Any, url: str, store: str) -> int | None:
    destination = canonical_store_url(url, store)
    if destination is None:
        LOGGER.warning(
            "Cart navigation refused: store=%s url=%s", store, redact_url(url)
        )
        raise UnsafeRedirect("refusing a non-canonical store URL")
    LOGGER.debug(
        "Cart navigation started: store=%s url=%s", store, redact_url(destination)
    )
    response = None
    try:
        response = await page.goto(
            destination, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
        )
    except Exception as first_error:
        LOGGER.debug(
            "Cart navigation retry: store=%s url=%s error=%s",
            store,
            redact_url(destination),
            type(first_error).__name__,
        )
        try:
            response = await page.goto(
                destination, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
            )
        except Exception as exc:
            LOGGER.warning(
                "Cart navigation failed: store=%s url=%s error=%s",
                store,
                redact_url(destination),
                type(exc).__name__,
            )
            raise BrowserNavigationError(f"could not load {store} product page") from exc
    if not is_store_url(page.url, store):
        LOGGER.warning(
            "Cart navigation escaped store: store=%s final_url=%s",
            store,
            redact_url(page.url),
        )
        raise UnsafeRedirect(f"{store} redirected outside its canonical HTTPS domain")
    status = getattr(response, "status", None)
    LOGGER.debug(
        "Cart navigation finished: store=%s status=%s final_url=%s",
        store,
        status if status is not None else "unknown",
        redact_url(page.url),
    )
    if store == "bandcamp":
        await _dismiss_bandcamp_cookie_banner(page)
    return status


async def _bandcamp_dom_products(page: Any) -> list[StoreProduct]:
    """Bounded DOM fallback when Bandcamp omits the historical metadata blobs."""

    products: dict[str, StoreProduct] = {}
    try:
        data = await page.evaluate(
            """() => {
                const t = globalThis.TralbumData || {};
                const current = t.current || {};
                return {
                    id: String(current.track_id || current.id || ""),
                    title: String(current.title || "").slice(0, 500),
                    artist: String(current.artist || t.artist || "").slice(0, 500),
                    minimum: current.minimum_price,
                    suggested: current.set_price,
                    currency: String(current.currency || t.currency || "").slice(0, 12),
                };
            }"""
        )
    except Exception:
        data = {}
    if isinstance(data, dict):
        title = str(data.get("title") or "").strip()
        product_id = str(data.get("id") or "")
        currency = str(data.get("currency") or "").upper()
        minimum = _decimal(data.get("minimum"))
        price_text = ""
        price_region = page.locator("li.buyItem.digital h4.compound-button.main-button").first
        try:
            price_text = await price_region.inner_text()
        except Exception:
            pass
        currency = currency or _currency_from_text(price_text)
        if minimum is None:
            match = re.search(r"(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)", price_text)
            minimum = _decimal(match.group(1).replace(",", ".")) if match else None
        if title and "/track/" in urlparse(page.url).path:
            products[page.url] = StoreProduct(
                "bandcamp",
                page.url.split("#", 1)[0],
                product_id if product_id.isdigit() else "",
                title,
                str(data.get("artist") or ""),
                minimum,
                currency,
            )

    anchors = page.locator('a[href*="/track/"]')
    try:
        count = min(await anchors.count(), 500)
    except Exception:
        count = 0
    for index in range(count):
        anchor = anchors.nth(index)
        try:
            href = await anchor.get_attribute("href")
            title = (await anchor.inner_text()).strip()[:500]
        except Exception:
            continue
        url = urljoin(page.url, href or "").split("#", 1)[0]
        if title and is_store_url(url, "bandcamp") and "/track/" in urlparse(url).path:
            products.setdefault(url, StoreProduct("bandcamp", url, "", title))

    if not products and "/track/" in urlparse(page.url).path:
        meta = page.locator('meta[property="og:title"]')
        try:
            title = (await meta.first.get_attribute("content") or "").strip()[:500]
        except Exception:
            title = ""
        if title:
            title = title.split(" | ", 1)[0].strip()
            products[page.url] = StoreProduct("bandcamp", page.url, "", title)
    return list(products.values())


async def _bandcamp_search_candidates_async(
    page: Any, track: Track, cancel: asyncio.Event
) -> tuple[list[StoreProduct], list[str]]:
    """Use Bandcamp's visible autocomplete without entering its CAPTCHA search page."""

    _async_cancelled(cancel)
    search = await _first_visible_match_async(
        page.get_by_placeholder("Search for artist, album, or track")
    )
    if search is None:
        await _navigate_async(page, STORE_HOME["bandcamp"], "bandcamp")
        search = await _first_visible_match_async(
            page.get_by_placeholder("Search for artist, album, or track")
        )
    if search is None:
        return [], []
    query = _display_text(track.title)[:200]
    try:
        await search.fill(query)
    except Exception:
        return [], []
    anchors = page.locator('a[href*="from=search"][href*="bandcamp.com/"]')
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        _async_cancelled(cancel)
        if await anchors.count() > 0:
            break
        await asyncio.sleep(0.2)

    tracks: dict[str, StoreProduct] = {}
    albums: dict[str, None] = {}
    for index in range(min(await anchors.count(), 40)):
        anchor = anchors.nth(index)
        try:
            href = await anchor.get_attribute("href") or ""
            lines = [line.strip() for line in (await anchor.inner_text()).splitlines() if line.strip()]
        except Exception:
            continue
        parsed = urlparse(urljoin(page.url, href))
        url = urlunparse(("https", parsed.hostname or "", parsed.path, "", "", ""))
        if not lines or not is_store_url(url, "bandcamp"):
            continue
        if parsed.path.startswith("/track/"):
            artist = ""
            for line in lines[1:]:
                if line.casefold().startswith("by "):
                    artist = line[3:].strip()
                    break
            tracks.setdefault(
                url,
                StoreProduct("bandcamp", url, "", lines[0][:500], artist[:500]),
            )
        elif parsed.path.startswith("/album/"):
            albums.setdefault(url, None)
    LOGGER.debug(
        "Bandcamp autocomplete candidates: tracks=%d albums=%d query=%r",
        len(tracks),
        len(albums),
        query,
    )
    return list(tracks.values()), list(albums)


async def _resolve_bandcamp_product_async(
    page: Any, track: Track, source_url: str, cancel: asyncio.Event
) -> StoreProduct:
    status = await _navigate_async(page, source_url, "bandcamp")
    unavailable: ProductUnavailable | None = None
    if status not in {404, 410}:
        try:
            return match_product(
                track, await _page_products_async(page, "bandcamp", cancel)
            )
        except ProductUnavailable as exc:
            unavailable = exc
    else:
        unavailable = ProductUnavailable("linked Bandcamp release is no longer available")

    search_tracks, album_urls = await _bandcamp_search_candidates_async(
        page, track, cancel
    )
    try:
        chosen = match_product(track, search_tracks)
    except ProductUnavailable:
        chosen = None
    if chosen is not None:
        LOGGER.info(
            "Bandcamp autocomplete resolved track=%r product=%s",
            track.label,
            redact_url(chosen.url),
        )
        return chosen

    for album_url in album_urls[:3]:
        _async_cancelled(cancel)
        status = await _navigate_async(page, album_url, "bandcamp")
        if status in {404, 410}:
            continue
        try:
            chosen = match_product(
                track, await _page_products_async(page, "bandcamp", cancel)
            )
        except ProductUnavailable:
            continue
        LOGGER.info(
            "Bandcamp autocomplete album resolved track=%r product=%s",
            track.label,
            redact_url(chosen.url),
        )
        return chosen
    raise unavailable or ProductUnavailable("linked release has no exact track")


async def _page_products_async(
    page: Any, store: str, cancel: asyncio.Event | None = None
) -> list[StoreProduct]:
    if not is_store_url(page.url, store):
        raise UnsafeRedirect(f"{store} page left its canonical domain")
    deadline = time.monotonic() + 5.0
    last_error: AutomationError | None = None
    while True:
        if cancel is not None:
            _async_cancelled(cancel)
        try:
            content = await page.content()
            products = products_from_html(content, page.url, store)
        except SecurityChallengeBlocked:
            try:
                await page.bring_to_front()
            except Exception:
                pass
            LOGGER.warning(
                "Cart security challenge blocked automation: store=%s url=%s",
                store,
                redact_url(page.url),
            )
            raise
        except AutomationError as exc:
            last_error = exc
            products = []
        except Exception as exc:
            last_error = StoreStructureError(f"could not inspect {store} product page")
            last_error.__cause__ = exc
            products = []
        if store == "bandcamp":
            dom_products = await _bandcamp_dom_products(page)
            by_identity: dict[tuple[str, str], StoreProduct] = {}
            for product in products:
                identity = (urlparse(product.url).hostname or "", urlparse(product.url).path)
                by_identity[identity] = product
            for product in dom_products:
                identity = (urlparse(product.url).hostname or "", urlparse(product.url).path)
                earlier = by_identity.get(identity)
                if earlier is None:
                    by_identity[identity] = product
                else:
                    by_identity[identity] = StoreProduct(
                        "bandcamp",
                        product.url,
                        product.product_id or earlier.product_id,
                        product.title or earlier.title,
                        product.artist or earlier.artist,
                        product.price if product.price is not None else earlier.price,
                        product.currency or earlier.currency,
                    )
            products = list(by_identity.values())
        if products:
            LOGGER.debug(
                "Cart products found: store=%s count=%d url=%s",
                store,
                len(products),
                redact_url(page.url),
            )
            return products[:500]
        if store == "bandcamp" and urlparse(page.url).path in ("", "/"):
            raise ProductUnavailable(
                "linked Bandcamp page is a storefront, not a release or track"
            )
        if time.monotonic() >= deadline:
            raise StoreStructureError(
                f"{store} product structure changed or is unavailable"
            ) from last_error
        await asyncio.sleep(0.2)


async def _login_visible_async(page: Any) -> bool:
    login_name = re.compile(r"^(?:log ?in|sign in)$", re.IGNORECASE)
    try:
        for locator in (
            page.get_by_role("link", name=login_name),
            page.get_by_role("button", name=login_name),
        ):
            if await _visible_async(locator):
                return True
    except Exception:
        return True
    return False


async def _is_logged_in_async(page: Any, store: str) -> bool:
    if not is_store_url(page.url, store) or await _login_visible_async(page):
        return False
    names = (
        re.compile(r"^(collection|wishlist|log out)$", re.IGNORECASE)
        if store == "bandcamp"
        else re.compile(r"^(?:account(?: settings)?|profile|log ?out)$", re.IGNORECASE)
    )
    return (
        await _first_visible_async(
            page.get_by_role("link", name=names),
            page.get_by_role("button", name=names),
        )
        is not None
    )


async def _login_complete_async(page: Any, store: str) -> bool:
    if await _is_logged_in_async(page, store):
        return True
    return (
        store == "bandcamp"
        and urlparse(page.url).path in ("", "/")
        and not await _login_visible_async(page)
    )


async def _raise_if_security_challenge_async(page: Any, store: str) -> None:
    try:
        content = await page.content()
        products_from_html(content, page.url, store)
    except SecurityChallengeBlocked as exc:
        try:
            await page.bring_to_front()
        except Exception:
            pass
        LOGGER.warning(
            "Store login blocked by security challenge: store=%s url=%s",
            store,
            redact_url(page.url),
        )
        raise SecurityChallengeBlocked(
            f"{store.capitalize()} security verification rejects automated browsers; "
            "automatic login was stopped safely"
        ) from exc
    except AutomationError:
        # Login pages are not product pages. Only the explicit challenge signal
        # matters here; missing product metadata is expected.
        pass


async def _ensure_logins_async(
    pages: dict[str, Any], cancel: asyncio.Event, progress: ProgressCallback | None = None
) -> None:
    pending: dict[str, Any] = {}
    for store, page in pages.items():
        _async_cancelled(cancel)
        _emit_progress(progress, CartProgress("login", 0, len(pages), store=store))
        await _navigate_async(page, STORE_HOME[store], store)
        await _raise_if_security_challenge_async(page, store)
        if await _is_logged_in_async(page, store):
            LOGGER.info("Store login already active: store=%s", store)
            continue
        await _navigate_async(page, STORE_LOGIN[store], store)
        await _raise_if_security_challenge_async(page, store)
        if not await _login_complete_async(page, store):
            LOGGER.info("Waiting for manual store login: store=%s", store)
            pending[store] = page

    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    while pending and time.monotonic() < deadline:
        _async_cancelled(cancel)
        for store, page in tuple(pending.items()):
            if page.is_closed():
                raise AutomationError(f"{store} login window was closed")
            await _raise_if_security_challenge_async(page, store)
            if is_store_url(page.url, store) and await _login_complete_async(page, store):
                LOGGER.info("Store login completed: store=%s", store)
                del pending[store]
        if pending:
            await asyncio.sleep(0.25)
    if pending:
        stores = " and ".join(sorted(pending))
        raise AutomationError(f"timed out waiting for manual {stores} login")


def _currency_from_text(value: str) -> str:
    match = re.search(r"\b(GBP|USD|EUR|AUD|CAD|JPY|PLN|CHF|SEK|NOK|DKK)\b", value.upper())
    if match:
        return match.group(1)
    for symbol, currency in (("£", "GBP"), ("€", "EUR"), ("$", "USD")):
        if symbol in value:
            return currency
    return ""


async def _bandcamp_quote_async(page: Any, product: StoreProduct) -> PriceQuote:
    minimum = product.price or Decimal(0)
    name = re.compile(r"^buy digital (?:track|album)$", re.IGNORECASE)
    control = await _first_visible_async(
        page.get_by_role("button", name=name),
        page.get_by_role("link", name=name),
        page.get_by_text(name, exact=True),
    )
    if control is None:
        album_only = re.compile(r"^buy the full digital album$", re.IGNORECASE)
        if await _first_visible_async(
            page.get_by_role("link", name=album_only),
            page.get_by_text(album_only, exact=True),
        ):
            raise ProductUnavailable(
                "exact Bandcamp track is sold only as part of a full album"
            )
    control_text = ""
    if control is not None:
        try:
            region = control.locator("xpath=ancestor::h4[1]")
            control_text = await region.inner_text()
        except Exception:
            try:
                control_text = await control.inner_text()
            except Exception:
                pass
    price_input = await _only_visible_async(
        page.locator('input#userPrice, input[name="userPrice"]')
    )
    if price_input is None and control is not None:
        try:
            await control.click(timeout=ACTION_TIMEOUT_MS, force=True)
            price_input = await _only_visible_async(
                page.locator('input#userPrice, input[name="userPrice"]')
            )
        except Exception:
            price_input = None

    try:
        suggested = _decimal(
            await page.evaluate(
                "() => (globalThis.TralbumData || {}).current?.set_price ?? null"
            )
        )
    except Exception:
        suggested = None
    step = None
    if price_input is not None:
        try:
            input_minimum = _decimal(await price_input.get_attribute("min"))
            input_suggested = _decimal(await price_input.input_value())
            if input_minimum is not None:
                minimum = input_minimum
            if input_suggested is not None:
                suggested = input_suggested
            step = _decimal(await price_input.get_attribute("step"))
        except Exception as exc:
            raise StoreStructureError("Bandcamp price field could not be inspected") from exc
    try:
        selected = purchase_price(minimum, suggested, step)
    except AutomationError as exc:
        raise ProductUnavailable(
            "Bandcamp item has no positive cart price; open this name-your-price item manually"
        ) from exc
    currency = product.currency or _currency_from_text(control_text)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise StoreStructureError("Bandcamp did not expose a verifiable currency")
    editable = price_input is not None or bool(
        re.search(r"\bor more\b", control_text, re.IGNORECASE)
    )
    return PriceQuote(currency, minimum, selected, suggested, step, editable)


async def _quote_async(page: Any, store: str, product: StoreProduct) -> PriceQuote:
    if store == "bandcamp":
        return await _bandcamp_quote_async(page, product)
    if product.price is None or not product.currency:
        raise StoreStructureError("beatport did not expose a verifiable price and currency")
    return PriceQuote(
        product.currency,
        product.price,
        product.price,
        suggested=product.price,
        editable=False,
    )


def _same_product(expected: CartItem, product: StoreProduct) -> bool:
    if expected.product_id and product.product_id:
        return expected.product_id == product.product_id
    return urlparse(expected.product_url).path == urlparse(product.url).path


async def _open_bandcamp_cart_async(page: Any) -> bool:
    cart_link = await _first_visible_match_async(
        page.locator('[data-test="mb-cart"] a[title="cart"]')
    )
    if cart_link is None:
        return False
    try:
        await cart_link.click(timeout=ACTION_TIMEOUT_MS)
    except Exception:
        return False
    deadline = time.monotonic() + 3.0
    sidecart = page.locator("#sidecart")
    while time.monotonic() < deadline:
        try:
            if await sidecart.is_visible() or (
                urlparse(page.url).hostname == "bandcamp.com"
                and urlparse(page.url).path == "/cart"
            ):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return True


async def _bandcamp_cart_contains_async(page: Any, item: CartItem) -> bool:
    async def contains_row() -> bool:
        remove_name = re.compile(r"^remove$", re.IGNORECASE)
        if item.product_id:
            by_id = page.locator(
                f'#sidecartContents #item_list [data-item-id="{item.product_id}"], '
                f'#sidecartContents #item_list [data-track-id="{item.product_id}"]'
            )
            for node in await _visible_async(by_id):
                region = node.locator("xpath=ancestor-or-self::*[.//a][1]")
                if await _first_visible_async(
                    region.get_by_role("link", name=remove_name)
                ):
                    return True
        expected = urlparse(item.product_url)
        selectors = ["#sidecartContents #item_list a[href]"]
        if urlparse(page.url).hostname == "bandcamp.com" and urlparse(page.url).path == "/cart":
            selectors.append('[data-test*="cart"] a[href]')
        anchors = page.locator(", ".join(selectors))
        for index in range(min(await anchors.count(), 500)):
            anchor = anchors.nth(index)
            if not await anchor.is_visible():
                continue
            url = urljoin(page.url, await anchor.get_attribute("href") or "")
            found = urlparse(url)
            if not is_store_url(url, "bandcamp") or (
                found.hostname,
                found.path,
            ) != (expected.hostname, expected.path):
                continue
            region = anchor.locator("xpath=ancestor::*[.//a][1]")
            if await _first_visible_async(
                region.get_by_role("link", name=remove_name)
            ):
                return True
        return False

    if await contains_row():
        return True

    if not await _open_bandcamp_cart_async(page):
        return False
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if await contains_row():
            return True
        await asyncio.sleep(0.2)
    return False


async def _bandcamp_cart_count_async(page: Any) -> int | None:
    locator = page.locator('[data-test="mb-cart"] .menubar-cart-icon text')
    try:
        values = []
        for index in range(min(await locator.count(), 5)):
            text = (await locator.nth(index).text_content() or "").strip()
            match = re.search(r"\d+", text)
            if match:
                values.append(int(match.group()))
        return max(values) if values else None
    except Exception:
        return None


async def _verify_bandcamp_click_async(
    page: Any, item: CartItem, count_before: int | None
) -> bool:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        count_after = await _bandcamp_cart_count_async(page)
        if (
            count_before is not None
            and count_after is not None
            and count_after > count_before
        ):
            LOGGER.debug(
                "Bandcamp cart verified by count: before=%d after=%d track=%r",
                count_before,
                count_after,
                item.track_label,
            )
            return True
        await asyncio.sleep(0.2)
    if await _bandcamp_cart_contains_async(page, item):
        LOGGER.debug("Bandcamp cart verified in current DOM: track=%r", item.track_label)
        return True
    await _navigate_async(page, item.product_url, "bandcamp")
    verified = await _bandcamp_cart_contains_async(page, item)
    LOGGER.debug(
        "Bandcamp cart verification after reload: verified=%s track=%r",
        verified,
        item.track_label,
    )
    return verified


async def _beatport_cart_contains_async(page: Any, product_id: str) -> bool:
    anchors = page.locator(
        f'a[href*="/track/"][href$="/{product_id}"], '
        f'a[href*="/track/"][href$="/{product_id}/"]'
    )
    remove_name = re.compile(r"^remove(?: track)?(?: from cart)?$", re.IGNORECASE)
    for index in range(min(await anchors.count(), 500)):
        anchor = anchors.nth(index)
        if not await anchor.is_visible():
            continue
        region = anchor.locator("xpath=ancestor::*[.//button][1]")
        if await _first_visible_async(region.get_by_role("button", name=remove_name)):
            return True
    return False


async def _cart_contains_async(
    page: Any, item: CartItem, cancel: asyncio.Event, *, navigate: bool = True
) -> bool:
    _async_cancelled(cancel)
    if navigate:
        destination = item.product_url if item.store == "bandcamp" else STORE_CART[item.store]
        await _navigate_async(page, destination, item.store)
    try:
        if item.store == "beatport":
            if not item.product_id.isdigit():
                raise StoreStructureError("beatport product has no stable numeric ID")
            return await _beatport_cart_contains_async(page, item.product_id)
        return await _bandcamp_cart_contains_async(page, item)
    except AutomationError:
        raise
    except Exception as exc:
        raise StoreStructureError(f"could not verify the {item.store} cart") from exc


async def _resolve_cart_item_async(
    page: Any, track: Track, store: str, source_url: str, cancel: asyncio.Event
) -> CartItem:
    _async_cancelled(cancel)
    if store == "bandcamp":
        chosen = await _resolve_bandcamp_product_async(
            page, track, source_url, cancel
        )
    else:
        await _navigate_async(page, source_url, store)
        chosen = match_product(
            track, await _page_products_async(page, store, cancel)
        )
    if urlparse(page.url).path != urlparse(chosen.url).path:
        await _navigate_async(page, chosen.url, store)
        products = await _page_products_async(page, store, cancel)
        verified = match_product(track, products)
        if chosen.product_id and verified.product_id != chosen.product_id:
            raise UnsafeMatch(f"{store} track identity changed after release lookup")
        chosen = verified
    quote = await _quote_async(page, store, chosen)
    unresolved = CartItem(
        track.key,
        _display_text(track.label),
        store,
        source_url,
        chosen.url,
        chosen.product_id,
        chosen.title,
        quote.selected,
        quote.currency,
        False,
        quote.minimum,
        quote.suggested,
        quote.step,
        quote.editable,
    )
    if store == "beatport":
        return unresolved
    return replace(unresolved, already_in_cart=await _cart_contains_async(
        page, unresolved, cancel
    ))


async def _refresh_item_async(
    page: Any, expected: CartItem, cancel: asyncio.Event
) -> CartItem:
    _async_cancelled(cancel)
    await _navigate_async(page, expected.product_url, expected.store)
    products = await _page_products_async(page, expected.store, cancel)
    product = next((candidate for candidate in products if _same_product(expected, candidate)), None)
    if product is None:
        raise StoreStructureError(f"{expected.store} product can no longer be verified")
    quote = await _quote_async(page, expected.store, product)
    return replace(
        expected,
        product_url=product.url,
        product_id=product.product_id or expected.product_id,
        product_title=product.title,
        currency=quote.currency,
        minimum_price=quote.minimum,
        suggested_price=quote.suggested,
        price_step=quote.step,
        price_editable=quote.editable,
    )


def _same_async_snapshot(expected: CartItem, current: CartItem) -> bool:
    expected_minimum = expected.minimum_price
    current_minimum = current.minimum_price
    return (
        expected.store == current.store
        and (
            expected.product_id == current.product_id
            if expected.product_id and current.product_id
            else urlparse(expected.product_url).path == urlparse(current.product_url).path
        )
        and _normalise(expected.product_title) == _normalise(current.product_title)
        and expected_minimum == current_minimum
        and expected.currency == current.currency
        and expected.price >= (current_minimum or Decimal(0))
    )


async def _beatport_add_control_async(page: Any, item: CartItem) -> Any | None:
    named = re.compile(r"^add(?: track)? to cart$", re.IGNORECASE)
    control = await _first_visible_async(
        page.get_by_role("button", name=named),
        page.get_by_text(named, exact=True),
    )
    if control is not None:
        return control
    amount = format(item.price, "f")
    whole, dot, fraction = amount.partition(".")
    amount_pattern = (
        rf".*(?<!\d){re.escape(whole)}[.,]{re.escape(fraction)}(?!\d).*"
        if dot
        else rf".*(?<!\d){re.escape(amount)}(?!\d).*"
    )
    price_name = re.compile(amount_pattern, re.IGNORECASE)
    control = await _first_visible_async(page.get_by_role("button", name=price_name))
    if control is not None:
        return control
    heading = await _first_visible_async(
        page.get_by_role("heading", name=item.product_title, exact=True, level=1)
    )
    if heading is None:
        return None
    region = heading.locator("xpath=ancestor::*[.//button][1]")
    return await _first_visible_async(region.get_by_role("button", name=price_name))


async def _add_to_cart_async(page: Any, item: CartItem, cancel: asyncio.Event) -> None:
    _async_cancelled(cancel)
    if not is_store_url(page.url, item.store) or urlparse(page.url).path != urlparse(
        item.product_url
    ).path:
        raise StoreStructureError(f"{item.store} product page changed before the cart click")
    if item.store == "bandcamp":
        price_input = await _only_visible_async(
            page.locator('input#userPrice, input[name="userPrice"]')
        )
        if price_input is None:
            name = re.compile(r"^buy digital (?:track|album)$", re.IGNORECASE)
            buy_control = await _first_visible_async(
                page.get_by_role("button", name=name),
                page.get_by_role("link", name=name),
                page.get_by_text(name, exact=True),
            )
            if buy_control is None:
                raise StoreStructureError("Bandcamp purchase control changed or is unavailable")
            await buy_control.click(timeout=ACTION_TIMEOUT_MS)
            price_input = await _only_visible_async(
                page.locator('input#userPrice, input[name="userPrice"]')
            )
        if price_input is not None:
            await price_input.fill(format(item.price, "f"), timeout=ACTION_TIMEOUT_MS)
        elif item.price_editable and item.price > (item.minimum_price or Decimal(0)):
            raise StoreStructureError(
                "Bandcamp no longer exposes the editable price field"
            )
        add_button = await _first_visible_async(
            page.get_by_role("button", name=re.compile(r"^add to cart$", re.IGNORECASE)),
            page.get_by_text(re.compile(r"^add to cart$", re.IGNORECASE), exact=True),
        )
    else:
        add_button = await _beatport_add_control_async(page, item)
    if add_button is None:
        raise StoreStructureError(f"{item.store} add-to-cart control changed or is unavailable")
    _async_cancelled(cancel)
    try:
        await add_button.click(timeout=ACTION_TIMEOUT_MS)
    except Exception as exc:
        raise CartUnverified(f"{item.store} cart click could not be verified") from exc


class CartBrowserSession:
    """One lazy Playwright context shared by all cart batches in a TUI run."""

    def __init__(self, profile: Path | None = None) -> None:
        self.profile = profile
        self.state: Literal["NEW", "STARTING", "READY", "CLOSING", "CLOSED", "FAILED"] = "NEW"
        self._playwright = None
        self._context = None
        self._context_headless: bool | None = None
        self._owned_pages: list[Any] = []
        self._cart_pages: dict[str, Any] = {}
        self._instrumented_pages: set[int] = set()
        self._operation_lock: asyncio.Lock | None = None
        self._closing = False

    def _lock(self) -> asyncio.Lock:
        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()
        return self._operation_lock

    def _context_closed(self, _context: Any) -> None:
        self._context = None
        self._context_headless = None
        self._owned_pages.clear()
        self._cart_pages.clear()
        self._instrumented_pages.clear()
        if not self._closing:
            self.state = "CLOSED"

    async def _ensure_context(self, *, headless: bool = True) -> Any:
        if self._context is not None and not self._context.is_closed():
            if self._context_headless == headless:
                return self._context
            await self._close_context()
        self.state = "STARTING"
        if not headless and sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        ):
            self.state = "FAILED"
            raise AutomationError("store cart needs a desktop display (on WSL, enable WSLg)")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            self.state = "FAILED"
            raise AutomationError(
                "the required Playwright dependency is missing; reinstall dj-soundcloud-digger"
            ) from exc
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if not Path(self._playwright.chromium.executable_path).is_file():
            self.state = "FAILED"
            raise ChromiumMissing("Chromium is required for store carts")
        try:
            context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile or store_profile_path()),
                headless=headless,
                locale="en-US",
                accept_downloads=False,
                chromium_sandbox=True,
            )
        except Exception as exc:
            self.state = "FAILED"
            message = str(exc).lower()
            if "executable doesn't exist" in message:
                raise ChromiumMissing("Chromium is required for store carts") from exc
            if "singleton" in message or "user data directory is already in use" in message:
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
        context.on("close", self._context_closed)
        self._context = context
        self._context_headless = headless
        self._owned_pages = list(context.pages[:1])
        self.state = "READY"
        for page in self._owned_pages:
            self._instrument_page(page)
        LOGGER.info(
            "Store browser session ready: pages=%d headless=%s",
            len(self._owned_pages),
            headless,
        )
        return context

    def _instrument_page(self, page: Any) -> Any:
        identity = id(page)
        if identity in self._instrumented_pages:
            return page
        self._instrumented_pages.add(identity)

        def response_received(response: Any) -> None:
            status = getattr(response, "status", 0)
            if status >= 400:
                LOGGER.debug(
                    "Store browser HTTP error: status=%s url=%s",
                    status,
                    redact_url(getattr(response, "url", "")),
                )

        def console_message(message: Any) -> None:
            kind = getattr(message, "type", "")
            if kind not in {"error", "warning"}:
                return
            location = getattr(message, "location", {}) or {}
            LOGGER.debug(
                "Store browser console message: type=%s source=%s",
                kind,
                redact_url(str(location.get("url") or "")),
            )

        try:
            page.on("response", response_received)
            page.on("console", console_message)
            page.on(
                "crash",
                lambda *_args: LOGGER.warning("Store browser page crashed"),
            )
        except Exception:
            # Minimal fake pages in tests do not implement Playwright events.
            pass
        return page

    async def _work_pages(self, count: int = 2, *, headless: bool = True) -> list[Any]:
        context = await self._ensure_context(headless=headless)
        pages = [page for page in self._owned_pages if not page.is_closed()]
        while len(pages) < count:
            page = self._instrument_page(await context.new_page())
            pages.append(page)
        for page in pages:
            self._instrument_page(page)
        self._owned_pages = pages
        return pages[:count]

    async def _replace_page(self, old: Any) -> Any:
        context = await self._ensure_context()
        try:
            await old.close()
        except Exception:
            pass
        new = self._instrument_page(await context.new_page())
        self._owned_pages = [new if page is old else page for page in self._owned_pages]
        return new

    async def _close_context(self) -> None:
        self._closing = True
        self.state = "CLOSING"
        context, self._context = self._context, None
        self._context_headless = None
        if context is not None:
            try:
                await context.close(reason="dj-digger cart session closed")
            except Exception:
                pass
        self._owned_pages.clear()
        self._cart_pages.clear()
        self._instrumented_pages.clear()
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._closing = False
        self.state = "CLOSED"

    async def close(self) -> None:
        async with self._lock():
            await self._close_context()

    async def reset_profile(self) -> None:
        async with self._lock():
            await self._close_context()
            target = Path(self.profile) if self.profile else data_dir() / "store-browser"
            parent = data_dir().resolve()
            if target.name != "store-browser" or target.parent.resolve() != parent:
                raise AutomationError("refusing to reset an unexpected browser profile path")
            if target.is_symlink():
                raise AutomationError("refusing to reset a symlinked browser profile")
            if not target.exists():
                return
            quarantine = target.with_name(f".store-browser-reset-{os.getpid()}-{time.time_ns()}")
            target.rename(quarantine)
            try:
                shutil.rmtree(quarantine)
            except Exception:
                quarantine.rename(target)
                raise
            target.mkdir(parents=True, mode=0o700)
            if os.name != "nt":
                target.chmod(0o700)

    async def setup_logins(
        self,
        stores: Iterable[str],
        cancel: asyncio.Event,
        progress: ProgressCallback | None = None,
    ) -> None:
        wanted = tuple(dict.fromkeys(store for store in stores if store in STORE_HOSTS))
        if not wanted:
            return
        async with self._lock():
            context = await self._ensure_context(headless=False)
            pages = [self._instrument_page(await context.new_page()) for _ in wanted]
            try:
                await _ensure_logins_async(
                    dict(zip(wanted, pages, strict=True)), cancel, progress
                )
            finally:
                for page in pages:
                    if not page.is_closed():
                        await page.close()

    async def check_logins(self, stores: Iterable[str]) -> dict[str, bool]:
        wanted = tuple(dict.fromkeys(store for store in stores if store in STORE_HOSTS))
        if not wanted:
            return {}
        async with self._lock():
            context = await self._ensure_context()
            pages = [self._instrument_page(await context.new_page()) for _ in wanted]
            try:
                states: dict[str, bool] = {}
                for store, page in zip(wanted, pages, strict=True):
                    await _navigate_async(page, STORE_HOME[store], store)
                    states[store] = await _is_logged_in_async(page, store)
                return states
            finally:
                for page in pages:
                    if not page.is_closed():
                        await page.close()

    async def _preflight(
        self,
        requests: tuple[CartRequest, ...],
        pages: list[Any],
        cancel: asyncio.Event,
        progress: ProgressCallback | None,
    ) -> CartPlan:
        queue: asyncio.Queue[tuple[int, CartRequest] | None] = asyncio.Queue()
        for index, request in enumerate(requests):
            queue.put_nowait((index, request))
        for _ in pages:
            queue.put_nowait(None)

        items: list[CartItem | None] = [None] * len(requests)
        result_slots: list[CartResult | None] = [None] * len(requests)
        structure_failures: dict[str, tuple[str, set[str]]] = {}
        broken_stores: set[str] = set()
        completed = 0

        async def worker(worker_index: int, initial_page: Any) -> None:
            nonlocal completed
            page = initial_page
            while True:
                entry = await queue.get()
                if entry is None:
                    queue.task_done()
                    return
                index, request = entry
                label = _display_text(request.track.label)
                try:
                    if cancel.is_set():
                        result_slots[index] = CartResult(
                            request.track.key,
                            label,
                            request.links[0][0] if request.links else "",
                            "failed",
                            "cart operation was cancelled",
                            "cancelled",
                        )
                        continue
                    unavailable: list[str] = []
                    for store, url in request.links:
                        if store in broken_stores:
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request,
                                    label,
                                    "Beatport will match this track by artist and title",
                                    url,
                                )
                            else:
                                result_slots[index] = CartResult(
                                    request.track.key,
                                    label,
                                    store,
                                    "failed",
                                    "store automation stopped after repeated structural failures",
                                    "store_structure",
                                )
                            break
                        try:
                            try:
                                item = await _resolve_cart_item_async(
                                    page, request.track, store, url, cancel
                                )
                            except StoreStructureError:
                                page = await self._replace_page(page)
                                pages[worker_index] = page
                                item = await _resolve_cart_item_async(
                                    page, request.track, store, url, cancel
                                )
                        except ProductUnavailable as exc:
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request,
                                    label,
                                    "Beatport will match this track by artist and title",
                                    url,
                                )
                                break
                            unavailable.append(str(exc))
                            continue
                        except UnsafeMatch as exc:
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request,
                                    label,
                                    "Beatport will match this track by artist and title",
                                    url,
                                )
                            else:
                                result_slots[index] = CartResult(
                                    request.track.key,
                                    label,
                                    store,
                                    "skipped",
                                    str(exc),
                                    "unsafe_match",
                                )
                            break
                        except UnsafeRedirect as exc:
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request,
                                    label,
                                    "Beatport will match this track by artist and title",
                                )
                            else:
                                result_slots[index] = CartResult(
                                    request.track.key,
                                    label,
                                    store,
                                    "failed",
                                    str(exc),
                                    "unsafe_redirect",
                                )
                            break
                        except SecurityChallengeBlocked as exc:
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request, label, str(exc), url
                                )
                            else:
                                result_slots[index] = CartResult(
                                    request.track.key,
                                    label,
                                    store,
                                    "failed",
                                    str(exc),
                                    "browser_failure",
                                    canonical_store_url(url, store) or "",
                                )
                            break
                        except UserActionTimeout as exc:
                            result_slots[index] = CartResult(
                                request.track.key,
                                label,
                                store,
                                "failed",
                                str(exc),
                                "user_action_timeout",
                            )
                            break
                        except StoreStructureError as exc:
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request,
                                    label,
                                    "Beatport will match this track by artist and title",
                                    url,
                                )
                                break
                            signature = str(exc)
                            earlier, keys = structure_failures.get(store, (signature, set()))
                            keys = set(keys)
                            keys.add(request.track.key)
                            if earlier != signature:
                                signature, keys = str(exc), {request.track.key}
                            structure_failures[store] = (signature, keys)
                            if len(keys) >= 2:
                                broken_stores.add(store)
                            result_slots[index] = CartResult(
                                request.track.key,
                                label,
                                store,
                                "failed",
                                str(exc),
                                "store_structure",
                            )
                            break
                        except CartCancelled:
                            result_slots[index] = CartResult(
                                request.track.key,
                                label,
                                store,
                                "failed",
                                "cart operation was cancelled",
                                "cancelled",
                            )
                            break
                        except AutomationError as exc:
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request,
                                    label,
                                    "Beatport will match this track by artist and title",
                                    url,
                                )
                            else:
                                result_slots[index] = CartResult(
                                    request.track.key,
                                    label,
                                    store,
                                    "failed",
                                    str(exc),
                                    "browser_failure",
                                )
                            break
                        except Exception as exc:
                            LOGGER.error(
                                "Unexpected cart preflight error: store=%s track=%r error=%s",
                                store,
                                label,
                                type(exc).__name__,
                            )
                            if store == "beatport":
                                result_slots[index] = _beatport_playlist_result(
                                    request,
                                    label,
                                    "Beatport will match this track by artist and title",
                                    url,
                                )
                            else:
                                result_slots[index] = CartResult(
                                    request.track.key,
                                    label,
                                    store,
                                    "failed",
                                    "unexpected store interaction failure",
                                    "browser_failure",
                                )
                            break
                        else:
                            items[index] = item
                            structure_failures.pop(store, None)
                            break
                    else:
                        result_slots[index] = CartResult(
                            request.track.key,
                            label,
                            request.links[-1][0] if request.links else "",
                            "skipped",
                            unavailable[-1] if unavailable else "no eligible Bandcamp or Beatport link",
                            "unavailable",
                        )
                finally:
                    if result_slots[index] is not None:
                        _log_cart_result("preflight", result_slots[index])
                    elif items[index] is not None:
                        ready = items[index]
                        LOGGER.info(
                            "Cart preflight ready: store=%s track=%r product=%s price=%s %s",
                            ready.store,
                            ready.track_label,
                            redact_url(ready.product_url),
                            ready.price,
                            ready.currency,
                        )
                    completed += 1
                    _emit_progress(
                        progress,
                        CartProgress(
                            "preflight",
                            completed,
                            len(requests),
                            track_label=label,
                        ),
                    )
                    queue.task_done()

        async with asyncio.TaskGroup() as group:
            for index, page in enumerate(pages):
                group.create_task(worker(index, page))
        return CartPlan(
            tuple(item for item in items if item is not None),
            tuple(result for result in result_slots if result is not None),
        )

    async def _execute_store(
        self,
        store: str,
        items: list[CartItem],
        page: Any,
        cancel: asyncio.Event,
        progress: ProgressCallback | None,
        progress_state: list[int],
        total: int,
    ) -> list[CartResult]:
        results: list[CartResult] = []
        for index, item in enumerate(items):
            if cancel.is_set():
                results.extend(
                    CartResult(
                        pending.track_key,
                        pending.track_label,
                        pending.store,
                        "failed",
                        "cart operation was cancelled",
                        "cancelled",
                    )
                    for pending in items[index:]
                )
                break
            clicked = False
            bandcamp_count_before: int | None = None
            try:
                current = await _refresh_item_async(page, item, cancel)
                if not _same_async_snapshot(item, current):
                    results.append(
                        CartResult(
                            item.track_key,
                            item.track_label,
                            store,
                            "skipped",
                            "product identity or price changed after preflight",
                            "price_changed",
                        )
                    )
                    continue
                if await _cart_contains_async(page, current, cancel):
                    results.append(
                        CartResult(item.track_key, item.track_label, store, "already_in_cart")
                    )
                    continue
                ready = await _refresh_item_async(page, item, cancel)
                if not _same_async_snapshot(item, ready):
                    results.append(
                        CartResult(
                            item.track_key,
                            item.track_label,
                            store,
                            "skipped",
                            "product identity or price changed after cart inspection",
                            "price_changed",
                        )
                    )
                    continue
                if store == "bandcamp":
                    bandcamp_count_before = await _bandcamp_cart_count_async(page)
                await _add_to_cart_async(page, ready, cancel)
                clicked = True
                if store == "bandcamp":
                    verified = await asyncio.wait_for(
                        _verify_bandcamp_click_async(
                            page, ready, bandcamp_count_before
                        ),
                        timeout=(ACTION_TIMEOUT_MS * 2) / 1000,
                    )
                else:
                    verified = await asyncio.wait_for(
                        _cart_contains_async(page, ready, asyncio.Event()),
                        timeout=(ACTION_TIMEOUT_MS * 2) / 1000,
                    )
                if verified:
                    results.append(CartResult(item.track_key, item.track_label, store, "added"))
                else:
                    results.append(
                        CartResult(
                            item.track_key,
                            item.track_label,
                            store,
                            "failed",
                            "cart click was not verified; it was not retried",
                            "cart_unverified",
                        )
                    )
            except CartUnverified as exc:
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        store,
                        "failed",
                        str(exc),
                        "cart_unverified",
                    )
                )
            except CartCancelled:
                code: CartResultCode = "cart_unverified" if clicked else "cancelled"
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        store,
                        "failed",
                        "cart state is uncertain" if clicked else "cart operation was cancelled",
                        code,
                    )
                )
            except UnsafeRedirect as exc:
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        store,
                        "failed",
                        str(exc),
                        "unsafe_redirect",
                    )
                )
            except AutomationError as exc:
                code = "cart_unverified" if clicked else "store_structure"
                results.append(
                    CartResult(item.track_key, item.track_label, store, "failed", str(exc), code)
                )
            except Exception as exc:
                LOGGER.error(
                    "Unexpected cart execution error: store=%s track=%r error=%s",
                    store,
                    item.track_label,
                    type(exc).__name__,
                )
                code = "cart_unverified" if clicked else "browser_failure"
                results.append(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        store,
                        "failed",
                        "unexpected store interaction failure",
                        code,
                    )
                )
            finally:
                if results and results[-1].track_key == item.track_key:
                    _log_cart_result("execution", results[-1])
                progress_state[0] += 1
                _emit_progress(
                    progress,
                    CartProgress(
                        "adding",
                        progress_state[0],
                        total,
                        store,
                        item.track_label,
                    ),
                )
        return results

    async def _open_final_carts(
        self,
        pages: dict[str, Any],
        successful: dict[str, list[CartItem]],
        keep_open: dict[str, list[CartItem]] | None = None,
    ) -> tuple[tuple[str, ...], tuple[CartResult, ...]]:
        opened: list[str] = []
        warnings: list[CartResult] = []
        self._cart_pages.clear()
        targets: dict[str, list[CartItem]] = defaultdict(list)
        for source in (successful, keep_open or {}):
            for store, items in source.items():
                for item in items:
                    if item not in targets[store]:
                        targets[store].append(item)
        if targets:
            final_pages = await self._work_pages(
                min(len(targets), 2), headless=False
            )
            pages = {
                store: final_pages[index]
                for index, store in enumerate(targets)
            }
        for store, items in targets.items():
            if not items:
                continue
            page = pages[store]
            try:
                if store == "beatport":
                    await _navigate_async(page, STORE_CART[store], store)
                else:
                    await _navigate_async(page, items[-1].product_url, store)
                    await _open_bandcamp_cart_async(page)
                    verified_items = successful.get(store, [])
                    deadline = time.monotonic() + 3.0
                    while True:
                        present = await asyncio.gather(
                            *(
                                _bandcamp_cart_contains_async(page, item)
                                for item in verified_items
                            )
                        )
                        missing = [
                            item
                            for item, found in zip(verified_items, present, strict=True)
                            if not found
                        ]
                        if not missing or time.monotonic() >= deadline:
                            break
                        await asyncio.sleep(0.2)
                    if missing:
                        warnings.append(
                            CartResult(
                                "",
                                "Bandcamp cart view",
                                store,
                                "failed",
                                f"final cart view did not expose {len(missing)} verified item(s)",
                                "cart_view_incomplete",
                            )
                        )
                await page.bring_to_front()
            except Exception as exc:
                LOGGER.warning(
                    "Could not expose final cart: store=%s error=%s",
                    store,
                    type(exc).__name__,
                )
                continue
            self._cart_pages[store] = page
            opened.append(store)
        for page in tuple(self._owned_pages):
            if page not in self._cart_pages.values() and not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass
        self._owned_pages = list(self._cart_pages.values())
        return tuple(opened), tuple(warnings)

    async def run_batch(
        self,
        requests: Iterable[CartRequest],
        cancel: asyncio.Event,
        *,
        approve: ApprovalCallback,
        progress: ProgressCallback | None = None,
    ) -> CartBatchOutcome:
        request_list = tuple(requests)
        async with self._lock():
            LOGGER.info("Cart batch started: tracks=%d", len(request_list))
            _emit_progress(progress, CartProgress("starting", 0, len(request_list)))
            direct_results: list[CartResult] = []
            pending_requests: list[CartRequest] = []
            for request in request_list:
                direct_url = (
                    _direct_beatport_track_url(request.links[0][1])
                    if len(request.links) == 1 and request.links[0][0] == "beatport"
                    else None
                )
                if direct_url is None:
                    pending_requests.append(request)
                    continue
                result = _beatport_playlist_result(
                    request,
                    _display_text(request.track.label),
                    "ready for Beatport playlist transfer",
                    direct_url,
                )
                direct_results.append(result)
                _log_cart_result("playlist", result)
            if not pending_requests:
                _emit_progress(
                    progress,
                    CartProgress("ready", len(request_list), len(request_list)),
                )
                return CartBatchOutcome(tuple(direct_results))
            pages = await self._work_pages(2)
            try:
                plan = await self._preflight(
                    tuple(pending_requests), pages, cancel, progress
                )
            except UserActionTimeout as exc:
                return CartBatchOutcome(
                    tuple(direct_results) + tuple(
                        CartResult(
                            request.track.key,
                            request.track.label,
                            request.links[0][0] if request.links else "",
                            "failed",
                            str(exc),
                            "user_action_timeout",
                        )
                        for request in pending_requests
                    )
                )
            plan = CartPlan(plan.items, tuple(direct_results) + plan.results)
            if cancel.is_set():
                cancelled = tuple(
                    CartResult(
                        item.track_key,
                        item.track_label,
                        item.store,
                        "failed",
                        "cart operation was cancelled",
                        "cancelled",
                    )
                    for item in plan.items
                )
                return CartBatchOutcome(plan.results + cancelled, cancelled=True)
            if not plan.items:
                LOGGER.info(
                    "Cart batch stopped after preflight: ready=0 results=%d",
                    len(plan.results),
                )
                return CartBatchOutcome(plan.results)
            LOGGER.info(
                "Cart preflight completed: ready=%d results=%d",
                len(plan.items),
                len(plan.results),
            )
            _emit_progress(progress, CartProgress("approval", 0, len(plan.items)))
            approved = await approve(plan)
            if approved is None or cancel.is_set():
                LOGGER.info("Cart batch approval cancelled")
                return CartBatchOutcome(plan.results, cancelled=True)
            LOGGER.info("Cart plan approved: items=%d", len(approved.items))

            by_store: dict[str, list[CartItem]] = defaultdict(list)
            for item in approved.items:
                by_store[item.store].append(item)
            store_pages = {
                store: pages[index]
                for index, store in enumerate(by_store)
            }
            progress_state = [0]
            all_results = list(approved.results)
            if "beatport" in by_store:
                playlist_items = [
                    CartResult(
                        item.track_key,
                        item.track_label,
                        "beatport",
                        "playlist_ready",
                        "ready for Beatport playlist transfer",
                        "playlist_ready",
                        item.product_url,
                    )
                    for item in by_store.pop("beatport")
                ]
                all_results.extend(playlist_items)
                for result in playlist_items:
                    _log_cart_result("playlist", result)
            tasks = [
                self._execute_store(
                    store,
                    items,
                    store_pages[store],
                    cancel,
                    progress,
                    progress_state,
                    len(approved.items),
                )
                for store, items in by_store.items()
            ]
            for store_results in await asyncio.gather(*tasks):
                all_results.extend(store_results)
            successful_keys = {
                (result.track_key, result.store)
                for result in all_results
                if result.status in {"added", "already_in_cart"}
            }
            successful: dict[str, list[CartItem]] = defaultdict(list)
            uncertain: dict[str, list[CartItem]] = defaultdict(list)
            for item in approved.items:
                if (item.track_key, item.store) in successful_keys:
                    successful[item.store].append(item)
                elif any(
                    result.track_key == item.track_key
                    and result.store == item.store
                    and result.code == "cart_unverified"
                    for result in all_results
                ):
                    uncertain[item.store].append(item)
            opened, warnings = await self._open_final_carts(
                store_pages, successful, uncertain
            )
            all_results.extend(warnings)
            _emit_progress(progress, CartProgress("ready", len(approved.items), len(approved.items)))
            outcome = CartBatchOutcome(tuple(all_results), opened, cancel.is_set())
            counts: dict[str, int] = defaultdict(int)
            for result in outcome.results:
                counts[result.status] += 1
            LOGGER.info(
                "Cart batch finished: added=%d already=%d playlist=%d skipped=%d "
                "failed=%d carts=%s",
                counts["added"],
                counts["already_in_cart"],
                counts["playlist_ready"],
                counts["skipped"],
                counts["failed"],
                ",".join(opened) or "none",
            )
            return outcome

    async def focus_carts(self) -> None:
        async with self._lock():
            for page in self._cart_pages.values():
                if not page.is_closed():
                    await page.bring_to_front()

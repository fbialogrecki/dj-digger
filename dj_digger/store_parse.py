"""Reading products, prices and currencies out of a store page.

Structured metadata, Bandcamp's TralbumData, and the visible DOM are merged by
canonical product path; the HTML size is bounded before parsing.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .browser_session import AutomationError
from .cart_models import SecurityChallengeBlocked, StoreProduct
from .store_urls import _beatport_track_id, is_store_url

MAX_HTML_BYTES = 2_000_000


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
            product = product.merged_over(earlier)
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


def _currency_from_text(value: str) -> str:
    match = re.search(r"\b(GBP|USD|EUR|AUD|CAD|JPY|PLN|CHF|SEK|NOK|DKK)\b", value.upper())
    if match:
        return match.group(1)
    for symbol, currency in (("£", "GBP"), ("€", "EUR"), ("$", "USD")):
        if symbol in value:
            return currency
    return ""

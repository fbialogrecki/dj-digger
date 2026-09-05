"""Deciding whether a store product is the track that was asked for.

Title variants, version tokens, artist compatibility: the rules that make a
remix not match its original and an alias still match its artist.
"""

import re
import unicodedata
from urllib.parse import urlparse

from .cart_models import CartItem, ProductUnavailable, StoreProduct, UnsafeMatch
from .links import store_for_url
from .models import Track
from .store_urls import _direct_beatport_track_url, canonical_store_url

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


def _exact_by_title(track: Track, products: list[StoreProduct]) -> StoreProduct | None:
    """The one product a title variant matches outright; artists break a tie."""

    targets = _title_variants(track.title, track.artist)
    exact = [
        product
        for product in products
        if targets & _product_title_variants(product)
    ]
    if not exact:
        return None
    if len(exact) == 1:
        return exact[0]
    same_artist = [
        product for product in exact if _normalise(product.artist) == _normalise(track.artist)
    ]
    if len(same_artist) == 1:
        return same_artist[0]
    source_artist = _artist_tokens(track.artist)
    by_artist = [
        product
        for product in exact
        if source_artist and _artists_compatible(track.artist, product.artist)
    ]
    if len(by_artist) == 1:
        return by_artist[0]
    raise UnsafeMatch("ambiguous exact product title")


def _exact_by_trailing_title(
    track: Track, products: list[StoreProduct]
) -> StoreProduct | None:
    """The one product with the same complete trailing title and version qualifier.

    Promo uploaders and labels often name the same recording with different
    artist aliases ("Phil:osophy - Remember" vs "Philth Tangent - Remember").
    The linked release still gives a safe exact fallback when one and only
    one product agrees on everything after the artist prefix.
    """

    target_tail, target_stripped = _trailing_title(track.title)
    if len(target_tail) < 4:
        return None
    target_versions = _version_tokens(track.title)
    trailing = []
    for product in products:
        tail, stripped = _trailing_title(product.title)
        if (
            tail == target_tail
            and (target_stripped or stripped)
            and _version_tokens(product.title) == target_versions
        ):
            trailing.append(product)
    if len(trailing) > 1:
        raise UnsafeMatch("ambiguous exact trailing product title")
    return trailing[0] if trailing else None


def match_product(track: Track, products: list[StoreProduct]) -> StoreProduct:
    """Return the one exact product, refusing fuzzy or version-incompatible matches."""

    chosen = _exact_by_title(track, products) or _exact_by_trailing_title(track, products)
    if chosen is not None:
        return chosen

    target_tail, _stripped = _trailing_title(track.title)
    target_versions = _version_tokens(track.title)
    target_version_core = _trailing_title(_without_version_context(track.title))[0]
    target_bases = {
        _base_title(variant) for variant in _title_variants(track.title, track.artist)
    }
    for product in products:
        if _version_tokens(product.title) == target_versions:
            continue
        if (
            len(target_tail) >= 4
            and _trailing_title(_without_version_context(product.title))[0]
            == target_version_core
        ) or _base_title(product.title) in target_bases:
            raise UnsafeMatch("version qualifier does not match")
    raise ProductUnavailable("linked release has no exact track")


def _product_url(product: CartItem | StoreProduct) -> str:
    return product.product_url if isinstance(product, CartItem) else product.url


def _same_product(
    expected: CartItem | StoreProduct, product: CartItem | StoreProduct
) -> bool:
    """Same product by id when both carry one, by canonical path otherwise."""

    if expected.product_id and product.product_id:
        return expected.product_id == product.product_id
    return urlparse(_product_url(expected)).path == urlparse(_product_url(product)).path


def _remember_exact_beatport_links(
    record, outcome
) -> bool:
    """Replace stored Beatport release links only when an exact track URL is known."""

    exact_urls = {
        result.track_key: exact
        for result in outcome.results
        if result.store == "beatport"
        and result.code == "playlist_ready"
        and (exact := _direct_beatport_track_url(result.url)) is not None
    }
    changed = False
    for track in record.tracks:
        if store_for_url(track.purchase_url or "") == "beatport":
            canonical = canonical_store_url(track.purchase_url or "", "beatport")
            if canonical is not None and canonical != track.purchase_url:
                track.purchase_url = canonical
                changed = True
        normalized_extra = []
        for url, text in track.extra_links:
            canonical = (
                canonical_store_url(url, "beatport")
                if store_for_url(url) == "beatport"
                else None
            )
            normalized_extra.append((canonical or url, text))
            changed |= canonical is not None and canonical != url
        track.extra_links = normalized_extra

        exact = exact_urls.get(track.key)
        if exact is None:
            continue
        if store_for_url(track.purchase_url or "") == "beatport":
            if track.purchase_url != exact:
                track.purchase_url = exact
                changed = True
            continue
        updated = []
        replaced = False
        for url, text in track.extra_links:
            if not replaced and store_for_url(url) == "beatport":
                updated.append((exact, text))
                replaced = True
                changed |= url != exact
            else:
                updated.append((url, text))
        if not replaced:
            updated.append((exact, "Buy on Beatport"))
            changed = True
        track.extra_links = updated
    return changed

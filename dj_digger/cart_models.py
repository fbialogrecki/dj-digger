"""The vocabulary of a store cart batch: errors, items, results, plans, callbacks.

A leaf module: nothing here touches a browser, so the adapter and the batch
runner can both import it without a cycle.
"""

import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .browser_session import AutomationError
from .links import redact_url
from .models import Track

LOG_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


LOG_SECRET = re.compile(
    r"\b([a-z0-9_-]*(?:token|password|authorization|cookie|session)[a-z0-9_-]*)"
    r"\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


class ProductUnavailable(RuntimeError):
    """The linked release has no exact, individually purchasable track."""


class UnsafeMatch(RuntimeError):
    """The candidates are too ambiguous to mutate a cart safely."""


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

    def merged_over(self, earlier: "StoreProduct") -> "StoreProduct":
        """This product, with *earlier* filling in whatever it does not say itself."""

        return StoreProduct(
            self.store,
            self.url or earlier.url,
            self.product_id or earlier.product_id,
            self.title or earlier.title,
            self.artist or earlier.artist,
            self.price if self.price is not None else earlier.price,
            self.currency or earlier.currency,
        )


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
    price_step: Decimal | None = None
    price_editable: bool = False


CartStatus = Literal[
    "added",
    "already_in_cart",
    "playlist_ready",
    "skipped",
    "failed",
    "manual",
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
    "cart_view_failed",
    "not_selected",
    "playlist_ready",
    "manual_verified",
    "manual_unverified",
]


# After this many unverified clicks in one store the batch stops clicking and
# hands the rest to the person at the browser window instead.
MANUAL_AFTER_UNVERIFIED = 2


# How many product pages the manual mode opens at once.
MANUAL_TABS_MAX = 8


# Verification runs in stages, each with its own budget, so a slow reload
# cannot eat the whole allowance: (name, seconds).
VERIFY_STAGES = (("count", 5.0), ("sidecart", 10.0), ("reload", 25.0))


VERIFY_BUDGET_SECONDS = 45.0


# Diagnostics saved when a click could not be verified or a page lost its
# shape: the last N folders under data_dir()/cart-diagnostics are kept.
CART_DIAGNOSTICS_KEEP = 10


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


@dataclass(frozen=True)
class PriceQuote:
    currency: str
    minimum: Decimal
    selected: Decimal
    suggested: Decimal | None = None
    step: Decimal | None = None
    editable: bool = False


CartPhase = Literal["starting", "login", "preflight", "approval", "adding", "manual", "ready"]


@dataclass(frozen=True)
class CartProgress:
    phase: CartPhase
    completed: int
    total: int
    store: str = ""
    track_label: str = ""


@dataclass(frozen=True)
class VerifyOutcome:
    verified: bool
    stage: str
    elapsed: float


@dataclass(frozen=True)
class CartBatchOutcome:
    results: tuple[CartResult, ...]
    cart_stores: tuple[str, ...] = ()
    cancelled: bool = False
    # Items whose click could not be verified and which the manual mode did
    # not settle; the result screen offers to finish them in the browser.
    manual_candidates: tuple[CartItem, ...] = ()

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


ProgressCallback = Callable[[CartProgress], None]


ApprovalCallback = Callable[[CartPlan], Awaitable[CartPlan | None]]


# Asked to let the person finish the given items in the browser window; True
# once they say they are done, False to give up on them.
ManualCallback = Callable[[list[CartItem]], Awaitable[bool]]

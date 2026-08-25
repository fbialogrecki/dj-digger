"""Handing links to the browser: the best one, a shop search, or everything shown.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
import urllib.parse
from collections import Counter
from threading import Event

from textual import work

from .. import browser as browser_module
from .. import cart as cart_module
from .. import gates
from .. import links as links_module
from ..state import GOT, NEW, OPENED, SKIP
from .keymap import (
    DIRECT_STORE_CATEGORIES,
    OPEN_ALL_CONFIRM_THRESHOLD,
)
from .rows import Row
from .screens import ConfirmScreen

LOGGER = logging.getLogger(__name__)

SEARCH_URLS = {
    "bandcamp": "https://bandcamp.com/search?q={query}",
    "beatport": "https://www.beatport.com/search?q={query}",
}


class OpeningMixin:
    """Handing links to the browser: the best one, a shop search, or everything shown."""

    def action_open_link(self) -> None:
        row = self.current_row()
        if row is None:
            return
        record = self.record_to_open(row)
        if record.link_text == links_module.NO_STORE_LINK:
            self.notify("No link for this track - opening it on SoundCloud", timeout=3)
        elif record.link_text == links_module.FREE_DOWNLOAD:
            self.notify("Use w to download the artist-provided file", timeout=4)
        url = (
            record.track.permalink_url
            if record.link_text in {links_module.NO_STORE_LINK, links_module.FREE_DOWNLOAD}
            else record.link_url
        )
        if browser_module.open_url(url, self.browser):
            if self.status_of(row) == NEW:
                self.state.set(row.track.key, OPENED)
            self.refresh_rows()
        else:
            self.notify("Could not open the link", severity="error")

    def _find_gate_url(self, row: Row) -> str | None:
        """The link ``w`` hands to the gate resolvers, surest bet first.

        Three passes over one shortlist rather than three shortlists, and the
        host list comes from ``gates`` rather than being spelled out again here.
        """

        candidates = [
            record
            for record in row.records
            if record.link_url
            and "soundcloud.com" not in record.link_url
            and record.link_text != links_module.NO_STORE_LINK
        ]
        for record in candidates:
            if record.category == "gate":
                return record.link_url
        for record in candidates:
            if gates.can_resolve(record.link_url):
                return record.link_url
        for record in candidates:
            if record.category not in DIRECT_STORE_CATEGORIES:
                return record.link_url
        return None

    def action_search(self, store: str) -> None:
        row = self.current_row()
        if row is None:
            return
        query = urllib.parse.quote_plus(row.track.label)
        self.notify(f"Searching {store.capitalize()} for {row.track.label}...", timeout=3)
        browser_module.open_url(SEARCH_URLS[store].format(query=query), self.browser)

    def _cart_store_order(self) -> tuple[str, ...]:
        supported = {"bandcamp", "beatport"}
        if not self.store_filters:
            return ("bandcamp", "beatport")
        selected = self.store_filters & supported
        return tuple(store for store in ("bandcamp", "beatport") if store in selected)

    def _cart_request(self, row: Row) -> cart_module.CartRequest:
        links = []
        for store in self._cart_store_order():
            record = row.record_for(store)
            if record is not None and record.link_url:
                links.append((store, record.link_url))
        return cart_module.CartRequest(row.track, tuple(links))

    def action_cart_track(self) -> None:
        row = self.current_row()
        if row is None:
            return
        request = self._cart_request(row)
        if not request.links:
            self.notify("The selected track has no eligible Bandcamp or Beatport link", timeout=4)
            return
        self._start_cart_preflight([request], single=True)

    def action_cart_visible(self) -> None:
        if not self._cart_store_order():
            self.notify(
                "The active store filters contain neither Bandcamp nor Beatport",
                severity="warning",
                timeout=4,
            )
            return
        rows = [
            row for row in self.visible_rows if self.status_of(row) not in (GOT, SKIP)
        ]
        if not rows:
            self.notify("No unhandled visible tracks to add", timeout=3)
            return
        self._start_cart_preflight([self._cart_request(row) for row in rows], single=False)

    def _start_cart_preflight(
        self, requests: list[cart_module.CartRequest], *, single: bool
    ) -> None:
        if self._cart_busy:
            self.notify("The dedicated store browser is already open", timeout=3)
            return
        self._cart_busy = True
        self._cart_cancel.clear()
        self.notify(f"Checking {len(requests)} track{'s' if len(requests) != 1 else ''}...", timeout=3)
        self._start_cart_worker(requests, single)

    def _start_cart_worker(
        self, requests: list[cart_module.CartRequest], single: bool
    ) -> None:
        self._cart_worker(
            lambda: cart_module.run_cart(
                requests,
                self._cart_cancel,
                approve=lambda plan: self._approve_cart(plan, single),
            ),
            self._cart_session_finished,
            chromium_missing=lambda: self._offer_chromium_install(requests, single),
        )

    def _approve_cart(self, plan: cart_module.CartPlan, single: bool) -> bool:
        """Keep the browser worker alive while Textual collects the decision."""

        if single:
            item = plan.items[0]
            self.call_from_thread(
                self.notify,
                f"Adding {item.track_label} — {item.currency} {item.price:.2f}",
                timeout=4,
            )
            return True

        answered = Event()
        decision: list[bool] = []

        def record_decision(confirmed) -> None:
            decision.append(bool(confirmed))
            answered.set()

        def show_confirmation() -> None:
            self.push_screen(ConfirmScreen(plan.summary()), record_decision)

        self.call_from_thread(show_confirmation)
        while not answered.wait(0.25):
            if self._cart_cancel.is_set():
                return False
        return decision[0]

    @work(thread=True, exclusive=True, group="cart")
    def _cart_worker(self, job, done, chromium_missing=None) -> None:
        """One cart operation off the UI thread, with the shared error scaffolding."""

        try:
            outcome = job()
        except Exception as exc:
            if isinstance(exc, cart_module.ChromiumMissing) and chromium_missing is not None:
                if not self._cart_cancel.is_set():
                    self.call_from_thread(chromium_missing)
                return
            if not self._cart_cancel.is_set():
                self.call_from_thread(self._cart_failed, str(exc))
            return
        if not self._cart_cancel.is_set():
            self.call_from_thread(done, outcome)

    def _offer_chromium_install(
        self, requests: list[cart_module.CartRequest], single: bool
    ) -> None:
        self.push_screen(
            ConfirmScreen(
                "Store carts need Playwright Chromium. Download it now? "
                "This is a one-time download for the installed Playwright version."
            ),
            lambda confirmed: self._chromium_install_confirmed(
                requests, single, bool(confirmed)
            ),
        )

    def _chromium_install_confirmed(
        self,
        requests: list[cart_module.CartRequest],
        single: bool,
        confirmed: bool,
    ) -> None:
        if not confirmed:
            self._cart_busy = False
            self.notify("Chromium installation cancelled", timeout=3)
            return
        self.notify("Installing Chromium in the background...", timeout=4)
        self._cart_worker(
            lambda: cart_module.install_chromium(self._cart_cancel),
            lambda _result: self._chromium_installed(requests, single),
        )

    def _chromium_installed(
        self, requests: list[cart_module.CartRequest], single: bool
    ) -> None:
        self.notify("Chromium installed; checking the store product...", timeout=4)
        self._start_cart_worker(requests, single)

    def _cart_session_finished(
        self, results: tuple[cart_module.CartResult, ...] | None
    ) -> None:
        if results is None:
            self._cart_busy = False
            self.notify("Cart addition cancelled", timeout=3)
            return
        self._cart_results_finished(results)

    def _cart_failed(self, message: str) -> None:
        self._cart_busy = False
        self.show_error(f"Cart automation failed: {message}")
        self.notify("Cart automation failed", severity="error", timeout=6)

    def _cart_results_finished(self, results: tuple[cart_module.CartResult, ...]) -> None:
        self._cart_busy = False
        counts = Counter(result.status for result in results)
        for result in results:
            if result.status in {"skipped", "failed"}:
                self.show_error(
                    f"{result.track_label} [{result.store or 'no store'}]: {result.reason}"
                )
        self.notify(
            f"Cart: {counts['added']} added, {counts['already_in_cart']} already there, "
            f"{counts['skipped']} skipped, {counts['failed']} failed",
            timeout=6,
        )

    def action_open_visible(self) -> None:
        target_rows = [row for row in self.visible_rows if self.status_of(row) not in (GOT, OPENED)]
        if not target_rows:
            self.notify("Nothing to open (all visible tracks are marked as 'got' or already opened)", timeout=3)
            return

        count = len(target_rows)
        if count > OPEN_ALL_CONFIRM_THRESHOLD and not self._pending_open_all:
            self._pending_open_all = True
            self.notify(
                f"That opens {count} tabs. Press 'a' again to confirm, "
                "or filter the list down first.",
                severity="warning",
                timeout=6,
            )
            return

        self._pending_open_all = False
        self.notify(f"Opening {len(target_rows)} links in background...", timeout=3)
        self.open_visible_in_background(target_rows)

    @work(thread=True, exclusive=True, group="open_all")
    def open_visible_in_background(self, rows: list[Row]) -> None:
        urls = [self.record_to_open(row).link_url for row in rows]

        def on_success(idx: int, url: str) -> None:
            row = rows[idx]
            if self.status_of(row) == NEW:
                self.state.set(row.track.key, OPENED)
                self.call_from_thread(self.refresh_rows)

        def handle_error(err_msg: str) -> None:
            self.call_from_thread(self.show_error, err_msg)

        opened = browser_module.open_urls(
            urls, self.browser, on_success=on_success, on_error=handle_error
        )
        self.call_from_thread(self._open_visible_finished, opened, len(rows))

    def _open_visible_finished(self, opened: int, total: int) -> None:
        if opened < total:
            self.show_error(
                f"Opened {opened}/{total} tabs. {total - opened} failed to open "
                "(OS process / browser tab opening limit reached)."
            )
        self.notify(f"Opened {opened}/{total} links", timeout=3)
        self.refresh_rows()

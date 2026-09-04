"""Handing links to the browser: the best one, a shop search, or everything shown.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import asyncio
import logging
import urllib.parse
from collections import Counter
from threading import Event

from requests import RequestException
from textual import work

from .. import browser as browser_module
from .. import cart as cart_module
from .. import gates
from .. import library as library_module
from .. import links as links_module
from ..beatport_playlist import (  # noqa: F401 - re-exported for tests
    _beatport_playlist_lines,
    _create_soundiiz_import,
    _soundiiz_metadata,
    _write_beatport_playlist,
)
from ..scanner import copy_to_clipboard
from ..state import GOT, NEW, OPENED, SKIP
from ..store_urls import _direct_beatport_track_url, canonical_store_url
from .keymap import (
    DIRECT_STORE_CATEGORIES,
    OPEN_ALL_CONFIRM_THRESHOLD,
)
from .rows import Row
from .screens import (
    CartManualScreen,
    CartPlanScreen,
    CartProgressScreen,
    CartResultScreen,
    ConfirmScreen,
)

LOGGER = logging.getLogger(__name__)

SEARCH_URLS = {
    "bandcamp": "https://bandcamp.com/search?q={query}",
    "beatport": "https://www.beatport.com/search?q={query}",
}


def _remember_exact_beatport_links(
    record: library_module.CrateRecord, outcome: cart_module.CartBatchOutcome
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
        if links_module.store_for_url(track.purchase_url or "") == "beatport":
            canonical = canonical_store_url(track.purchase_url or "", "beatport")
            if canonical is not None and canonical != track.purchase_url:
                track.purchase_url = canonical
                changed = True
        normalized_extra = []
        for url, text in track.extra_links:
            canonical = (
                canonical_store_url(url, "beatport")
                if links_module.store_for_url(url) == "beatport"
                else None
            )
            normalized_extra.append((canonical or url, text))
            changed |= canonical is not None and canonical != url
        track.extra_links = normalized_extra

        exact = exact_urls.get(track.key)
        if exact is None:
            continue
        if links_module.store_for_url(track.purchase_url or "") == "beatport":
            if track.purchase_url != exact:
                track.purchase_url = exact
                changed = True
            continue
        updated = []
        replaced = False
        for url, text in track.extra_links:
            if not replaced and links_module.store_for_url(url) == "beatport":
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
        self.open_link_in_background(url, row)

    @work(thread=True, group="open_link")
    def open_link_in_background(self, url: str, row: Row | None = None) -> None:
        """Hand one link to the browser off the interface thread.

        On WSL the handoff is a subprocess that can take seconds - twenty at
        the limit - and while it ran nothing on screen answered, Ctrl+C
        included. The mark is written back on the UI thread as before; a
        shop search has no row to mark.
        """

        opened = browser_module.open_url(url, self.browser)
        self.call_from_thread(self._link_opened, row, opened)

    def _link_opened(self, row: Row | None, opened: bool) -> None:
        if not opened:
            what = "link" if row is not None else "search"
            self.notify(f"Could not open the {what}", severity="error")
            return
        if row is None:
            return
        if self.status_of(row) == NEW:
            self.state.set(row.track.key, OPENED)
        self._paint_key(row.track.key)
        self.update_status()

    def _find_gate_url(self, row: Row) -> str | None:
        """The link ``d`` hands to the gate resolvers, surest bet first.

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
        self.open_link_in_background(SEARCH_URLS[store].format(query=query))

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

    def _cart_requests(self, row: Row) -> list[cart_module.CartRequest]:
        request = self._cart_request(row)
        if not request.links:
            return []
        if {"bandcamp", "beatport"} <= self.store_filters:
            return [
                cart_module.CartRequest(row.track, ((store, url),))
                for store, url in request.links
            ]
        return [request]

    def action_cart_track(self) -> None:
        row = self.current_row()
        if row is None:
            return
        requests = self._cart_requests(row)
        if not requests:
            self.notify("The selected track has no eligible Bandcamp or Beatport link", timeout=4)
            return
        self._start_cart_preflight(requests, single=len(requests) == 1)

    def action_cart_visible(self) -> None:
        if not self._cart_store_order():
            self.notify(
                "The active store filters contain neither Bandcamp nor Beatport",
                severity="warning",
                timeout=4,
            )
            return
        rows = [
            row for row in self.targets() if self.status_of(row) not in (GOT, SKIP)
        ]
        if not rows:
            self.notify("No unhandled visible tracks to add", timeout=3)
            return
        requests = [request for row in rows for request in self._cart_requests(row)]
        self._start_cart_preflight(requests, single=False)

    def _claim_cart(self, taken: str = "The dedicated store browser is busy") -> bool:
        """Take the store browser for one job; False, and a word to the user, if it is taken.

        Whoever claims it hands it back by clearing ``_cart_busy`` when done.
        """

        if self._cart_busy:
            self.notify(taken, timeout=3)
            return False
        self._cart_busy = True
        return True

    def _start_cart_preflight(
        self, requests: list[cart_module.CartRequest], *, single: bool
    ) -> None:
        if not self._claim_cart("The dedicated store browser is already open"):
            return
        self._cart_cancel.clear()
        self.start_job("Cart", cancel=self._cart_cancel)
        self._run_cart_batch(requests, single)

    def _show_cart_progress(self) -> CartProgressScreen:
        screen = CartProgressScreen(self._cart_cancel)
        self._cart_progress_screen = screen
        self.push_screen(screen)
        return screen

    def _hide_cart_progress(self) -> None:
        screen = self._cart_progress_screen
        self._cart_progress_screen = None
        if screen is not None and screen.is_mounted:
            try:
                screen.dismiss(None)
            except Exception:
                pass

    def _cart_progress(self, progress: cart_module.CartProgress) -> None:
        screen = self._cart_progress_screen
        if screen is not None:
            screen.update_progress(progress)

    async def _wait_cart_screen(self, screen, *, restore_progress: bool = True):
        """Show one cart-owned modal in place of the progress screen and await it.

        The modal is always removed, even if the worker is cancelled under it.
        With ``restore_progress`` a yes brings the progress screen back; a
        refusal leaves it down, since the worker is about to stop.
        """

        self._hide_cart_progress()
        try:
            result = await self.push_screen_wait(screen)
        finally:
            if screen.is_mounted:
                try:
                    screen.dismiss(None)
                except Exception:
                    pass
        if restore_progress and result and not self._cart_cancel.is_set():
            self._show_cart_progress()
        return result

    async def _approve_cart_async(
        self, plan: cart_module.CartPlan, single: bool
    ) -> cart_module.CartPlan | None:
        if single and len(plan.items) == 1 and not plan.items[0].price_editable:
            item = plan.items[0]
            if item.store == "beatport":
                message = f"Preparing {item.track_label} for a Beatport playlist"
            else:
                message = f"Adding {item.track_label} — {item.currency} {item.price:.2f}"
            self.notify(message, timeout=4)
            return plan
        LOGGER.info("Cart review opened: items=%d", len(plan.items))
        approved = await self._wait_cart_screen(CartPlanScreen(plan))
        LOGGER.info(
            "Cart review closed: approved=%d cancelled=%s",
            len(approved.items) if approved is not None else 0,
            approved is None,
        )
        return approved

    async def _manual_cart_async(self, items: list[cart_module.CartItem]) -> bool:
        """Let the person finish the staged pages; True once they say they are done."""

        LOGGER.info("Manual cart completion opened: items=%d", len(items))
        done = await self._wait_cart_screen(CartManualScreen(items))
        LOGGER.info("Manual cart completion closed: done=%s", bool(done))
        return bool(done)

    async def _install_cart_chromium(self) -> bool:
        confirmed = await self._wait_cart_screen(
            ConfirmScreen(
                "Store carts need Playwright Chromium. Download it now? "
                "This is a one-time download for the installed Playwright version."
            ),
            restore_progress=False,
        )
        if not confirmed:
            return False
        self.notify("Installing Chromium in the background...", timeout=4)
        install_cancel = Event()
        install_task = asyncio.create_task(
            asyncio.to_thread(cart_module.install_chromium, install_cancel)
        )
        try:
            while not install_task.done():
                if self._cart_cancel.is_set():
                    install_cancel.set()
                await asyncio.sleep(0.1)
            await install_task
        finally:
            if not install_task.done():
                install_cancel.set()
        self._show_cart_progress()
        return True

    @work(exclusive=True, group="cart", exit_on_error=False)
    async def _run_cart_batch(
        self, requests: list[cart_module.CartRequest], single: bool
    ) -> None:
        current_requests = list(requests)
        try:
            while current_requests:
                self._show_cart_progress()
                try:
                    outcome = await self._cart_session.run_batch(
                        current_requests,
                        self._cart_cancel,
                        approve=lambda plan: self._approve_cart_async(plan, single),
                        progress=self._cart_progress,
                        manual=self._manual_cart_async,
                    )
                except cart_module.ChromiumMissing:
                    if not await self._install_cart_chromium():
                        self.notify("Chromium installation cancelled", timeout=3)
                        return
                    continue
                except Exception as exc:
                    if not self._cart_cancel.is_set():
                        self._cart_failed(str(exc))
                    return
                finally:
                    self._hide_cart_progress()

                if outcome.cancelled and not outcome.results:
                    self.notify("Cart addition cancelled", timeout=3)
                    return
                self._cart_results_finished(outcome.results)
                action = await self._wait_cart_screen(
                    CartResultScreen(outcome), restore_progress=False
                )
                if action == "focus":
                    await self._cart_session.focus_carts()
                    return
                if action == "manual":
                    await self._finish_cart_manually(outcome)
                    return
                if action == "playlist":
                    await self._prepare_beatport_playlist(current_requests, outcome)
                    return
                if action != "retry":
                    return
                self._cart_cancel.clear()
                current_requests, single = self._retry_subset(current_requests, outcome)
        finally:
            self._hide_cart_progress()
            self._cart_busy = False

    async def _finish_cart_manually(self, outcome: cart_module.CartBatchOutcome) -> None:
        """Hand the items the automation could not add to the person at the browser."""

        settled = await self._cart_session.finish_manually(
            list(outcome.manual_candidates), self._manual_cart_async, self._cart_cancel
        )
        self._cart_results_finished(tuple(settled))
        await self._wait_cart_screen(
            CartResultScreen(cart_module.CartBatchOutcome(tuple(settled))),
            restore_progress=False,
        )

    async def _prepare_beatport_playlist(
        self, requests: list[cart_module.CartRequest], outcome: cart_module.CartBatchOutcome
    ) -> None:
        """Write the Beatport tracks as a Soundiiz import, copy them, and open Soundiiz."""

        lines = _beatport_playlist_lines(requests, outcome)
        if not lines:
            self.notify(
                "No Beatport tracks were available for the playlist",
                severity="warning",
                timeout=5,
            )
            return
        if self.crate is not None and _remember_exact_beatport_links(self.crate, outcome):
            await asyncio.to_thread(library_module.save, self.crate)
        try:
            path = await asyncio.to_thread(
                _write_beatport_playlist,
                lines,
                self._download_directory(),
            )
        except OSError as exc:
            LOGGER.error(
                "Could not save Beatport playlist: %s",
                cart_module.log_safe_text(exc),
            )
            self.show_error("Could not save the Beatport playlist")
            self.notify(
                "Could not save the Beatport playlist",
                severity="error",
                timeout=6,
            )
            return
        try:
            import_url = await asyncio.to_thread(
                _create_soundiiz_import,
                requests,
                outcome,
                self.crate_title or "DJ Digger Beatport playlist",
            )
        except (OSError, ValueError, RequestException) as exc:
            LOGGER.warning("Could not create Soundiiz import: %s", cart_module.log_safe_text(exc))
            self.notify(
                f"Playlist saved to {path}, but Soundiiz import failed",
                severity="warning",
                timeout=9,
            )
            return
        copied, opened = await asyncio.gather(
            asyncio.to_thread(copy_to_clipboard, "\n".join(lines)),
            asyncio.to_thread(browser_module.open_url, import_url, self.browser),
        )
        LOGGER.info(
            "Prepared Beatport playlist: tracks=%d copied=%s opened=%s path=%s",
            len(lines),
            copied,
            opened,
            path,
        )
        if opened and copied:
            message = f"Beatport playlist ready in Soundiiz ({len(lines)} tracks)"
        elif opened:
            message = f"Beatport playlist saved to {path}; upload it in Soundiiz"
        else:
            message = f"Beatport playlist saved to {path}; Soundiiz did not open"
        self.notify(
            message,
            severity="warning" if not opened else "information",
            timeout=9,
        )

    def _retry_subset(
        self, requests: list[cart_module.CartRequest], outcome: cart_module.CartBatchOutcome
    ) -> tuple[list[cart_module.CartRequest], bool]:
        """The requests worth another pass, and whether it may skip the review."""

        retryable = outcome.retryable_targets
        remaining = [
            request
            for request in requests
            if any((request.track.key, store) in retryable for store, _url in request.links)
        ]
        force_review = any(
            result.code == "price_changed" and result.retryable for result in outcome.results
        )
        return remaining, len(remaining) == 1 and not force_review

    def _cart_failed(self, message: str) -> None:
        LOGGER.error("Cart automation failed: %s", cart_module.log_safe_text(message))
        self.show_error(f"Cart automation failed: {message}")
        self.notify("Cart automation failed", severity="error", timeout=6)

    def _cart_results_finished(self, results: tuple[cart_module.CartResult, ...]) -> None:
        counts = Counter(result.status for result in results)
        grouped: Counter[tuple[str, str, str]] = Counter(
            (result.store, result.code, result.reason)
            for result in results
            if result.status in {"skipped", "failed"} and result.code != "not_selected"
        )
        for (store, _code, reason), count in grouped.items():
            suffix = f" ({count} tracks)" if count > 1 else ""
            LOGGER.warning(
                "Cart result group: store=%s code=%s tracks=%d reason=%s",
                store or "none",
                _code or "none",
                count,
                cart_module.log_safe_text(reason),
            )
            self.show_error(f"{store or 'no store'}: {reason}{suffix}")
        self.notify(
            f"Purchases: {counts['added']} added, {counts['already_in_cart']} already there, "
            f"{counts['playlist_ready']} in Beatport playlist, "
            f"{counts['skipped']} skipped, {counts['failed']} failed",
            timeout=6,
        )

    @work(exclusive=True, group="cart", exit_on_error=False)
    async def _run_cart_op(
        self, operation, success, *, timeout: int = 4, progress: bool = False
    ) -> None:
        """One store-browser chore on a claimed browser: a toast when it works, the banner when not.

        ``operation`` returns the awaitable; ``success`` turns its result into
        the toast, or into an empty string when there is nothing to say.
        """

        if progress:
            self._show_cart_progress()
        try:
            result = await operation()
        except Exception as exc:
            self._cart_failed(str(exc))
        else:
            message = success(result)
            if message:
                self.notify(message, timeout=timeout)
        finally:
            if progress:
                self._hide_cart_progress()
            self._cart_busy = False

    def action_setup_store_logins(self) -> None:
        if not self._claim_cart():
            return
        self._cart_cancel.clear()
        self._run_cart_op(
            lambda: self._cart_session.setup_logins(
                ("bandcamp",), self._cart_cancel, self._cart_progress
            ),
            lambda _: "Bandcamp session is ready",
            progress=True,
        )

    def action_check_store_logins(self) -> None:
        if not self._claim_cart():
            return
        self._run_cart_op(
            lambda: self._cart_session.check_logins(("bandcamp",)),
            lambda states: ", ".join(
                f"{store.capitalize()}: {'signed in' if ready else 'not signed in'}"
                for store, ready in states.items()
            ),
            timeout=6,
        )

    def action_reset_store_profile(self) -> None:
        if not self._claim_cart():
            return

        async def reset() -> bool:
            confirmed = await self._wait_cart_screen(
                ConfirmScreen(
                    "Reset the dedicated store browser? This removes its cookies and logins."
                ),
                restore_progress=False,
            )
            if confirmed:
                await self._cart_session.reset_profile()
            return bool(confirmed)

        self._run_cart_op(reset, lambda done: "Store browser profile reset" if done else "")

    def action_open_visible(self) -> None:
        target_rows = [row for row in self.targets() if self.status_of(row) not in (GOT, OPENED)]
        if not target_rows:
            self.notify("Nothing to open (all visible tracks are marked as 'got' or already opened)", timeout=3)
            return

        if not self._confirm_many(len(target_rows), "shift+O"):
            return
        self.notify(f"Opening {len(target_rows)} links in background...", timeout=3)
        self.open_visible_in_background(target_rows)

    def action_open_beatport_tracks(self) -> None:
        """Beatport carts are not automated; its exact track pages open where you are logged in.

        A release link cannot be turned into a track page without a lookup, so
        those rows are counted and left out rather than opened at the album.
        """

        rows: list[Row] = []
        urls: list[str] = []
        release_only = 0
        for row in self.targets():
            if self.status_of(row) in (GOT, SKIP):
                continue
            record = row.record_for("beatport")
            if record is None or not record.link_url:
                continue
            direct = cart_module._direct_beatport_track_url(record.link_url)
            if direct is None:
                release_only += 1
                continue
            rows.append(row)
            urls.append(direct)
        if not rows:
            self.notify(
                "No exact Beatport track pages to open"
                + (f" ({release_only} release links skipped)" if release_only else ""),
                timeout=4,
            )
            return
        count = len(rows)
        if not self._confirm_many(count, "shift+P", "Beatport tabs"):
            return
        message = f"Opening {count} Beatport track pages; press Add to cart on each"
        if release_only:
            message += f" ({release_only} release links skipped)"
        self.notify(message, timeout=5)
        self.open_visible_in_background(rows, urls)

    def _confirm_many(self, count: int, key: str, what: str = "tabs") -> bool:
        """Above the threshold, the first press of ``key`` only asks; the second goes ahead.

        Changing the filter clears the question (see filters.py), since the
        count it was about is gone.
        """

        if count <= OPEN_ALL_CONFIRM_THRESHOLD or self._pending_open == key:
            self._pending_open = None
            return True
        self._pending_open = key
        self.notify(
            f"That opens {count} {what}. Press {key} again to confirm, "
            "or filter the list down first.",
            severity="warning",
            timeout=6,
        )
        return False

    @work(thread=True, exclusive=True, group="open_all")
    def open_visible_in_background(self, rows: list[Row], urls: list[str] | None = None) -> None:
        if urls is None:
            urls = [self.record_to_open(row).link_url for row in rows]
        cancel = Event()
        self.call_from_thread(self.start_job, "Opening", len(rows), cancel=cancel)

        def on_success(idx: int, url: str) -> None:
            row = rows[idx]
            if self.status_of(row) == NEW:
                self.state.set(row.track.key, OPENED)
                self.call_from_thread(self._paint_key, row.track.key)
            self.call_from_thread(self.job_progress, 1)

        def handle_error(err_msg: str) -> None:
            self.call_from_thread(self.show_error, err_msg)
            self.call_from_thread(self.job_progress, failed=1)

        opened = browser_module.open_urls(
            urls, self.browser, on_success=on_success, on_error=handle_error, cancel=cancel
        )
        self.call_from_thread(self._open_visible_finished, opened, len(rows))

    def _open_visible_finished(self, opened: int, total: int) -> None:
        if opened < total:
            self.show_error(
                f"Opened {opened}/{total} tabs. {total - opened} failed to open "
                "(OS process / browser tab opening limit reached)."
            )
        self.notify(f"Opened {opened}/{total} links", timeout=3)
        self.finish_job()

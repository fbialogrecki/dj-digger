"""Handing links to the browser: the best one, a shop search, or everything shown.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import asyncio
import logging
import urllib.parse
from collections import Counter
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

from textual import work

from .. import browser as browser_module
from .. import cart as cart_module
from .. import gates
from .. import links as links_module
from ..scanner import copy_to_clipboard
from ..state import GOT, NEW, OPENED, SKIP
from .keymap import (
    DIRECT_STORE_CATEGORIES,
    OPEN_ALL_CONFIRM_THRESHOLD,
)
from .rows import Row
from .screens import CartPlanScreen, CartProgressScreen, CartResultScreen, ConfirmScreen

LOGGER = logging.getLogger(__name__)

SEARCH_URLS = {
    "bandcamp": "https://bandcamp.com/search?q={query}",
    "beatport": "https://www.beatport.com/search?q={query}",
}
SOUNDIIZ_BEATPORT_TRANSFER_URL = "https://soundiiz.com/beatport/import-playlist"


def _beatport_playlist_lines(
    requests: list[cart_module.CartRequest], outcome: cart_module.CartBatchOutcome
) -> tuple[str, ...]:
    """Soundiiz-compatible entries, exact when Beatport exposed a track URL."""

    requests_by_target = {
        (request.track.key, store): request
        for request in requests
        for store, _url in request.links
    }
    lines: list[str] = []
    seen_keys: set[str] = set()
    for result in outcome.results:
        if (
            result.store != "beatport"
            or result.code != "playlist_ready"
            or result.track_key in seen_keys
        ):
            continue
        request = requests_by_target.get((result.track_key, "beatport"))
        if request is None:
            continue
        seen_keys.add(result.track_key)
        url = cart_module.canonical_store_url(result.url, "beatport")
        if url and "/track/" in urlparse(url).path:
            lines.append(links_module.redact_url(url))
            continue
        artist = " ".join(request.track.artist.split())
        title = " ".join(request.track.title.split())
        lines.append(f"{artist} - {title}" if artist else title)
    return tuple(line for line in lines if line)


def _write_beatport_playlist(lines: tuple[str, ...], directory: Path) -> Path:
    """Create, never overwrite, a plain-text playlist accepted by Soundiiz."""

    directory.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = "" if index == 1 else f" ({index})"
        path = directory / f"Beatport playlist{suffix}.txt"
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines) + "\n")
        except FileExistsError:
            index += 1
            continue
        return path


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
        self.open_link_in_background(row, url)

    @work(thread=True, group="open_link")
    def open_link_in_background(self, row: Row, url: str) -> None:
        """Hand one link to the browser off the interface thread.

        On WSL the handoff is a subprocess that can take seconds - twenty at
        the limit - and while it ran nothing on screen answered, Ctrl+C
        included. The mark is written back on the UI thread as before.
        """

        opened = browser_module.open_url(url, self.browser)
        self.call_from_thread(self._link_opened, row, opened)

    def _link_opened(self, row: Row, opened: bool) -> None:
        if not opened:
            self.notify("Could not open the link", severity="error")
            return
        if self.status_of(row) == NEW:
            self.state.set(row.track.key, OPENED)
        self._paint_key(row.track.key)
        self.update_status()

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
        self.open_search_in_background(SEARCH_URLS[store].format(query=query))

    @work(thread=True, group="open_link")
    def open_search_in_background(self, url: str) -> None:
        if not browser_module.open_url(url, self.browser):
            self.call_from_thread(self.notify, "Could not open the search", severity="error")

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

    def _start_cart_preflight(
        self, requests: list[cart_module.CartRequest], *, single: bool
    ) -> None:
        if self._cart_busy:
            self.notify("The dedicated store browser is already open", timeout=3)
            return
        self._cart_busy = True
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

    async def _wait_cart_screen(self, screen):
        """Await one cart-owned modal and always remove it if its worker is cancelled."""

        self._cart_decision_screen = screen
        try:
            return await self.push_screen_wait(screen)
        finally:
            self._cart_decision_screen = None
            if screen.is_mounted:
                try:
                    screen.dismiss(None)
                except Exception:
                    pass

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
        self._hide_cart_progress()
        LOGGER.info("Cart review opened: items=%d", len(plan.items))
        approved = await self._wait_cart_screen(CartPlanScreen(plan))
        LOGGER.info(
            "Cart review closed: approved=%d cancelled=%s",
            len(approved.items) if approved is not None else 0,
            approved is None,
        )
        if approved is not None and not self._cart_cancel.is_set():
            self._show_cart_progress()
        return approved

    async def _install_cart_chromium(self) -> bool:
        self._hide_cart_progress()
        confirmed = await self._wait_cart_screen(
            ConfirmScreen(
                "Store carts need Playwright Chromium. Download it now? "
                "This is a one-time download for the installed Playwright version."
            )
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
                action = await self._wait_cart_screen(CartResultScreen(outcome))
                if action == "focus":
                    await self._cart_session.focus_carts()
                    return
                if action == "playlist":
                    lines = _beatport_playlist_lines(current_requests, outcome)
                    if not lines:
                        self.notify(
                            "No Beatport tracks were available for the playlist",
                            severity="warning",
                            timeout=5,
                        )
                        return
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
                    copied, opened = await asyncio.gather(
                        asyncio.to_thread(copy_to_clipboard, "\n".join(lines)),
                        asyncio.to_thread(
                            browser_module.open_url,
                            SOUNDIIZ_BEATPORT_TRANSFER_URL,
                            self.browser,
                        ),
                    )
                    LOGGER.info(
                        "Prepared Beatport playlist: tracks=%d copied=%s opened=%s path=%s",
                        len(lines),
                        copied,
                        opened,
                        path,
                    )
                    if opened and copied:
                        message = (
                            f"Beatport playlist ready ({len(lines)} tracks). In Soundiiz "
                            "choose Import playlist → Plain text and paste."
                        )
                    elif opened:
                        message = f"Beatport playlist saved to {path}; upload it in Soundiiz"
                    else:
                        message = f"Beatport playlist saved to {path}; Soundiiz did not open"
                    self.notify(
                        message,
                        severity="warning" if not opened else "information",
                        timeout=9,
                    )
                    return
                if action != "retry":
                    return
                retryable = outcome.retryable_targets
                current_requests = [
                    request
                    for request in current_requests
                    if any(
                        (request.track.key, store) in retryable
                        for store, _url in request.links
                    )
                ]
                self._cart_cancel.clear()
                force_review = any(
                    result.code == "price_changed" and result.retryable
                    for result in outcome.results
                )
                single = len(current_requests) == 1 and not force_review
        finally:
            self._hide_cart_progress()
            self._cart_busy = False

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

    def action_setup_store_logins(self) -> None:
        if self._cart_busy:
            self.notify("The dedicated store browser is busy", timeout=3)
            return
        self._cart_busy = True
        self._cart_cancel.clear()
        self._setup_store_logins()

    @work(exclusive=True, group="cart", exit_on_error=False)
    async def _setup_store_logins(self) -> None:
        self._show_cart_progress()
        try:
            await self._cart_session.setup_logins(
                ("bandcamp",),
                self._cart_cancel,
                self._cart_progress,
            )
        except Exception as exc:
            self._cart_failed(str(exc))
        else:
            self.notify("Bandcamp session is ready", timeout=4)
        finally:
            self._hide_cart_progress()
            self._cart_busy = False

    def action_check_store_logins(self) -> None:
        if self._cart_busy:
            self.notify("The dedicated store browser is busy", timeout=3)
            return
        self._cart_busy = True
        self._check_store_logins()

    @work(exclusive=True, group="cart", exit_on_error=False)
    async def _check_store_logins(self) -> None:
        try:
            states = await self._cart_session.check_logins(("bandcamp",))
        except Exception as exc:
            self._cart_failed(str(exc))
        else:
            summary = ", ".join(
                f"{store.capitalize()}: {'signed in' if ready else 'not signed in'}"
                for store, ready in states.items()
            )
            self.notify(summary, timeout=6)
        finally:
            self._cart_busy = False

    def action_reset_store_profile(self) -> None:
        if self._cart_busy:
            self.notify("The dedicated store browser is busy", timeout=3)
            return
        self._reset_store_profile()

    @work(exclusive=True, group="cart", exit_on_error=False)
    async def _reset_store_profile(self) -> None:
        confirmed = await self._wait_cart_screen(
            ConfirmScreen(
                "Reset the dedicated store browser? This removes its cookies and logins."
            )
        )
        if not confirmed:
            return
        self._cart_busy = True
        try:
            await self._cart_session.reset_profile()
        except Exception as exc:
            self._cart_failed(str(exc))
        else:
            self.notify("Store browser profile reset", timeout=4)
        finally:
            self._cart_busy = False

    def action_open_visible(self) -> None:
        target_rows = [row for row in self.targets() if self.status_of(row) not in (GOT, OPENED)]
        if not target_rows:
            self.notify("Nothing to open (all visible tracks are marked as 'got' or already opened)", timeout=3)
            return

        count = len(target_rows)
        if count > OPEN_ALL_CONFIRM_THRESHOLD and not self._pending_open_all:
            self._pending_open_all = True
            self.notify(
                f"That opens {count} tabs. Press shift+O again to confirm, "
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

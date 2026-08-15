"""Handing links to the browser: the best one, a shop search, or everything shown.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
import urllib.parse

from textual import work

from .. import browser as browser_module
from .. import gates
from .. import links as links_module
from ..state import GOT, NEW, OPENED
from .keymap import (
    DIRECT_STORE_CATEGORIES,
    OPEN_ALL_CONFIRM_THRESHOLD,
)
from .rows import Row

LOGGER = logging.getLogger(__name__)


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

    def action_search_bandcamp(self) -> None:
        row = self.current_row()
        if row is None:
            return
        query = urllib.parse.quote_plus(row.track.label)
        url = f"https://bandcamp.com/search?q={query}"
        self.notify(f"Searching Bandcamp for {row.track.label}...", timeout=3)
        browser_module.open_url(url, self.browser)

    def action_search_beatport(self) -> None:
        row = self.current_row()
        if row is None:
            return
        query = urllib.parse.quote_plus(row.track.label)
        url = f"https://www.beatport.com/search?q={query}"
        self.notify(f"Searching Beatport for {row.track.label}...", timeout=3)
        browser_module.open_url(url, self.browser)

    def action_cart_bandcamp(self) -> None:
        row = self.current_row()
        if row is None:
            return
        bc_record = row.record_for("bandcamp")
        if bc_record and bc_record.link_url:
            cart_url = bc_record.link_url + ("?" if "?" not in bc_record.link_url else "&") + "action=add_to_cart"
            self.notify(f"Adding to Bandcamp cart: {row.track.label}...", timeout=3)
            browser_module.open_url(cart_url, self.browser)
        else:
            query = urllib.parse.quote_plus(row.track.label)
            url = f"https://bandcamp.com/search?q={query}"
            self.notify(f"Searching Bandcamp for cart addition: {row.track.label}...", timeout=3)
            browser_module.open_url(url, self.browser)

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

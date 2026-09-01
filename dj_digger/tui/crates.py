"""The crate library: loading one, refreshing it, deleting it, and the sidebar that lists them.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
from collections.abc import Sequence

from textual.widgets import Button, DataTable, Input, ListView

from .. import library as library_module
from .. import links as links_module
from ..library import CrateHeader, CrateRecord
from ..models import LinkRecord
from ..state import NEW
from .rows import Row
from .screens import ConfirmScreen
from .widgets import CrateButton, CrateItem

LOGGER = logging.getLogger(__name__)


class CrateMixin:
    """The crate library: loading one, refreshing it, deleting it, and the sidebar that lists them."""

    def _set_records(self, records: Sequence[LinkRecord]) -> None:
        self.rows = [
            Row(position=index + 1, track=group[0].track, records=group)
            for index, group in enumerate(links_module.group_by_track(records))
        ]
        self.present = links_module.present_categories(records)
        self.store_filters = {c for c in self.store_filters if c in self.present}

    def all_records(self) -> list[LinkRecord]:
        return [record for row in self.rows for record in row.records]

    def latest_crate(self) -> CrateHeader | None:
        if not self.crates:
            return None
        # ``updated`` is refreshed_at or imported_at, whichever the crate has.
        return max(self.crates, key=lambda header: header.updated)

    async def reload_sidebar(self) -> None:
        # clear() only queues the removal, so appending without awaiting it
        # leaves the old items in place and duplicates the list.
        self.crates = library_module.list_crate_headers()
        listing = self.query_one("#crates", ListView)
        await listing.clear()
        for header in self.crates:
            listing.append(CrateItem(header))
        if self.crate is not None:
            sources = [header.source for header in self.crates]
            if self.crate.source in sources:
                listing.index = sources.index(self.crate.source)

    def highlighted_crate(self) -> CrateHeader | None:
        highlighted = self.query_one("#crates", ListView).highlighted_child
        if isinstance(highlighted, CrateItem):
            return highlighted.record
        if self.crate is None:
            return None
        return CrateHeader(
            self.crate.source,
            self.crate.title,
            self.crate.refreshed_at or self.crate.imported_at or "",
            self.crate.partial,
        )

    def open_crate(self, header: CrateHeader) -> None:
        """Load the full record behind a sidebar entry and show it."""

        record = library_module.load(header.source)
        if record is None:
            self.notify(f"'{header.title}' is gone from the library", severity="warning")
            self.call_next(self.reload_sidebar)
            return
        self.load_crate(record)

    def load_crate(self, record: CrateRecord) -> None:
        self.crate = record
        records = links_module.categorise_all(record.active_tracks)
        self.load_records(records, title=record.title)

    def load_records(self, records: Sequence[LinkRecord], *, title: str = "") -> None:
        self._set_records(records)
        self.selected.clear()
        self._anchor = None
        if title:
            self.crate_title = title
            self.sub_title = title
        self.search_term = ""
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        self.refresh_rows(keep_cursor=False)
        self.query_one("#tracks", DataTable).focus()

    def refresh_crate(self, header: CrateHeader | None) -> None:
        if header is None:
            self.notify("No crate to refresh", timeout=2)
            return
        if not header.source:
            self.notify("This crate has no source to refresh from", severity="warning")
            return
        if self.crate is None or self.crate.source != header.source:
            record = library_module.load(header.source)
            if record is None:
                self.notify(f"'{header.title}' is gone from the library", severity="warning")
                return
            self.crate = record
        self._start_dig(header.source)

    def confirm_delete_crate(self, header: CrateHeader | None) -> None:
        if header is None:
            self.notify("No crate to delete", timeout=2)
            return
        self.push_screen(
            ConfirmScreen(f"Delete the crate '{header.title}'? This cannot be undone."),
            lambda confirmed: self._crate_delete_answered(header, bool(confirmed)),
        )

    def action_refresh_crate(self) -> None:
        self.refresh_crate(self.highlighted_crate())

    def action_delete_crate(self) -> None:
        self.confirm_delete_crate(self.highlighted_crate())

    def action_reset_crate_statuses(self) -> None:
        if not self.rows:
            return
        for row in self.rows:
            self.state.set(row.track.key, NEW)
        self.refresh_rows()
        self.notify("Reset all track statuses to 'new' for this crate", timeout=3)

    def _crate_delete_answered(self, header: CrateHeader, confirmed: bool) -> None:
        if not confirmed:
            return
        # Reset track statuses for tracks in this crate so re-adding the crate
        # starts fresh. The sidebar only holds headers, so the tracks come from
        # the record itself.
        record = library_module.load(header.source)
        for track in record.tracks if record is not None else []:
            self.state.set(track.key, NEW)

        library_module.delete(header.source)
        if self.crate is not None and self.crate.source == header.source:
            self.crate = None
            self.crate_title = ""
            self.load_records([])
        self.crates = library_module.list_crate_headers()
        self.notify(f"Deleted '{header.title}'", timeout=3)
        remaining = self.latest_crate()
        if not self.rows and remaining is not None:
            self.open_crate(remaining)
        self.call_next(self.reload_sidebar)

    def action_toggle_sidebar(self) -> None:
        self.query_one("#sidebar").toggle_class("collapsed")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        header = self.highlighted_crate()
        if header is not None:
            self.open_crate(header)
        self.query_one("#tracks", DataTable).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button
        if isinstance(button, CrateButton):
            if button.intent == "refresh":
                self.refresh_crate(button.record)
            else:
                self.confirm_delete_crate(button.record)
            return
        if button.id == "crate-add":
            self.action_dig_link()

    def _reload_from_crate(self) -> None:
        """Rebuild the rows from the crate, keeping filters and cursor in place."""

        if self.crate is None:
            return
        self._set_records(links_module.categorise_all(self.crate.active_tracks))
        self.refresh_rows()

    def action_remove_track(self) -> None:
        """Drop a track from your copy. SoundCloud is read-only to us."""

        rows = self.selected_rows() or [self.current_row()]
        if rows == [None]:
            return
        if self.crate is None:
            self.notify("This list is not a saved crate, nothing to remove from", timeout=4)
            return
        for row in rows:
            self.crate.remove(row.track.key)
            # ctrl+z puts them back one at a time, newest first.
            self._undone.append(row.track.key)
        library_module.save(self.crate)
        self.selected.clear()
        self._reload_from_crate()
        if len(rows) == 1:
            self.notify(f"Removed {rows[0].track.label} - ctrl+z to undo", timeout=4)
        else:
            self.notify(f"Removed {len(rows)} tracks - ctrl+z puts them back one by one", timeout=4)

    def action_undo_remove(self) -> None:
        if self.crate is None or not self._undone:
            self.notify("Nothing to undo", timeout=2)
            return
        key = self._undone.pop()
        self.crate.restore(key)
        library_module.save(self.crate)
        self._reload_from_crate()
        self.notify("Restored", timeout=2)

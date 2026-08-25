"""The crate library: loading one, refreshing it, deleting it, and the sidebar that lists them.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
from collections.abc import Sequence

from textual.widgets import Button, DataTable, Input, ListView

from .. import library as library_module
from .. import links as links_module
from ..library import CrateRecord
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

    def latest_crate(self) -> CrateRecord | None:
        if not self.crates:
            return None
        return max(self.crates, key=lambda r: r.refreshed_at or r.imported_at or "")

    async def reload_sidebar(self) -> None:
        # clear() only queues the removal, so appending without awaiting it
        # leaves the old items in place and duplicates the list.
        self.crates = library_module.list_crates()
        listing = self.query_one("#crates", ListView)
        await listing.clear()
        for record in self.crates:
            listing.append(CrateItem(record))
        if self.crate is not None:
            sources = [record.source for record in self.crates]
            if self.crate.source in sources:
                listing.index = sources.index(self.crate.source)

    def highlighted_crate(self) -> CrateRecord | None:
        highlighted = self.query_one("#crates", ListView).highlighted_child
        if isinstance(highlighted, CrateItem):
            return highlighted.record
        return self.crate

    def load_crate(self, record: CrateRecord) -> None:
        self.crate = record
        records = links_module.categorise_all(record.active_tracks)
        self.load_records(records, title=record.title)

    def load_records(self, records: Sequence[LinkRecord], *, title: str = "") -> None:
        self._set_records(records)
        if title:
            self.crate_title = title
            self.sub_title = title
        self.search_term = ""
        search = self.query_one("#search", Input)
        search.value = ""
        search.remove_class("visible")
        self.refresh_rows(keep_cursor=False)
        self.query_one("#tracks", DataTable).focus()

    def refresh_crate(self, record: CrateRecord | None) -> None:
        if record is None:
            self.notify("No crate to refresh", timeout=2)
            return
        if not record.source:
            self.notify("This crate has no source to refresh from", severity="warning")
            return
        self.crate = record
        self._start_dig(record.source)

    def confirm_delete_crate(self, record: CrateRecord | None) -> None:
        if record is None:
            self.notify("No crate to delete", timeout=2)
            return
        self.push_screen(
            ConfirmScreen(f"Delete the crate '{record.title}'? This cannot be undone."),
            lambda confirmed: self._crate_delete_answered(record, bool(confirmed)),
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

    def _crate_delete_answered(self, record: CrateRecord, confirmed: bool) -> None:
        if not confirmed:
            return
        # Reset track statuses for tracks in this crate so re-adding the crate starts fresh
        for track in record.tracks:
            self.state.set(track.key, NEW)

        library_module.delete(record.source)
        if self.crate is not None and self.crate.source == record.source:
            self.crate = None
            self.crate_title = ""
            self.load_records([])
        self.crates = library_module.list_crates()
        self.notify(f"Deleted '{record.title}'", timeout=3)
        remaining = self.latest_crate()
        if not self.rows and remaining is not None:
            self.load_crate(remaining)
        self.call_next(self.reload_sidebar)

    def action_toggle_sidebar(self) -> None:
        self.query_one("#sidebar").toggle_class("collapsed")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        record = self.highlighted_crate()
        if record is not None:
            self.load_crate(record)
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

        row = self.current_row()
        if row is None:
            return
        if self.crate is None:
            self.notify("This list is not a saved crate, nothing to remove from", timeout=4)
            return
        track = row.track
        self.crate.remove(track.key)
        library_module.save(self.crate)
        self._undone.append(track.key)
        self._reload_from_crate()
        self.notify(f"Removed {track.label} - ctrl+z to undo", timeout=4)

    def action_undo_remove(self) -> None:
        if self.crate is None or not self._undone:
            self.notify("Nothing to undo", timeout=2)
            return
        key = self._undone.pop()
        self.crate.restore(key)
        library_module.save(self.crate)
        self._reload_from_crate()
        self.notify("Restored", timeout=2)

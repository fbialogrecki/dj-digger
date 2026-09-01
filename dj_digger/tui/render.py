"""Drawing the table and the status bar, and the marks that change what they say.

Mixed into ``DiggerApp``; the attributes these reach for are set up in its
``__init__``.
"""

import logging
from collections import Counter

from rich.table import Table
from rich.text import Text
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static

from .. import links as links_module
from ..state import GOT, NEW, OPENED, SKIP
from .filters import SORT_COLUMN
from .keymap import (
    DOMAIN_BADGE_CATEGORIES,
    FLASH,
    GENRE_WIDTH,
    INDEX_WIDTH,
    LEADING_WIDTH,
    LOCAL_FILE_GLYPH,
    MARK_WIDTH,
    MIN_TITLE_WIDTH,
    OPTIONAL_COLUMN_SPECS,
    PLAYING_GLYPH,
    QUICK_FILTER_KEYS,
    SPINNER,
    SPINNER_EVERY,
    STATUS_STYLES,
    STORES_WIDTH,
    TIME_WIDTH,
)
from .rows import Row
from .theme import FALLBACK_MUTED
from .widgets import TrackTable

LOGGER = logging.getLogger(__name__)


class RenderMixin:
    """Drawing the table and the status bar, and the marks that change what they say."""

    def _store_badges(self, row: Row) -> Text:
        """Every store this track turned up in, the one ``o`` opens picked out."""

        opening = self.record_to_open(row)
        badges = Text()
        for record in row.records:
            if badges:
                badges.append(" ")
            free = record.link_text == links_module.FREE_DOWNLOAD
            if record.category == "gate":
                host = links_module.host_of(record.link_url)
                name = "\u2193gate" if free else "gate"
                if record is not opening:
                    style = self.muted
                elif free:
                    style = "bold green"
                else:
                    style = "bold cyan"
                badges.append(name, style=style)
                if host and host != "gate":
                    clean_host = host.rpartition(".")[0] or host
                    badges.append(f"({clean_host})", style=self.muted)
            else:
                if record.category in DOMAIN_BADGE_CATEGORIES:
                    name = links_module.host_of(record.link_url) or record.category
                elif free:
                    name = "\u2193" + record.category
                else:
                    name = record.category

                if record is not opening:
                    style = self.muted
                elif free:
                    style = "bold green"
                elif record.link_text == links_module.NO_STORE_LINK:
                    style = self.muted
                else:
                    style = "bold cyan"
                badges.append(name, style=style)
        # DataTable clips the cell at STORES_WIDTH with nothing to show for it,
        # so "gate(hypeddit)" arrives as "gate(hypedd" and reads as a misspelt
        # store rather than a cut one. Cut it here, with the mark that says so.
        if len(badges.plain) > STORES_WIDTH:
            badges.truncate(STORES_WIDTH, overflow="ellipsis")
        return badges

    def _playing_key(self) -> str | None:
        loaded = self.player.loaded
        return loaded.track.key if loaded is not None else None

    def _cells(self, row: Row, playing_key: str | None) -> list[Text]:
        status = self.status_of(row)
        glyph, style, _meaning = STATUS_STYLES[status]
        style = self._themed(style)
        label_text = row.track.label
        dim = self.muted if status == SKIP else ""

        if row.track.key in self.download_progress:
            pct = self.download_progress[row.track.key]
            glyph = "\u29d7"
            style = "bold yellow"
            label_text = f"[{int(pct * 100)}%] {row.track.label}"
            dim = "bold black on yellow"
        else:
            if status == GOT:
                dim = "bold green"

        label_cell = Text(label_text, style=dim)
        if self.crate is not None and row.track.key in self.crate.new_track_keys:
            # Sorted to the top of the crate by CrateRecord.active_tracks.
            label_cell = Text("NEW ", style="bold yellow").append_text(label_cell)
        selected = row.track.key in self.selected
        if selected:
            label_cell = Text("\u258c", style="bold cyan").append_text(label_cell)

        leading = Text(
            LOCAL_FILE_GLYPH if row.track.local_path else " ",
            style="bold cyan",
        )
        leading.append(
            PLAYING_GLYPH if row.track.key == playing_key else " ",
            style="green",
        )

        return [
            leading,
            Text(glyph, style=style),
            Text(str(row.position), style="reverse" if selected else self.muted),
            label_cell,
            self._store_badges(row),
            Text(row.track.genre_label or "-", style=self.muted),
            *self._optional_cells(row),
            Text(row.track.duration_label or "-", style=self.muted),
        ]

    def _paint_row(self, index: int, flash: str = "") -> None:
        """Rewrite one row in place, rather than rebuilding the whole table."""

        if not self.query("#tracks") or not 0 <= index < len(self.visible_rows):
            return
        table = self.query_one("#tracks", TrackTable)
        if index >= table.row_count:
            return
        for column, cell in enumerate(self._cells(self.visible_rows[index], self._playing_key())):
            if flash:
                cell.stylize(flash)
            table.update_cell_at(Coordinate(index, column), cell, update_width=False)

    def _themed(self, style: str) -> str:
        """The keymap names its dim style by the terminal colour; the theme decides."""

        return self.muted if style == FALLBACK_MUTED else style

    def enabled_columns(self) -> list[tuple[str, str, int]]:
        """The optional column specs switched on in Settings, in table order."""

        wanted = set(self.config.columns)
        return [spec for spec in OPTIONAL_COLUMN_SPECS if spec[0] in wanted]

    def build_columns(self, table: TrackTable) -> None:
        keys = self._column_keys = {}
        keys["leading"] = table.add_column(
            Text(LOCAL_FILE_GLYPH + PLAYING_GLYPH, style=self.muted),
            width=LEADING_WIDTH,
        )
        keys["mark"] = table.add_column("", width=MARK_WIDTH)
        keys["#"] = table.add_column("#", width=INDEX_WIDTH)
        table.flexible_column = keys["Track"] = table.add_column("Track", width=MIN_TITLE_WIDTH)
        keys["Stores"] = table.add_column("Stores", width=STORES_WIDTH)
        keys["Genre"] = table.add_column("Genre", width=GENRE_WIDTH)
        for _name, header, width in self.enabled_columns():
            keys[header] = table.add_column(header, width=width)
        keys["Time"] = table.add_column("Time", width=TIME_WIDTH)
        self._paint_headers(table)

    def _paint_headers(self, table: TrackTable | None = None) -> None:
        """Put the sort arrow on the sorted column's header and nowhere else."""

        table = table or self.query_one("#tracks", TrackTable)
        arrow = " \u25bc" if self.sort_reverse else " \u25b2"
        sorted_header = SORT_COLUMN.get(self.sort_key or "", "")
        for name, key in self._column_keys.items():
            if key not in table.columns or name == "leading":
                continue
            base = "" if name == "mark" else name
            table.columns[key].label = Text(base + (arrow if name == sorted_header else ""))
        # The header is cached per column; the label change alone does not
        # redraw it. Private, like _post_selected_message in widgets.py.
        table._clear_caches()
        table.refresh()

    def rebuild_columns(self) -> None:
        """Settings changed which columns show; start the table over."""

        table = self.query_one("#tracks", TrackTable)
        table.clear(columns=True)
        self.build_columns(table)
        self.refresh_rows()
        self.call_after_refresh(table.fit_flexible_column)

    def _optional_cells(self, row: Row) -> list[Text]:
        track = row.track
        values = {
            "bpm": track.bpm_label,
            "key": track.key_signature,
            "year": str(track.release_year or ""),
            "label": track.label_name,
        }
        return [
            Text(values[name] or "-", style=self.muted)
            for name, _header, _width in self.enabled_columns()
        ]

    def _paint_key(self, key: str) -> None:
        """Repaint the row showing this track, if it is on screen."""

        for index, row in enumerate(self.visible_rows):
            if row.track.key == key:
                self._paint_row(index)
                return

    def _flash_row(self, index: int, style: str) -> None:
        """Light the row you acted on, so a keypress is visibly a change."""

        self._paint_row(index, flash=style)
        self.set_timer(FLASH, lambda: self._paint_row(index))

    def refresh_rows(self, *, keep_cursor: bool = True) -> None:
        table = self.query_one("#tracks", TrackTable)
        previous = table.cursor_row if keep_cursor else 0
        previous_scroll = table.scroll_offset if keep_cursor else None
        cursor_key = None
        top_key = None
        if keep_cursor and self.visible_rows:
            if 0 <= previous < len(self.visible_rows):
                cursor_key = self.visible_rows[previous].track.key
            top_index = min(previous_scroll.y, len(self.visible_rows) - 1)
            top_key = self.visible_rows[top_index].track.key
        self.visible_rows = self.matching_rows()
        playing_key = self._playing_key()

        table.clear()
        for row in self.visible_rows:
            table.add_row(*self._cells(row, playing_key))

        if self.visible_rows:
            indexes = {row.track.key: index for index, row in enumerate(self.visible_rows)}
            cursor = indexes.get(cursor_key, min(previous, len(self.visible_rows) - 1))
            table.move_cursor(row=cursor, scroll=not keep_cursor)
        table.fit_flexible_column()
        if previous_scroll is not None:
            scroll_y = indexes.get(top_key, previous_scroll.y) if self.visible_rows else 0

            def restore_scroll() -> None:
                table.scroll_to(
                    x=previous_scroll.x,
                    y=scroll_y,
                    animate=False,
                    force=True,
                    immediate=True,
                )

            restore_scroll()
            table.call_after_refresh(restore_scroll)
        self._drop_stale_preparation()
        self.update_status()

    def update_status(self) -> None:
        """One bar: the store legend on the left, where you are up to on the right.

        These were two stacked bars above the footer, which made three rows of
        chrome under the table. The crate name went with them - the sidebar
        already highlights which crate you are in.
        """

        bar = self.query_one("#status", Static)
        stores = self._store_line()
        progress = self._progress_line()

        grid = Table.grid(expand=True)
        grid.add_column(no_wrap=True)
        # Narrow terminals cannot have both. The legend is what the number keys
        # are documented by, so the counts are what goes - unless a job is
        # running, when the spinner is the one thing that must stay visible.
        if len(stores) + len(progress) + 2 <= bar.size.width:
            grid.add_column(justify="right", no_wrap=True)
            grid.add_row(stores, progress)
        elif self.job is not None or self._digging:
            grid.add_row(progress)
        else:
            grid.add_row(stores)
        bar.update(grid)

    def _progress_line(self) -> Text:
        counts = Counter(self.status_of(row) for row in self.rows)

        pieces = []
        job = self.job
        if job is not None:
            glyph = SPINNER[(self._frame // SPINNER_EVERY) % len(SPINNER)]
            pieces.append(f"{glyph} {job.describe()}")
        elif self._digging:
            # A dig driven without a job line, as the older tests do.
            glyph = SPINNER[(self._frame // SPINNER_EVERY) % len(SPINNER)]
            pieces.append(f"{glyph} {self._dig_message}")
        pieces.append(f"{len(self.visible_rows)}/{len(self.rows)} tracks")
        if self.selected:
            pieces.append(f"{len(self.selected)} selected")
        if self.sort_key:
            pieces.append(f"sort: {self.sort_key}{' \u25bc' if self.sort_reverse else ' \u25b2'}")
        pieces += [
            f"got {counts[GOT]}",
            f"skipped {counts[SKIP]}",
            f"opened {counts[OPENED]}",
        ]
        if self.search_term:
            pieces.append(f"search: {self.search_term!r}")
        if self.hide_handled:
            pieces.append("hiding handled")
        if self.crate is not None and self.crate.partial:
            pieces.append("imported from a file, press r to complete it")
        return Text(" \u00b7 ".join(pieces), style=self.muted)

    def _store_line(self) -> Text:
        """The stores in this crate, numbered, so the number keys explain themselves."""

        line = Text()
        self._badge_click_regions = []
        if not self.rows:
            line.append("press d to dig a link", style=self.muted)
            return line

        # Counted over what the search and hide-handled left, so the legend does
        # not claim 113 smartlinks next to the two rows a search is showing. The
        # store filter itself is deliberately not applied: counting that would
        # zero every store except the one you are in, which is the legend you
        # need to get back out of it.
        by_category = links_module.count_by_category(
            [record for row in self.soft_matching_rows() for record in row.records]
        )
        showing_all = not self.store_filters

        # Click regions are terminal-cell offsets (on_click compares event.x),
        # so spans come from cell_len of what has actually been appended.
        line.append("\u25b8 " if showing_all else "  ", style="bold")

        start = line.cell_len
        line.append("0 all", style="bold reverse" if showing_all else self.muted)
        self._badge_click_regions.append((start, line.cell_len, 0))

        for index, category in enumerate(self.present, start=1):
            active = category in self.store_filters
            line.append("  \u25b8" if active else "   ", style="bold")

            label = f"{index} {category}" if index <= QUICK_FILTER_KEYS else category
            start = line.cell_len
            line.append(label, style="bold reverse cyan" if active else "cyan")
            line.append(f"\u00b7{by_category[category]}", style=self.muted)
            self._badge_click_regions.append((start, line.cell_len, index))

        return line

    def _mark(self, row: Row, index: int, status: str, message: str) -> None:
        self.state.set(row.track.key, status)
        self.notify(f"{message}: {row.track.label}", timeout=2)
        if self.hide_handled:
            # The row is on its way out of the list, so there is nothing to light.
            self.refresh_rows()
            return
        self._flash_row(index, self._themed(STATUS_STYLES[status][1]))
        self.update_status()

    def _mark_selected(self, status: str, message: str) -> bool:
        """Mark every selected row at once. False when nothing is selected."""

        rows = self.selected_rows()
        if not rows:
            return False
        for row in rows:
            self.state.set(row.track.key, status)
        self.notify(f"{message}: {len(rows)} tracks", timeout=2)
        self.refresh_rows()
        return True

    def _toggle_status(self, status: str, message: str) -> None:
        """Pressing the same key again clears the mark, which is what people try."""

        if self._mark_selected(status, message):
            return
        row = self.current_row()
        if row is None:
            return
        clearing = self.status_of(row) == status
        cursor = self.query_one("#tracks", DataTable).cursor_row
        judging_what_plays = self._playing_index() == cursor
        label = "Unmarked" if clearing else message
        self._mark(row, cursor, NEW if clearing else status, label)
        if clearing:
            # Undoing a mark should leave you looking at what you just undid.
            return
        self._advance_cursor()
        if judging_what_plays and self.player.playing:
            # You marked the track you were listening to, so listening moves on
            # with you rather than finishing something you already ruled on.
            self.action_play_step(1)

    def _advance_cursor(self) -> None:
        table = self.query_one("#tracks", DataTable)
        if self.visible_rows and table.cursor_row < len(self.visible_rows) - 1:
            table.move_cursor(row=table.cursor_row + 1)

    def action_mark_got(self) -> None:
        self._toggle_status(GOT, "Got it")

    def action_mark_skip(self) -> None:
        self._toggle_status(SKIP, "Skipped")

    def action_mark_new(self) -> None:
        if self._mark_selected(NEW, "Unmarked"):
            return
        row = self.current_row()
        if row is None:
            return
        self._mark(row, self.query_one("#tracks", DataTable).cursor_row, NEW, "Unmarked")

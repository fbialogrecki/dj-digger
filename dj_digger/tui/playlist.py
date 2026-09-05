"""Pure playlist filtering, stable sorting and target selection."""

from ..links import CATEGORY_NAMES
from ..state import GOT, NEW, OPENED, SKIP

_STATUS_RANK = {NEW: 0, OPENED: 1, SKIP: 2, GOT: 3}
SORT_KEYS = {
    "title": lambda _: (lambda row: row.track.label.lower()),
    "time": lambda _: (lambda row: row.track.duration),
    "genre": lambda _: (lambda row: row.track.genre_label.lower()),
    "status": lambda status: (lambda row: _STATUS_RANK.get(status(row), 0)),
    "store": lambda _: (
        lambda row: min(
            (CATEGORY_NAMES.index(c) for c in row.categories if c in CATEGORY_NAMES),
            default=len(CATEGORY_NAMES),
        )
    ),
    "bpm": lambda _: (lambda row: row.track.bpm or 0.0),
    "key": lambda _: (lambda row: row.track.key_signature.lower()),
    "year": lambda _: (lambda row: row.track.release_year or 0),
}

def filter_rows(rows, search, hide_handled, status):
    tokens = search.lower().split()
    return [row for row in rows
            if not (hide_handled and status(row) in (GOT, SKIP))
            and all(token in row.haystack for token in tokens)]


def sort_rows(rows, stores, sort_key, reverse, status):
    matching = [row for row in rows
                if not stores or any(cat in row.categories for cat in stores)]
    if sort_key:
        matching.sort(key=SORT_KEYS[sort_key](status), reverse=reverse)
    return matching


def operation_targets(rows, selected, *, selected_only=False):
    if selected or selected_only:
        return [row for row in rows if row.track.key in selected]
    return list(rows)

"""Pure playlist filtering, stable sorting and target selection."""

from .models import GOT, SKIP


def filter_rows(rows, search, hide_handled, status):
    tokens = search.lower().split()
    return [row for row in rows
            if not (hide_handled and status(row) in (GOT, SKIP))
            and all(token in row.haystack for token in tokens)]



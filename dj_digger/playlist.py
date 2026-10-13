"""Pure playlist filtering, stable sorting and target selection."""


def filter_rows(rows, search, statuses, status):
    """Rows matching every search token whose status is in ``statuses`` (empty keeps all)."""
    tokens = search.lower().split()
    return [row for row in rows
            if (not statuses or status(row) in statuses)
            and all(token in row.haystack for token in tokens)]



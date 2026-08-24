# Stable scrolling during downloads

## Context

Download progress is stored per track and currently displayed by rebuilding the
entire track table at most once every 80 milliseconds. `refresh_rows()` clears
the `DataTable`, adds every row again, and restores the cursor row. Clearing the
widget also resets its viewport, so a user who scrolls through a long crate is
repeatedly returned to the top while downloads are active.

## Goal

Users can freely scroll through the track list throughout single and batch
downloads. Progress, completion, and failure indicators remain current without
moving the cursor or viewport automatically.

The change does not alter download concurrency, track ordering, status
persistence, filtering, or the meaning of the existing `h` hide-handled mode.

## Chosen approach

Update an affected visible row in place through the existing `_paint_row()`
path. A progress callback changes `download_progress`, locates the matching row
in `visible_rows`, and repaints only that row. It must not call
`refresh_rows()`.

Completion and failure use the same targeted repaint whenever the set of
visible rows remains unchanged. Completion also refreshes the status summary
after persisting `got`. If a completion changes membership of the visible list,
for example because handled tracks are hidden, the table may be rebuilt, but
the rebuild preserves the current viewport as closely as the remaining content
allows.

No new timer, table abstraction, or download-state model is introduced. The
existing 80 ms progress throttle remains in place to avoid excessive painting.

## Alternatives rejected

- Rebuilding the whole table and restoring its scroll offset would preserve the
  viewport but retain unnecessary layout work and possible flicker several
  times per second.
- Reducing the refresh frequency would only make the reset less frequent while
  making progress indicators stale.

## Error and edge-case behaviour

- Progress for a track outside the current filter is recorded but performs no
  table paint. It appears correctly if the track later becomes visible.
- A row that disappears after being marked `got` is allowed to disappear; the
  viewport is clamped only when the previous offset no longer exists.
- Failed downloads remove their progress marker without changing track order or
  the user's position.
- Concurrent batch callbacks remain serialized onto Textual's UI thread as they
  are today.

## Testing

Add a Textual regression test with enough rows to require vertical scrolling.
Move the viewport away from the top, deliver multiple progress updates, and
assert that:

- the vertical scroll offset and cursor row do not change;
- the affected row displays the latest percentage;
- the table is not cleared or repopulated.

Cover completion and failure so removing the progress marker also leaves the
viewport stable when the row remains visible. Run the full offline suite and
Ruff after the targeted test passes.

## Acceptance criteria

- A user can scroll continuously during single or batch downloads.
- Progress remains visible and updates smoothly.
- Download callbacks do not move the cursor or viewport unless the visible
  result set itself can no longer contain that position.
- Existing filtering, hide-handled behaviour, and status counts keep working.

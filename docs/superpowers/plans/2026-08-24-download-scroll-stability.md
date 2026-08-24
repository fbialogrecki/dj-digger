# Download Scroll Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the track-table cursor and viewport stable while single or batch downloads update their rows.

**Architecture:** Reuse `RenderMixin._paint_row()` for download-only visual changes instead of clearing and rebuilding the `DataTable`. When a real result-set change requires `refresh_rows()`, preserve its public `scroll_offset` and restore it after repopulating the table.

**Tech Stack:** Python 3.12+, Textual `DataTable`, pytest, Ruff

---

### Task 1: Repaint progress in place

**Files:**
- Modify: `tests/test_tui.py`
- Modify: `dj_digger/tui/downloads.py:59-65`

- [ ] **Step 1: Write the failing progress regression test**

Add a test using `synthetic_records(60)` that moves the cursor to row 30,
scrolls the table to `y=20`, spies on `table.clear`, and calls
`app._update_track_progress()` for visible row 35. Assert that `clear` was not
called, `cursor_row` and `scroll_offset` are unchanged, and the title cell
contains `[42%]`.

```python
def test_download_progress_does_not_move_the_viewport(state, monkeypatch):
    app = make_app(synthetic_records(60), state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            table.move_cursor(row=30)
            table.scroll_to(y=20, animate=False, force=True, immediate=True)
            await pilot.pause()
            cursor = table.cursor_row
            viewport = table.scroll_offset
            rebuilds = []
            monkeypatch.setattr(table, "clear", lambda *a, **k: rebuilds.append(1))

            row = app.visible_rows[35]
            app._update_track_progress(row.track.key, 0.42)
            await pilot.pause()

            assert rebuilds == []
            assert table.cursor_row == cursor
            assert table.scroll_offset == viewport
            assert "[42%]" in str(table.get_row_at(35)[TITLE_CELL])

    run(scenario)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `TMPDIR=/tmp uv run pytest tests/test_tui.py::test_download_progress_does_not_move_the_viewport -q`

Expected: failure because `_update_track_progress()` calls `refresh_rows()`,
which calls the spied `table.clear()`.

- [ ] **Step 3: Add the minimal targeted-paint helper**

Add this method to `DownloadMixin` and call it from throttled progress updates:

```python
def _paint_download_row(self, key: str) -> None:
    for index, row in enumerate(self.visible_rows):
        if row.track.key == key:
            self._paint_row(index)
            return

def _update_track_progress(self, key: str, pct: float) -> None:
    self.download_progress[key] = pct
    now = time.time()
    if now - self._last_progress_redraw >= 0.08:
        self._last_progress_redraw = now
        self._paint_download_row(key)
```

- [ ] **Step 4: Run the targeted test and verify GREEN**

Run: `TMPDIR=/tmp uv run pytest tests/test_tui.py::test_download_progress_does_not_move_the_viewport -q`

Expected: `1 passed`.

### Task 2: Repaint completion and failure in place

**Files:**
- Modify: `tests/test_tui.py`
- Modify: `dj_digger/tui/downloads.py:67-164`

- [ ] **Step 1: Write failing completion and failure tests**

Add two tests following Task 1's viewport setup. Seed
`app.download_progress[key]`, call `_download_finished()` in one and
`_download_failed()` in the other, and assert the viewport did not move, the
table was not cleared, and the percentage marker disappeared. The completion
test also asserts `state.get(key) == GOT`.

- [ ] **Step 2: Run both tests and verify RED**

Run: `TMPDIR=/tmp uv run pytest tests/test_tui.py -k 'download_finished_does_not_move_the_viewport or download_failed_does_not_move_the_viewport' -q`

Expected: both fail because their callbacks call `refresh_rows()`.

- [ ] **Step 3: Route terminal download states through targeted paints**

Use `_paint_download_row(key)` after removing the progress entry. On success,
call `update_status()` after persisting `GOT`. If `hide_handled` is true, retain
`refresh_rows()` because the successful row must leave the result set. Apply the
same rules to batch success/failure. At batch completion, clear only any stale
progress entries and repaint those keys rather than rebuilding the table.

```python
def _download_finished(self, key: str, path: Path) -> None:
    self.download_progress.pop(key, None)
    self.state.set(key, GOT)
    if self.hide_handled:
        self.refresh_rows()
    else:
        self._paint_download_row(key)
        self.update_status()
    self.notify(f"Downloaded to {path}", timeout=5)
```

- [ ] **Step 4: Run the completion/failure tests and verify GREEN**

Run: `TMPDIR=/tmp uv run pytest tests/test_tui.py -k 'download_finished_does_not_move_the_viewport or download_failed_does_not_move_the_viewport' -q`

Expected: both pass.

### Task 3: Preserve the viewport for necessary rebuilds

**Files:**
- Modify: `tests/test_tui.py`
- Modify: `dj_digger/tui/render.py:136-151`

- [ ] **Step 1: Write the failing rebuild regression test**

With 60 rows, place the cursor at row 30 and viewport at `y=20`, call
`app.refresh_rows()`, pause the pilot, then assert the cursor and
`scroll_offset` match their saved values.

- [ ] **Step 2: Run the test and verify RED**

Run: `TMPDIR=/tmp uv run pytest tests/test_tui.py::test_refresh_rows_preserves_the_viewport -q`

Expected: failure because `table.clear()` resets the viewport and
`move_cursor()` scrolls only enough to reveal the restored cursor.

- [ ] **Step 3: Restore the public scroll offset after repopulation**

Capture `table.scroll_offset` only when `keep_cursor` is true. Restore the cursor
with `scroll=False`, fit the flexible column, then restore both offset axes
immediately. Calls with `keep_cursor=False` retain the existing reset-to-top
behaviour used for filters and crate switches.

```python
previous_scroll = table.scroll_offset if keep_cursor else None
# clear and repopulate
table.move_cursor(row=min(previous, len(self.visible_rows) - 1), scroll=not keep_cursor)
table.fit_flexible_column()
if previous_scroll is not None:
    table.scroll_to(
        x=previous_scroll.x,
        y=previous_scroll.y,
        animate=False,
        force=True,
        immediate=True,
    )
```

- [ ] **Step 4: Run the rebuild test and verify GREEN**

Run: `TMPDIR=/tmp uv run pytest tests/test_tui.py::test_refresh_rows_preserves_the_viewport -q`

Expected: `1 passed`.

### Task 4: Validate and commit the fix

**Files:**
- Modify: `dj_digger/tui/downloads.py`
- Modify: `dj_digger/tui/render.py`
- Modify: `tests/test_tui.py`
- Create: `docs/superpowers/plans/2026-08-24-download-scroll-stability.md`

- [ ] **Step 1: Run the download and rendering tests**

Run: `TMPDIR=/tmp uv run pytest tests/test_tui.py -q`

Expected: all TUI tests pass.

- [ ] **Step 2: Run the complete offline suite**

Run: `TMPDIR=/tmp uv run pytest`

Expected: all offline tests pass and live markers remain deselected.

- [ ] **Step 3: Run static and diff checks**

Run: `uv run ruff check . && git diff --check`

Expected: `All checks passed!` and no diff errors.

- [ ] **Step 4: Commit the implementation**

```bash
git add dj_digger/tui/downloads.py dj_digger/tui/render.py tests/test_tui.py \
  docs/superpowers/plans/2026-08-24-download-scroll-stability.md
git commit -m "fix(tui): keep scrolling stable during downloads"
```

from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable

from dj_digger import links
from dj_digger.models import LinkRecord, Track
from dj_digger.state import GOT, OPENED, SKIP, TrackState
from dj_digger.tui import DiggerApp


def run(scenario):
    """Drive an async Textual pilot from a plain sync test."""

    asyncio.run(scenario())


@pytest.fixture
def state(tmp_path):
    return TrackState(tmp_path / "state.json")


@pytest.fixture
def records(tracks):
    return links.categorise_all(tracks)


def make_app(records, state, **kwargs):
    return DiggerApp(records, state=state, crate_title="test crate", **kwargs)


def synthetic_records(count, category="bandcamp"):
    return [
        LinkRecord(
            category=category,
            track=Track(title=f"Track {index}", permalink_url=f"https://soundcloud.com/a/{index}", id=index),
            link_url=f"https://label.bandcamp.com/track/{index}",
            link_text="Buy",
        )
        for index in range(count)
    ]


def test_every_link_gets_a_row(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test():
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_marking_got_persists_and_moves_on(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("g")
            assert state.get(records[0].track.key) == GOT
            # Cursor should have advanced so you can keep hammering the key.
            assert app.query_one("#tracks", DataTable).cursor_row == 1

    run(scenario)

    # A fresh state object reads the same verdict back off disk.
    assert TrackState(state.path).get(records[0].track.key) == GOT


def test_skipping_persists(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("s")

    run(scenario)
    assert state.get(records[0].track.key) == SKIP


def test_unmarking_clears_the_status(records, state):
    state.set(records[0].track.key, GOT)
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("u")

    run(scenario)
    assert state.get(records[0].track.key) == "new"


def test_opening_a_link_marks_it_opened(records, state, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "dj_digger.tui.browser_module.open_url",
        lambda url, browser="default": opened.append(url) or True,
    )
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("o")

    run(scenario)
    assert opened == [records[0].link_url]
    assert state.get(records[0].track.key) == OPENED


def test_enter_opens_the_link_exactly_once(records, state, monkeypatch):
    """The table binds enter itself, so the app binding must not fire as well."""

    opened = []
    monkeypatch.setattr(
        "dj_digger.tui.browser_module.open_url",
        lambda url, browser="default": opened.append(url) or True,
    )
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("enter")

    run(scenario)
    assert opened == [records[0].link_url]


def test_store_filter_narrows_the_table(records, state):
    app = make_app(records, state)
    bandcamp = sum(1 for record in records if record.category == "bandcamp")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("2")  # bandcamp is the second category
            assert app.store_filter == "bandcamp"
            assert app.query_one("#tracks", DataTable).row_count == bandcamp

            await pilot.press("0")  # back to everything
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_hiding_handled_rows(records, state):
    state.set(records[0].track.key, GOT)
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("h")
            assert app.query_one("#tracks", DataTable).row_count == len(records) - 1

    run(scenario)


def test_search_filters_by_artist_and_title(records, state):
    app = make_app(records, state)
    target = records[0].track.label

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("slash")
            app.query_one("#search").value = target
            await pilot.pause()
            rows = app.query_one("#tracks", DataTable).row_count
            assert 1 <= rows < len(records)

    run(scenario)


def test_escape_clears_every_filter(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("2")
            await pilot.press("h")
            await pilot.press("escape")
            assert app.store_filter == ""
            assert app.hide_handled is False
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_open_all_asks_before_flooding_the_browser(state, monkeypatch):
    """The whole point of the TUI is not opening 282 tabs by accident."""

    opened = []
    monkeypatch.setattr(
        "dj_digger.tui.browser_module.open_urls",
        lambda urls, browser="default", **kwargs: opened.extend(urls) or len(urls),
    )
    app = make_app(synthetic_records(25), state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("a")
            assert opened == []  # first press only warns
            await pilot.press("a")
            assert len(opened) == 25

    run(scenario)


def test_open_all_goes_straight_through_for_a_short_list(state, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "dj_digger.tui.browser_module.open_urls",
        lambda urls, browser="default", **kwargs: opened.extend(urls) or len(urls),
    )
    app = make_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("a")
            assert len(opened) == 3

    run(scenario)


def test_export_writes_the_visible_rows(records, state, tmp_path):
    output = tmp_path / "view.json"
    app = make_app(records, state, export_path=output)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("2")  # bandcamp only
            await pilot.press("e")

    run(scenario)

    written = json.loads(output.read_text(encoding="utf-8"))
    assert sum(len(items) for items in written.values()) == sum(
        1 for record in records if record.category == "bandcamp"
    )

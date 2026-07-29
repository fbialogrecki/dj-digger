from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import DataTable, Input

from dj_digger import links
from dj_digger.dig import DigOptions, TargetNotFound
from dj_digger.models import Crate, LinkRecord, Track
from dj_digger.state import GOT, OPENED, SKIP, TrackState
from dj_digger.tui import AskLinkScreen, DiggerApp


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


def test_number_keys_select_the_stores_this_crate_actually_has(records, state):
    """`1` is the first store present, not a fixed category - crates differ."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            assert app.present == ["bandcamp", "others"]

            await pilot.press("1")
            assert app.store_filter == "bandcamp"
            expected = sum(1 for record in records if record.category == "bandcamp")
            assert app.query_one("#tracks", DataTable).row_count == expected

            await pilot.press("2")
            assert app.store_filter == "others"

            await pilot.press("0")
            assert app.store_filter == ""
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_a_number_key_beyond_the_stores_present_is_a_no_op(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("9")
            assert app.store_filter == ""

    run(scenario)


def test_cycling_walks_only_the_stores_present(records, state):
    """With a dozen possible categories, cycling through the empty ones is useless."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("f")
            assert app.store_filter == "bandcamp"
            await pilot.press("f")
            assert app.store_filter == "others"
            await pilot.press("f")  # wraps back to everything
            assert app.store_filter == ""
            await pilot.press("F")  # and backwards
            assert app.store_filter == "others"

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


def crate_of(count, *, title="Fresh crate"):
    return Crate(
        source="https://soundcloud.com/a/sets/b",
        title=title,
        declared_count=count,
        tracks=[
            Track(
                title=f"Dug {index}",
                permalink_url=f"https://soundcloud.com/a/{index}",
                id=1000 + index,
                purchase_url=f"https://label.bandcamp.com/track/{index}",
            )
            for index in range(count)
        ],
    )


async def settle(app, pilot):
    """Wait for the background dig to land and the UI to catch up."""

    await app.workers.wait_for_complete()
    await pilot.pause()


def test_an_empty_app_asks_for_a_link(state):
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, AskLinkScreen)

    run(scenario)


def test_entering_a_link_fills_the_table(state, monkeypatch, tmp_path):
    monkeypatch.setattr("dj_digger.dig.dig", lambda target, **kwargs: crate_of(3))
    app = make_app([], state, export_path=tmp_path / "out.json")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#ask-input", Input).value = "https://soundcloud.com/a/sets/b"
            await pilot.press("enter")
            await settle(app, pilot)

            assert app.query_one("#tracks", DataTable).row_count == 3
            assert app.present == ["bandcamp"]
            assert app.sub_title == "Fresh crate"

    run(scenario)

    # A dig started from inside the browser still writes the export.
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["bandcamp"]


def test_the_target_is_passed_through_with_the_dig_options(state, monkeypatch):
    seen = {}

    def fake_dig(target, **kwargs):
        seen["target"] = target
        seen["kwargs"] = kwargs
        return crate_of(1)

    monkeypatch.setattr("dj_digger.dig.dig", fake_dig)
    app = make_app([], state, dig_options=DigOptions(limit=7, timeout=5.0, delay=0.0))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#ask-input", Input).value = "playlist.html"
            await pilot.press("enter")
            await settle(app, pilot)

    run(scenario)
    assert seen["target"] == "playlist.html"
    assert seen["kwargs"]["limit"] == 7
    assert seen["kwargs"]["timeout"] == 5.0


def test_cancelling_with_nothing_loaded_quits(state, monkeypatch):
    app = make_app([], state)
    exited = []
    monkeypatch.setattr(app, "exit", lambda *a, **k: exited.append(True))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    run(scenario)
    assert exited == [True]


def test_cancelling_keeps_a_crate_you_already_have(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, AskLinkScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_digging_a_second_link_replaces_the_crate(records, state, monkeypatch):
    monkeypatch.setattr("dj_digger.dig.dig", lambda target, **kwargs: crate_of(2, title="Second"))
    app = make_app(records, state, export_format="none")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("d")
            await pilot.pause()
            app.screen.query_one("#ask-input", Input).value = "https://soundcloud.com/a/sets/c"
            await pilot.press("enter")
            await settle(app, pilot)

            assert app.query_one("#tracks", DataTable).row_count == 2
            assert app.sub_title == "Second"

    run(scenario)


def test_a_failed_dig_reports_and_asks_again(state, monkeypatch):
    def boom(target, **kwargs):
        raise TargetNotFound("nope.html")

    monkeypatch.setattr("dj_digger.dig.dig", boom)
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#ask-input", Input).value = "nope.html"
            await pilot.press("enter")
            await settle(app, pilot)

            # Back at the prompt rather than dead or stuck on a spinner.
            assert isinstance(app.screen, AskLinkScreen)
            assert app._digging is False

    run(scenario)


def test_a_crate_with_no_tracks_is_treated_as_a_failure(state, monkeypatch):
    monkeypatch.setattr("dj_digger.dig.dig", lambda target, **kwargs: crate_of(0))
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#ask-input", Input).value = "https://soundcloud.com/a/sets/empty"
            await pilot.press("enter")
            await settle(app, pilot)
            assert isinstance(app.screen, AskLinkScreen)

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

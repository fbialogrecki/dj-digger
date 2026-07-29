from __future__ import annotations

import asyncio
import json

import pytest
from textual.widgets import Button, DataTable, Input, ListView, Static

from dj_digger import library, links
from dj_digger import tui
from dj_digger.dig import DigOptions, TargetNotFound
from dj_digger.models import Crate, LinkRecord, Track
from dj_digger.state import GOT, OPENED, SKIP, TrackState
from dj_digger.tui import AskLinkScreen, ConfirmScreen, DiggerApp, HelpScreen


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


def test_help_documents_every_key(records, state):
    """The footer only shows a handful, so help must not drift from the keymap."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)

            text = str(app.screen.query_one(Static).render())
            for _key, _action, label, _group, _show in tui.KEYMAP:
                assert label in text
            for section in (tui.SELECTED, tui.WHOLE_LIST, tui.CRATES, tui.OTHER):
                assert section in text

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

    run(scenario)


def test_the_command_palette_is_off(records, state):
    """It showed up in the footer as an unexplained 'palette'."""

    assert make_app(records, state).ENABLE_COMMAND_PALETTE is False


def test_the_status_bar_carries_the_crate_name(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test():
            assert "test crate" in str(app.query_one("#status", Static).render())

    run(scenario)


def test_bars_sit_below_the_table(records, state):
    """Both info bars belong at the bottom, under the table and sidebar."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test():
            order = [
                widget.id
                for widget in app.screen.children
                if widget.id in {"body", "status", "stores"}
            ]
            assert order == ["body", "status", "stores"]

    run(scenario)


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


def crate_of(count, *, title="Fresh crate", source="https://soundcloud.com/a/sets/b"):
    return Crate(
        source=source,
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


def saved_crate(count=3, *, source="https://soundcloud.com/a/sets/saved", title="Saved crate"):
    record = library.CrateRecord.from_crate(
        Crate(
            source=source,
            title=title,
            tracks=[
                Track(
                    title=f"Kept {index}",
                    permalink_url=f"https://soundcloud.com/a/k{index}",
                    id=500 + index,
                    purchase_url=f"https://label.bandcamp.com/track/k{index}",
                )
                for index in range(count)
            ],
        )
    )
    library.save(record)
    return record


def test_the_sidebar_lists_saved_crates(state):
    saved_crate(title="Alpha")
    saved_crate(source="https://soundcloud.com/a/sets/two", title="Beta")
    app = make_app([], state)

    async def scenario():
        async with app.run_test():
            assert [record.title for record in app.crates] == ["Alpha", "Beta"]
            assert app.query_one("#crates", ListView).children

    run(scenario)


def test_a_library_is_opened_instead_of_being_asked_for_a_link(state):
    """Someone with saved crates wants to see them, not be interrogated."""

    saved_crate(3, title="Already here")
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not isinstance(app.screen, AskLinkScreen)
            assert app.crate is not None and app.crate.title == "Already here"
            assert app.query_one("#tracks", DataTable).row_count == 3

    run(scenario)


def test_selecting_a_crate_switches_to_it(state):
    saved_crate(2, source="https://soundcloud.com/a/sets/one", title="One")
    saved_crate(4, source="https://soundcloud.com/a/sets/two", title="Two")
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            listing = app.query_one("#crates", ListView)
            listing.index = 1
            listing.action_select_cursor()
            await pilot.pause()

            assert app.crate.title == "Two"
            assert app.query_one("#tracks", DataTable).row_count == 4

    run(scenario)


def test_refreshing_redigs_the_saved_source_and_keeps_deletions(state, monkeypatch):
    record = saved_crate(3)
    record.remove("501")
    library.save(record)

    # Like the real dig, which reports back the source it was given.
    monkeypatch.setattr(
        "dj_digger.dig.dig",
        lambda target, **kwargs: crate_of(4, title="Refreshed", source=target),
    )
    app = make_app([], state, export_format="none")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            await settle(app, pilot)

    run(scenario)

    reloaded = library.load(record.slug)
    assert reloaded.refreshed_at
    assert len(reloaded.tracks) == 4
    assert reloaded.removed_track_keys == ["501"]


def test_deleting_a_crate_asks_first(state):
    record = saved_crate(2)
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("X")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("n")
            await pilot.pause()

    run(scenario)
    assert library.load(record.slug).title == "Saved crate"


def test_confirming_deletes_the_crate(state):
    saved_crate(2)
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("X")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()

    run(scenario)
    assert library.list_crates() == []


def test_the_sidebar_collapses(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            sidebar = app.query_one("#sidebar")
            assert not sidebar.has_class("collapsed")
            await pilot.press("ctrl+b")
            assert sidebar.has_class("collapsed")
            await pilot.press("ctrl+b")
            assert not sidebar.has_class("collapsed")

    run(scenario)


@pytest.mark.parametrize(
    "button_id,expected_action",
    [
        ("crate-add", "action_dig_link"),
        ("crate-refresh", "action_refresh_crate"),
        ("crate-delete", "action_delete_crate"),
    ],
)
def test_each_sidebar_button_runs_its_action(records, state, monkeypatch, button_id, expected_action):
    """Buttons and keys must trigger the same thing, or one of them rots."""

    app = make_app(records, state)
    called = []
    monkeypatch.setattr(app, expected_action, lambda: called.append(expected_action))

    async def scenario():
        async with app.run_test() as pilot:
            app.query_one(f"#{button_id}", Button).press()
            await pilot.pause()

    run(scenario)
    assert called == [expected_action]


def test_a_dig_lands_in_the_library(state, monkeypatch):
    monkeypatch.setattr("dj_digger.dig.dig", lambda target, **kwargs: crate_of(2, title="Dug"))
    app = make_app([], state, export_format="none")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.query_one("#ask-input", Input).value = "https://soundcloud.com/a/sets/new"
            await pilot.press("enter")
            await settle(app, pilot)

    run(scenario)

    crates = library.list_crates()
    assert [record.title for record in crates] == ["Dug"]
    assert len(crates[0].tracks) == 2


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

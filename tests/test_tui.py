import asyncio
import io
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from types import SimpleNamespace

import pytest
from rich.console import Console
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Input, Label, ListView, Static

from dj_digger import cart, gates, library, links, soundcloud, tui
from dj_digger.dig import DigOptions, TargetNotFound
from dj_digger.models import Crate, LinkRecord, Track
from dj_digger.player import Loaded, PlaybackUnavailable, PlayerBar, Stream
from dj_digger.scanner import LocalMatch
from dj_digger.state import GOT, OPENED, SKIP, TrackState
from dj_digger.tui import (
    AskLinkScreen,
    ConfirmScreen,
    ContextMenuScreen,
    DiggerApp,
    ErrorBanner,
    GateProfileScreen,
    HelpScreen,
    SettingsScreen,
    SoundCloudAuthScreen,
)


def run(scenario):
    """Drive an async Textual pilot from a plain sync test."""

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "group"),
    [
        (gates.GateAuthenticationRequired("Spotify"), "auth"),
        (gates.GateCaptchaRequired("captcha"), "captcha"),
        (gates.GateManualActionRequired("future"), "manual"),
        (gates.GateProtocolChanged("changed"), "protocol"),
        (gates.GateRejected("rejected"), "rejected"),
        (gates.GateDownloadError("download"), "download"),
        (soundcloud.SoundCloudError("download"), "download"),
    ],
)
def test_batch_gate_failures_have_actionable_summary_groups(error, group):
    from dj_digger.tui.downloads import _gate_failure_group

    assert _gate_failure_group(error) == group


def test_error_banner_keeps_messages_literal_and_has_a_working_x(state):
    app = make_app(synthetic_records(1), state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            banner = app.query_one(ErrorBanner)
            banner.add_error("Batch failed [Artist - Track]: bad [response]")
            for index in range(12):
                banner.add_error(f"Failure {index}: " + "long message " * 8)
            await pilot.pause()

            close = app.query_one("#error-close", Button)
            message = app.query_one("#error-text", Static)
            assert str(close.label) == "X"
            assert "[Artist - Track]" in str(message.render())
            assert banner.size.height <= 12

            await pilot.click("#error-close")
            await pilot.pause()
            assert banner.errors == []
            assert not banner.has_class("visible")

    run(scenario)


def test_batch_summary_toast_renders_literal_failure_groups(state):
    app = make_app(synthetic_records(1), state)

    async def scenario():
        async with app.run_test(notifications=True) as pilot:
            app._on_batch_download_complete(
                1,
                5,
                6,
                failure_groups={"manual": 2, "download": 2, "other": 1},
            )
            toasts = []
            for _ in range(20):
                await pilot.pause(0.05)
                toasts = list(app.query("Toast"))
                if toasts:
                    break
            assert toasts
            rendered = str(toasts[-1].render())
            assert "manual=2" in rendered
            assert "download=2" in rendered
            assert "other=1" in rendered

    run(scenario)


async def scroll_table(pilot, table, y):
    """Wait for Textual to size the table before setting a test viewport."""

    deadline = asyncio.get_running_loop().time() + 5
    await pilot.pause()
    while table.max_scroll_y <= 0:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("table never became scrollable")
        await pilot.pause(0.01)
    target = min(y, table.max_scroll_y)
    table.call_after_refresh(
        table.scroll_to,
        y=target,
        animate=False,
        force=True,
        immediate=True,
    )
    await pilot.pause()
    while table.scroll_offset.y != target:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"table never reached scroll offset {target}")
        await pilot.pause(0.01)
    await pilot.pause()
    assert table.scroll_offset.y == target


def bar_text(app, width=200):
    """The bottom bar as plain text - it is a Rich grid, not a bare string."""

    # force_terminal, or Rich clamps a non-tty to 80 columns whatever we ask for.
    console = Console(width=width, file=io.StringIO(), force_terminal=True)
    console.print(app.query_one("#status", Static).content)
    return console.file.getvalue()


# Cell offsets into a table row: playing, mark, number, title, stores, genre, time.
MARK_CELL = 1
TITLE_CELL = 3
STORES_CELL = 4
GENRE_CELL = 5
TIME_CELL = 6


@pytest.fixture
def state(tmp_path):
    return TrackState(tmp_path / "state.json")


@pytest.fixture
def records(tracks):
    return links.categorise_all(tracks)


def make_app(records, state, **kwargs):
    return DiggerApp(records, state=state, crate_title="test crate", **kwargs)


def synthetic_records(count, category="bandcamp"):
    """Ids run from 1: SoundCloud has no track 0, and 0 reads as "no id at all"."""

    return [
        LinkRecord(
            category=category,
            track=Track(
                title=f"Track {index}",
                permalink_url=f"https://soundcloud.com/a/{index}",
                id=index + 1,
            ),
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
            for _key, _action, _label, _group, _show, detail in tui.KEYMAP:
                assert detail in text
            for section in (tui.SELECTED, tui.WHOLE_LIST, tui.CRATES, tui.OTHER):
                assert section in text

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

    run(scenario)


def test_the_command_palette_is_off(records, state):
    """It showed up in the footer as an unexplained 'palette'."""

    assert make_app(records, state).ENABLE_COMMAND_PALETTE is False


def test_the_bottom_bar_pairs_the_stores_with_your_progress(records, state):
    """One bar, not two stacked ones: the legend left, how far you are right."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test(size=(160, 24)) as pilot:
            await pilot.pause()
            text = bar_text(app)
            assert text.count("\n") == 1
            assert "0 all" in text
            assert "got 0" in text
            # The sidebar says which crate this is, so the bar does not repeat it.
            assert "test crate" not in text

    run(scenario)


def test_the_bar_stays_one_line_and_drops_the_counts_when_cramped(records, state):
    """Wrapping would grow it back into the stack of bars it replaced."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test(size=(160, 24)) as pilot:
            await pilot.pause()
            bar = app.query_one("#status", Static)
            assert bar.size.height == 1
            assert "got 0" in bar_text(app)

            await pilot.resize_terminal(60, 24)
            await pilot.pause()
            assert bar.size.height == 1
            # The legend documents the number keys, so the counts are what goes.
            text = bar_text(app, width=60)
            assert "0 all" in text
            assert "got 0" not in text

    run(scenario)


def test_the_bar_sits_below_the_table(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test():
            order = [
                widget.id
                for widget in app.screen.children
                if widget.id in {"body", "status"}
            ]
            assert order == ["body", "status"]

    run(scenario)


def test_every_track_gets_a_row(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test():
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_a_track_in_two_stores_is_still_one_row(state):
    """Buying it on Bandcamp or earning it on a gate is one decision, not two."""

    track = Track(
        title="Everywhere",
        permalink_url="https://soundcloud.com/a/b",
        id=77,
        purchase_url="https://hypeddit.com/x/y",
        description="also at https://label.bandcamp.com/album/x",
    )
    records = links.categorise_all([track])
    assert len(records) == 2
    app = make_app(records, state)

    async def scenario():
        async with app.run_test():
            assert app.query_one("#tracks", DataTable).row_count == 1
            assert app.rows[0].categories == ["bandcamp", "gate"]

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


@pytest.mark.parametrize("key,status", [("s", SKIP), ("g", GOT)])
def test_pressing_the_same_mark_again_clears_it(records, state, key, status):
    """Reaching for s again to unskip is what people actually try."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", DataTable)
            await pilot.press(key)
            assert state.get(records[0].track.key) == status
            assert table.cursor_row == 1

            table.move_cursor(row=0)
            await pilot.press(key)
            assert state.get(records[0].track.key) == "new"
            # Undoing should not march the cursor onwards.
            assert table.cursor_row == 0

    run(scenario)


def test_a_crate_name_with_brackets_survives(state):
    """Label renders Textual markup, so [2026] would vanish from the sidebar."""

    saved_crate(1, source="https://soundcloud.com/a/sets/b", title="Techno [2026] vinyl")
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            label = app.query_one(".crate-name", Label)
            assert "[2026]" in str(label.render())

    run(scenario)


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


def test_single_click_only_selects_the_track(records, state, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "dj_digger.tui.browser_module.open_url",
        lambda url, browser="default": opened.append(url) or True,
    )
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#tracks", offset=(10, 1))
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).cursor_row == 0
            assert opened == []
            assert state.get(records[0].track.key) != OPENED

            await pilot.press("enter")

    run(scenario)
    assert opened == [records[0].link_url]


def test_right_click_opens_the_track_menu_without_opening_a_link(
    state, monkeypatch
):
    opened = []
    monkeypatch.setattr(
        "dj_digger.tui.browser_module.open_url",
        lambda url, browser="default": opened.append(url) or True,
    )
    records = synthetic_records(2)
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#tracks", offset=(10, 2), button=3)
            await pilot.pause()
            assert isinstance(app.screen, ContextMenuScreen)
            assert "Track 1" in str(app.screen.query_one(Label).render())
            assert opened == []

            await pilot.press("enter")
            await pilot.pause()

    run(scenario)
    assert opened == [records[1].link_url]


def cart_plan_for(record, *, already=False):
    return cart.CartPlan(
        items=(
            cart.CartItem(
                track_key=record.track.key,
                track_label=record.track.label,
                store=record.category,
                source_url=record.link_url,
                product_url=record.link_url,
                product_id="123",
                product_title=record.track.title,
                price=Decimal("1.25"),
                currency="GBP",
                already_in_cart=already,
            ),
        )
    )


def test_c_preflights_and_adds_the_selected_track(records, state, monkeypatch):
    bandcamp_record = next(record for record in records if record.category == "bandcamp")
    prepared = []
    executed = []

    def prepare(requests, _cancel):
        requests = list(requests)
        prepared.extend(requests)
        return cart_plan_for(bandcamp_record)

    def execute(plan, _cancel, **_kwargs):
        executed.append(plan)
        return (
            cart.CartResult(
                bandcamp_record.track.key,
                bandcamp_record.track.label,
                "bandcamp",
                "added",
            ),
        )

    monkeypatch.setattr(cart, "prepare_cart", prepare)
    monkeypatch.setattr(cart, "execute_cart", execute)
    app = make_app([bandcamp_record], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("c")
            for _ in range(10):
                await pilot.pause()
                if executed:
                    break

    run(scenario)
    assert prepared[0].links == (("bandcamp", bandcamp_record.link_url),)
    assert len(executed) == 1


def test_c_installs_missing_chromium_then_retries_preflight(records, state, monkeypatch):
    bandcamp_record = next(record for record in records if record.category == "bandcamp")
    installed = []
    prepared = []
    executed = []

    def prepare(requests, _cancel):
        prepared.append(list(requests))
        if not installed:
            raise cart.ChromiumMissing("Chromium is required for store carts")
        return cart_plan_for(bandcamp_record)

    def install(_cancel):
        installed.append(True)

    monkeypatch.setattr(cart, "prepare_cart", prepare)
    monkeypatch.setattr(cart, "install_chromium", install)
    monkeypatch.setattr(
        cart,
        "execute_cart",
        lambda plan, _cancel, **_kwargs: executed.append(plan)
        or (
            cart.CartResult(
                bandcamp_record.track.key,
                bandcamp_record.track.label,
                "bandcamp",
                "added",
            ),
        ),
    )
    app = make_app([bandcamp_record], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("c")
            for _ in range(20):
                await pilot.pause()
                if isinstance(app.screen, ConfirmScreen):
                    break
            assert isinstance(app.screen, ConfirmScreen)
            assert "Chromium" in str(app.screen.query_one(Label).render())
            await pilot.press("y")
            for _ in range(30):
                await pilot.pause()
                if executed:
                    break

    run(scenario)
    assert installed == [True]
    assert len(prepared) == 2
    assert len(executed) == 1


def test_shift_c_confirms_the_visible_preflight_before_mutating(state, monkeypatch):
    record = synthetic_records(1)[0]
    plan = cart_plan_for(record)
    executed = []
    monkeypatch.setattr(cart, "prepare_cart", lambda _requests, _cancel: plan)
    monkeypatch.setattr(
        cart,
        "execute_cart",
        lambda candidate, _cancel, **_kwargs: executed.append(candidate)
        or (cart.CartResult(record.track.key, record.track.label, "bandcamp", "added"),),
    )
    app = make_app([record], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("C")
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, ConfirmScreen):
                    break
            assert isinstance(app.screen, ConfirmScreen)
            assert executed == []
            assert "GBP 1.25" in str(app.screen.query_one(Label).render())
            await pilot.press("y")
            for _ in range(10):
                await pilot.pause()
                if executed:
                    break

    run(scenario)
    assert executed == [plan]


def test_batch_cart_refuses_a_filter_without_supported_stores(state, monkeypatch):
    record = synthetic_records(1)[0]
    monkeypatch.setattr(
        cart,
        "prepare_cart",
        lambda *_args, **_kwargs: pytest.fail("unsupported filter must not open a browser"),
    )
    app = make_app([record], state)
    app.store_filters = {"gate"}

    app.action_cart_visible()

    assert app._cart_busy is False


def test_cart_request_keeps_bandcamp_first_when_both_store_filters_are_active(state):
    track = Track(
        title="Signal",
        permalink_url="https://soundcloud.com/a/signal",
        id=42,
        purchase_url="https://label.bandcamp.com/album/release",
        description="https://www.beatport.com/release/release/99",
    )
    app = make_app(links.categorise(track), state)
    app.store_filters = {"beatport", "bandcamp"}

    request = app._cart_request(app.rows[0])

    assert [store for store, _url in request.links] == ["bandcamp", "beatport"]


def test_batch_cart_leaves_got_and_skipped_tracks_out(state, monkeypatch):
    records = synthetic_records(3)
    state.set(records[0].track.key, GOT)
    state.set(records[1].track.key, SKIP)
    seen = []

    def prepare(requests, _cancel):
        seen.extend(requests)
        return cart.CartPlan(
            results=(
                cart.CartResult(records[2].track.key, records[2].track.label, "bandcamp", "skipped"),
            )
        )

    monkeypatch.setattr(cart, "prepare_cart", prepare)
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("C")
            for _ in range(10):
                await pilot.pause()
                if seen:
                    break

    run(scenario)
    assert [item.track.key for item in seen] == [records[2].track.key]


def test_number_keys_select_the_stores_this_crate_actually_has(records, state):
    """`1` is the first store present, not a fixed category - crates differ."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            assert app.present == ["no-link", "bandcamp", "others"]

            await pilot.press("2")
            assert app.store_filters == {"bandcamp"}
            expected = sum(1 for record in records if record.category == "bandcamp")
            assert app.query_one("#tracks", DataTable).row_count == expected

            await pilot.press("3")
            assert app.store_filters == {"bandcamp", "others"}

            await pilot.press("0")
            assert app.store_filters == set()
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_a_number_key_beyond_the_stores_present_is_a_no_op(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("9")
            assert app.store_filters == set()

    run(scenario)


def test_cycling_walks_only_the_stores_present(records, state):
    """With a dozen possible categories, cycling through the empty ones is useless."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("f")
            assert app.store_filters == {"no-link"}
            await pilot.press("f")
            assert app.store_filters == {"bandcamp"}
            await pilot.press("f")
            assert app.store_filters == {"others"}
            await pilot.press("f")  # wraps back to everything
            assert app.store_filters == set()
            await pilot.press("F")  # and backwards
            assert app.store_filters == {"others"}

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
            assert app.store_filters == set()
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
        # Wide, or the narrow-terminal rule below would have collapsed it first.
        async with app.run_test(size=(140, 42)) as pilot:
            sidebar = app.query_one("#sidebar")
            assert not sidebar.has_class("collapsed")
            await pilot.press("ctrl+b")
            assert sidebar.has_class("collapsed")
            await pilot.press("ctrl+b")
            assert not sidebar.has_class("collapsed")

    run(scenario)


def test_a_narrow_terminal_collapses_the_sidebar_by_itself(records, state):
    """28 of 80 columns on crate names costs the title and the right-hand columns."""

    app = make_app(records, state)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            sidebar = app.query_one("#sidebar")
            assert sidebar.has_class("collapsed")
            await pilot.resize_terminal(140, 42)
            await pilot.pause()
            assert not sidebar.has_class("collapsed")

    run(scenario)


def test_the_add_button_digs(records, state, monkeypatch):
    app = make_app(records, state)
    called = []
    monkeypatch.setattr(app, "action_dig_link", lambda: called.append("dig"))

    async def scenario():
        async with app.run_test() as pilot:
            app.query_one("#crate-add", Button).press()
            await pilot.pause()

    run(scenario)
    assert called == ["dig"]


def test_the_add_button_sits_under_the_last_crate(state):
    """Not pinned to the bottom of the sidebar - it belongs with the list."""

    saved_crate(1, source="https://soundcloud.com/a/sets/one", title="One")
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            listing = app.query_one("#crates", ListView)
            add = app.query_one("#crate-add", Button)
            assert add.region.y == listing.region.y + listing.region.height

    run(scenario)


@pytest.mark.parametrize("intent,expected", [("refresh", "refresh_crate"), ("delete", "confirm_delete_crate")])
def test_each_crate_row_carries_its_own_buttons(state, monkeypatch, intent, expected):
    """Icons act on the crate in that row, not on whatever is highlighted."""

    saved_crate(1, source="https://soundcloud.com/a/sets/one", title="One")
    target = saved_crate(1, source="https://soundcloud.com/a/sets/two", title="Two")
    app = make_app([], state)
    called = []
    monkeypatch.setattr(app, expected, lambda record: called.append(record.title))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            items = list(app.query(tui.CrateItem))
            assert len(items) == 2
            # Icons only exist on the row you are pointing at.
            app.query_one("#crates", ListView).index = 1
            await pilot.pause()
            button = next(
                child
                for child in items[1].children
                if isinstance(child, tui.CrateButton) and child.intent == intent
            )
            button.press()
            await pilot.pause()

    run(scenario)
    assert called == [target.title]


def test_crate_icons_keep_out_of_the_way_of_the_name(state):
    """Six columns of icons on every row is six columns the names need more."""

    saved_crate(1, source="https://soundcloud.com/a/sets/one", title="One")
    saved_crate(1, source="https://soundcloud.com/a/sets/two", title="Two")
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#crates", ListView).index = 0
            await pilot.pause()
            items = list(app.query(tui.CrateItem))
            shown = [
                [child.display for child in item.children if isinstance(child, tui.CrateButton)]
                for item in items
            ]
            assert shown == [[True, True], [False, False]]

    run(scenario)


def test_a_long_crate_name_is_trimmed_rather_than_wrapped_away(state):
    """height: 1 means a wrapped name loses its second half entirely."""

    saved_crate(1, source="https://soundcloud.com/a/sets/x", title="Hard Techno Ressurection")
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            label = app.query_one(".crate-name", Label)
            assert label.content.no_wrap is True
            assert label.content.overflow == "ellipsis"
            # The full name is still reachable, just not all at once.
            assert label.tooltip == "Hard Techno Ressurection"

    run(scenario)


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


def test_removing_a_track_persists_and_undo_brings_it_back(state):
    record = saved_crate(3)
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).row_count == 3

            await pilot.press("x")
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).row_count == 2
            assert library.load(record.slug).removed_track_keys == ["500"]

            await pilot.press("ctrl+z")
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).row_count == 3
            assert library.load(record.slug).removed_track_keys == []

    run(scenario)


def test_removing_keeps_the_active_filter(state):
    saved_crate(3)
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("1")  # bandcamp, the only store here
            assert app.store_filters == {"bandcamp"}
            await pilot.press("x")
            await pilot.pause()
            # A removal must not reset what you filtered down to.
            assert app.store_filters == {"bandcamp"}
            assert app.query_one("#tracks", DataTable).row_count == 2

    run(scenario)


def test_removing_without_a_saved_crate_is_refused_not_a_crash(records, state):
    app = make_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            assert app.crate is None
            await pilot.press("x")
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).row_count == len(records)

    run(scenario)


def test_undo_with_nothing_removed_is_harmless(state):
    saved_crate(2)
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+z")
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).row_count == 2

    run(scenario)


def test_the_genre_column_shows_genre_then_tag_then_nothing(state):
    tracks = [
        Track(title="A", permalink_url="u/a", id=1, genre="Techno", purchase_url="https://l.bandcamp.com/a"),
        Track(title="B", permalink_url="u/b", id=2, tags=["Acid"], purchase_url="https://l.bandcamp.com/b"),
        Track(title="C", permalink_url="u/c", id=3, purchase_url="https://l.bandcamp.com/c"),
    ]
    app = make_app(links.categorise_all(tracks), state)

    async def scenario():
        async with app.run_test():
            table = app.query_one("#tracks", DataTable)
            genres = [str(table.get_row_at(index)[GENRE_CELL]) for index in range(3)]
            assert genres == ["Techno", "Acid", "-"]

    run(scenario)


def test_the_time_column_reads_as_minutes_and_seconds(state):
    tracks = [
        Track(title="A", permalink_url="u/a", id=1, duration=254_000),
        Track(title="B", permalink_url="u/b", id=2),
    ]
    app = make_app(links.categorise_all(tracks), state)

    async def scenario():
        async with app.run_test():
            table = app.query_one("#tracks", DataTable)
            assert [str(table.get_row_at(index)[TIME_CELL]) for index in range(2)] == ["4:14", "-"]

    run(scenario)


def test_the_store_column_badges_every_store_and_picks_out_the_one_o_opens(state):
    track = Track(
        title="Everywhere",
        permalink_url="https://soundcloud.com/a/b",
        id=5,
        purchase_url="https://hypeddit.com/x/y",
        description="also at https://label.bandcamp.com/album/x",
    )
    app = make_app(links.categorise_all([track]), state)

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", DataTable)
            # One character over the column, so it arrives elided rather than
            # clipped by the table into "gate(hypedd".
            assert str(table.get_row_at(0)[STORES_CELL]) == "bandcamp gate(hypeddi…"
            # Bandcamp comes first, so that is what o would follow.
            assert app.record_to_open(app.rows[0]).category == "bandcamp"

            # Filtering to the gate is how you say you want the gate instead.
            await pilot.press("2")
            assert app.store_filters == {"gate"}
            assert app.record_to_open(app.rows[0]).category == "gate"

    run(scenario)


def test_a_free_soundcloud_download_is_badged_and_opened_first(state):
    track = Track(
        title="Handed out",
        permalink_url="https://soundcloud.com/a/b",
        id=6,
        downloadable=True,
        has_downloads_left=True,
        download_url="https://api-v2.soundcloud.com/tracks/6/download",
        purchase_url="https://label.bandcamp.com/album/x",
    )
    app = make_app(links.categorise_all([track]), state)

    async def scenario():
        async with app.run_test():
            table = app.query_one("#tracks", DataTable)
            assert str(table.get_row_at(0)[STORES_CELL]) == "\u2193soundcloud bandcamp"
            chosen = app.record_to_open(app.rows[0])
            assert chosen.category == "soundcloud"
            assert chosen.link_url == track.download_url

    run(scenario)


def test_shops_and_others_are_badged_with_their_domain(state):
    """"others" as a word says nothing; the domain is the only identification."""

    tracks = [
        Track(title="A", permalink_url="u/a", id=1, purchase_url="https://www.nofu.de/redirect/?r=X"),
        Track(title="B", permalink_url="u/b", id=2, purchase_url="https://boomkat.com/products/x"),
    ]
    app = make_app(links.categorise_all(tracks), state)

    async def scenario():
        async with app.run_test():
            table = app.query_one("#tracks", DataTable)
            badges = [str(table.get_row_at(index)[STORES_CELL]) for index in range(2)]
            assert badges == ["nofu.de", "boomkat.com"]

    run(scenario)


def test_the_status_bar_counts_tracks_not_links(state):
    track = Track(
        title="Everywhere",
        permalink_url="https://soundcloud.com/a/b",
        id=7,
        purchase_url="https://hypeddit.com/x/y",
        description="also at https://label.bandcamp.com/album/x",
    )
    app = make_app(links.categorise_all([track]), state)

    async def scenario():
        async with app.run_test(size=(160, 24)) as pilot:
            await pilot.pause()
            assert "1/1 tracks" in bar_text(app)

    run(scenario)


def test_the_title_column_takes_the_width_left_over(state):
    """A fixed title column left half the terminal empty and cut the titles."""

    app = make_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test(size=(160, 24)) as pilot:
            await pilot.pause()
            table = app.query_one("#tracks", tui.TrackTable)
            spent = sum(column.get_render_width(table) for column in table.columns.values())
            assert spent == table.size.width
            assert table.columns[table.flexible_column].width > tui.MIN_TITLE_WIDTH

    run(scenario)


def test_an_80_column_terminal_needs_no_horizontal_scrollbar(state):
    """Genre and Time used to sit off the right edge behind a scrollbar."""

    app = make_app(synthetic_records(60), state)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            table = app.query_one("#tracks", tui.TrackTable)
            # Enough rows for a vertical scrollbar, whose two columns the title
            # has to leave alone.
            assert table.show_vertical_scrollbar
            assert not table.show_horizontal_scrollbar

    run(scenario)


def test_the_footer_drops_keys_rather_than_cutting_one_in_half(state):
    """Thirteen bindings want 161 columns; the row is as wide as the terminal."""

    app = make_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            # The footer builds its keys on mount and rebuilds them whenever
            # focus moves, so one pause is not a guarantee that they exist yet -
            # on a slow runner this read an empty set and asserted nothing.
            keys = []
            for _ in range(20):
                await pilot.pause()
                keys = [key for key in app.query("FooterKey") if key.display]
                if keys:
                    break
            assert keys, "the footer never composed"

            spent = sum(len(k.key_display) + len(k.description) + 3 for k in keys)
            assert spent <= 80
            # The ones you cannot do without survive the cut.
            shown = {key.action for key in keys}
            assert {"open_link", "play_pause", "help", "quit"} <= shown

    run(scenario)


def test_folding_the_sidebar_gives_the_title_more_room(state):
    app = make_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test(size=(160, 24)) as pilot:
            await pilot.pause()
            table = app.query_one("#tracks", tui.TrackTable)
            before = table.columns[table.flexible_column].width
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert table.columns[table.flexible_column].width > before

    run(scenario)


def a_stream(duration=300.0):
    return Stream(url="https://cdn/x.mp3", waveform_url="https://wave/x.json", duration=duration)


class FakePlayer:
    """The slice of Player the TUI leans on, without touching a sound card."""

    def __init__(self):
        self.loaded = None
        self.playing = False
        self.position = 0.0
        self.duration = 300.0
        self.fraction = 0.0
        self.volume = 0.8
        self.seeks = []
        self.closed = False
        self.muted = False
        self.finished = False
        self.level = 0.0

    def take_level(self):
        return self.level

    def take_finished(self):
        finished, self.finished = self.finished, False
        return finished

    def load(self, track, stream, session, waveform=None, source=None):
        self.loaded = SimpleNamespace(
            track=track, stream=stream, duration=self.duration, waveform=waveform or []
        )
        self.source = source
        return self.loaded

    def play(self):
        self.playing = True

    def pause(self):
        self.playing = False

    def toggle(self):
        self.playing = not self.playing

    def stop(self):
        self.playing = False

    def seek(self, seconds):
        self.seeks.append(seconds)
        self.position = seconds

    def nudge(self, seconds):
        self.seek(self.position + seconds)

    def change_volume(self, delta):
        self.volume = max(0.0, min(1.0, self.volume + delta))

    def toggle_mute(self):
        self.muted = not self.muted

    def close(self):
        self.closed = True


def two_tracks_three_links():
    """One track sold in two shops, plus a plain one: three links, two rows."""

    both = Track(
        title="Sold twice",
        permalink_url="https://soundcloud.com/a/both",
        id=901,
        purchase_url="https://label.bandcamp.com/track/x",
        description="also https://www.beatport.com/track/x/1",
    )
    single = Track(
        title="Sold once",
        permalink_url="https://soundcloud.com/a/single",
        id=902,
        purchase_url="https://label.bandcamp.com/track/y",
    )
    return links.categorise_all([both, single])


def player_app(records, state, **kwargs):
    app = make_app(records, state, **kwargs)
    app.player = FakePlayer()
    return app


def loading_fetch(app, started):
    """Stand in for the audio worker, doing what _audio_ready would do."""

    def fetch(track):
        started.append(track.id)
        app._player_bar().message = ""
        app.player.load(track, a_stream(), None)
        app.player.play()
        app.refresh_rows()
        app._focus_playing_track()
        app._player_bar().refresh_bar()

    return fetch


def test_three_links_over_two_tracks_make_two_rows():
    records = two_tracks_three_links()
    assert len(records) == 3

    app = DiggerApp(records, state=None)

    async def scenario():
        async with app.run_test():
            assert [row.track.id for row in app.rows] == [901, 902]

    run(scenario)


def test_next_track_moves_on_to_the_next_track(state, monkeypatch):
    app = player_app(two_tracks_three_links(), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", lambda track: started.append(track.id))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()

    run(scenario)
    assert started == [901, 902]


def test_previous_track_walks_back(state, monkeypatch):
    app = player_app(two_tracks_three_links(), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", lambda track: started.append(track.id))

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", DataTable)
            table.move_cursor(row=1)  # the second track
            await pilot.press("p")
            await pilot.pause()

    run(scenario)
    assert started == [901]


def test_a_finished_track_rolls_on_to_the_next(state, monkeypatch):
    """Auditioning a crate should not need a keypress between every track."""

    app = player_app(synthetic_records(4), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, started))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()
            app.player.finished = True
            app._tick()
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).cursor_row == 1

    run(scenario)
    assert started == [1, 2]


def test_the_end_of_the_list_stops_instead_of_wrapping(state, monkeypatch):
    app = player_app(synthetic_records(2), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, started))

    async def scenario():
        async with app.run_test() as pilot:
            app.query_one("#tracks", DataTable).move_cursor(row=1)
            await pilot.press("space")
            await pilot.pause()
            app.player.finished = True
            app._tick()
            await pilot.pause()

    run(scenario)
    assert started == [2]


def test_wandering_off_leaves_the_cursor_where_you_put_it(state, monkeypatch):
    """Browsing ahead while something plays must survive the auto-advance."""

    app = player_app(synthetic_records(4), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, started))

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", DataTable)
            await pilot.press("space")
            await pilot.pause()
            table.move_cursor(row=3)
            await pilot.pause()

            app.player.finished = True
            app._tick()
            await pilot.pause()
            assert table.cursor_row == 3

    run(scenario)
    # Playback moved on all the same.
    assert started == [1, 2]


def test_the_playing_row_carries_a_marker(state, monkeypatch):
    app = player_app(synthetic_records(3), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", DataTable)
            await pilot.press("space")
            await pilot.pause()
            markers = [str(table.get_row_at(index)[0]) for index in range(3)]
            assert markers == [tui.PLAYING_GLYPH, "", ""]

    run(scenario)


def test_marking_the_track_you_are_hearing_moves_listening_on_too(state, monkeypatch):
    """Ruling on a track mid-triage should not leave it playing to the end."""

    app = player_app(synthetic_records(4), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, started))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).cursor_row == 1

    run(scenario)
    assert started == [1, 2]


def test_marking_a_track_you_are_not_hearing_leaves_playback_alone(state, monkeypatch):
    app = player_app(synthetic_records(4), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, started))

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", DataTable)
            await pilot.press("space")
            await pilot.pause()
            table.move_cursor(row=2)
            await pilot.press("s")
            await pilot.pause()

    run(scenario)
    assert started == [1]


# Getting the next track ready


class FakeSource:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def prepared_for(app, index, source=None):
    track = app.visible_rows[index].track
    return tui.Prepared(track=track, stream=a_stream(), waveform=[1, 2], source=source)


def test_the_next_track_is_got_ready_before_this_one_ends(state, monkeypatch):
    """Otherwise every track in the crate is followed by a second of Loading."""

    app = player_app(synthetic_records(3), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))
    asked = []
    monkeypatch.setattr(app, "prepare_track", lambda track: asked.append(track.id))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()

            app.player.position = 290.0  # ten seconds left of three hundred
            app._tick()
            await pilot.pause()

    run(scenario)
    assert asked == [2]


def test_nothing_is_got_ready_while_there_is_time_left(state, monkeypatch):
    app = player_app(synthetic_records(3), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))
    asked = []
    monkeypatch.setattr(app, "prepare_track", lambda track: asked.append(track.id))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()
            app.player.position = 100.0
            app._tick()
            await pilot.pause()

    run(scenario)
    assert asked == []


def test_the_last_track_has_nothing_to_get_ready(state, monkeypatch):
    app = player_app(synthetic_records(1), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))
    asked = []
    monkeypatch.setattr(app, "prepare_track", lambda track: asked.append(track.id))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()
            app.player.position = 295.0
            app._tick()
            await pilot.pause()

    run(scenario)
    assert asked == []


def test_a_prepared_track_plays_without_asking_for_it_again(state, monkeypatch):
    app = player_app(synthetic_records(3), state)
    started = []
    monkeypatch.setattr(app, "fetch_audio", lambda track: started.append(track.id))

    async def scenario():
        async with app.run_test() as pilot:
            source = FakeSource()
            app._prepared = prepared_for(app, 1, source)
            app.query_one("#tracks", DataTable).move_cursor(row=1)
            await pilot.press("space")
            await pilot.pause()

            assert started == [], "the worker was asked for what was already here"
            assert app.player.playing is True
            assert app.player.source is source

    run(scenario)


def test_using_the_prepared_track_leaves_nothing_behind(state, monkeypatch):
    app = player_app(synthetic_records(3), state)
    monkeypatch.setattr(app, "fetch_audio", lambda track: None)

    async def scenario():
        async with app.run_test() as pilot:
            app._prepared = prepared_for(app, 1)
            app.query_one("#tracks", DataTable).move_cursor(row=1)
            await pilot.press("space")
            await pilot.pause()
            assert app._prepared is None

    run(scenario)


def test_a_filter_that_changes_what_comes_next_throws_it_away(state, monkeypatch):
    records = synthetic_records(2) + synthetic_records(1, category="beatport")
    app = player_app(records, state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")  # the first track
            await pilot.pause()
            source = FakeSource()
            app._prepared = prepared_for(app, 1, source)

            await pilot.press("2")  # only the beatport track survives
            await pilot.pause()

            assert app._prepared is None
            assert source.closed is True

    run(scenario)


def test_a_preparation_that_arrives_too_late_is_thrown_away(state):
    app = player_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test():
            source = FakeSource()
            app._preparing = "someone else"
            app._preparation_done("2", prepared_for(app, 1, source))

            assert app._prepared is None
            assert source.closed is True

    run(scenario)


def test_leaving_the_app_lets_go_of_the_prepared_track(state):
    app = player_app(synthetic_records(3), state)
    source = FakeSource()

    async def scenario():
        async with app.run_test():
            app._prepared = prepared_for(app, 1, source)

    run(scenario)
    assert source.closed is True


# Showing that a keypress landed


def styles_on(table, index):
    return {span.style for cell in table.get_row_at(index) for span in cell.spans}


def test_marking_a_track_lights_the_row_then_lets_it_settle(state):
    app = make_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            await pilot.press("g")
            await pilot.pause()
            lit = tui.STATUS_STYLES[GOT][1]
            row_cells = table.get_row_at(0)
            if len(row_cells) > TITLE_CELL and row_cells[TITLE_CELL].spans:
                assert str(row_cells[TITLE_CELL].spans[-1].style) == lit

            await pilot.pause(tui.FLASH + 0.1)
            assert lit not in styles_on(table, 0)
            assert str(table.get_row_at(0)[MARK_CELL]) == "\u2713"

    run(scenario)


def test_marking_a_track_does_not_redraw_the_whole_table(state, monkeypatch):
    """Rebuilding every row to change one glyph is the flicker you could see."""

    app = make_app(synthetic_records(4), state)

    async def scenario():
        async with app.run_test() as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            rebuilds = []
            monkeypatch.setattr(table, "clear", lambda *a, **k: rebuilds.append(1))

            await pilot.press("g")
            await pilot.pause()

            assert rebuilds == []
            assert str(table.get_row_at(0)[MARK_CELL]) == "\u2713"

    run(scenario)


def test_download_progress_does_not_move_the_viewport(state, monkeypatch):
    app = make_app(synthetic_records(60), state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            table.move_cursor(row=30)
            await scroll_table(pilot, table, 20)
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


def test_batch_progress_repaints_every_row_waiting_for_the_throttle(state):
    app = make_app(synthetic_records(4), state)

    async def scenario():
        async with app.run_test():
            first, second = app.visible_rows[:2]
            app._last_progress_redraw = float("inf")
            app._update_track_progress(first.track.key, 0.21)
            app._update_track_progress(second.track.key, 0.37)

            app._last_progress_redraw = 0
            app._update_track_progress(first.track.key, 0.42)

            table = app.query_one("#tracks", tui.TrackTable)
            assert "[42%]" in str(table.get_row_at(0)[TITLE_CELL])
            assert "[37%]" in str(table.get_row_at(1)[TITLE_CELL])

    run(scenario)


class ProgressProbeClient:
    def __init__(self, app, seen, output):
        self.app = app
        self.seen = seen
        self.output = output

    def download_track(self, track, directory, **kwargs):
        self.seen.append(self.app.download_progress[track.key])
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_bytes(b"audio")
        return self.output

    def close(self):
        pass


class ProfileRetryClient:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def download_track(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise gates.GateProfileRequired("real email required")
        return self.output

    def close(self):
        pass


def test_single_download_uses_a_playlist_named_subdirectory(state, tmp_path):
    track = Track(
        id=80,
        title="Download",
        permalink_url="https://soundcloud.com/a/download",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://api-v2.soundcloud.com/tracks/80/download",
    )
    app = make_app(links.categorise(track), state)
    app.crate_title = "Warehouse / Session: 01"
    app.config.download_directory = str(tmp_path / "Downloads")
    directories = []

    class Client:
        def download_track(self, _track, directory, **_kwargs):
            directories.append(directory)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "track.wav"
            path.write_bytes(b"audio")
            return path

        def close(self):
            pass

    app._client = Client()

    async def scenario():
        async with app.run_test():
            worker = app.download_track_in_background(track)
            await worker.wait()

    run(scenario)
    assert directories == [tmp_path / "Downloads" / "Warehouse Session 01"]
    assert state.get(track.key) == GOT


@pytest.mark.parametrize("save_profile", [False, True])
def test_gate_profile_wizard_retries_a_single_download_at_most_once(
    state, tmp_path, save_profile
):
    record = LinkRecord(
        category="gate",
        track=Track(
            title="Gate", artist="Artist",
            permalink_url="https://soundcloud.com/a/gate", id=81,
        ),
        link_url="https://hypeddit.com/a/gate",
        link_text="Download",
    )
    app = make_app([record], state)
    client = ProfileRetryClient(tmp_path / "gate.mp3")
    app._client = client

    async def scenario():
        async with app.run_test() as pilot:
            row = app.visible_rows[0]
            worker = app.download_track_in_background(row.track, record.link_url)
            await worker.wait()
            await pilot.pause()
            assert isinstance(app.screen, GateProfileScreen)
            if save_profile:
                app.screen.query_one("#gate-profile-name", Input).value = "Filip"
                app.screen.query_one("#gate-profile-email", Input).value = "filip@example.com"
                await pilot.click("#gate-profile-save")
                for _ in range(20):
                    await pilot.pause()
                    if state.get(row.track.key) == GOT:
                        break
            else:
                await pilot.click("#gate-profile-cancel")
                await pilot.pause()

    run(scenario)
    assert client.calls == (2 if save_profile else 1)
    assert (state.get(record.track.key) == GOT) is save_profile


def test_a_repeated_prerequisite_error_does_not_open_a_wizard_loop(state, tmp_path):
    record = LinkRecord(
        category="gate",
        track=Track(
            title="Gate", permalink_url="https://soundcloud.com/a/gate-loop", id=82
        ),
        link_url="https://hypeddit.com/a/gate-loop",
        link_text="Download",
    )
    app = make_app([record], state)

    class Client:
        calls = 0

        def download_track(self, *_args, **_kwargs):
            self.calls += 1
            raise gates.GateProfileRequired("still missing")

        def close(self):
            pass

    client = Client()
    app._client = client

    async def scenario():
        async with app.run_test() as pilot:
            worker = app.download_track_in_background(
                app.visible_rows[0].track, record.link_url
            )
            await worker.wait()
            await pilot.pause()
            app.screen.query_one("#gate-profile-name", Input).value = "Filip"
            app.screen.query_one("#gate-profile-email", Input).value = "filip@example.com"
            await pilot.click("#gate-profile-save")
            for _ in range(25):
                await pilot.pause()
                if client.calls == 2:
                    break
            await pilot.pause()
            assert not isinstance(app.screen, GateProfileScreen)

    run(scenario)
    assert client.calls == 2
    assert state.get(record.track.key) != GOT


def test_batch_finishes_independent_tracks_before_prompting_and_retries_only_pending(
    state, tmp_path, monkeypatch
):
    records = [
        LinkRecord(
            category="gate",
            track=Track(
                title="Needs email", artist="Artist",
                permalink_url="https://soundcloud.com/a/1", id=91,
            ),
            link_url="https://hypeddit.com/a/1",
            link_text="Download",
        ),
        LinkRecord(
            category="soundcloud",
            track=Track(
                title="Ready", artist="Artist",
                permalink_url="https://soundcloud.com/a/2", id=92,
                downloadable=True, has_downloads_left=True,
                download_url="https://api-v2.soundcloud.com/tracks/92/download",
            ),
            link_url="https://api-v2.soundcloud.com/tracks/92/download",
            link_text=links.FREE_DOWNLOAD,
        ),
    ]
    app = make_app(records, state)

    class Client:
        def __init__(self):
            self.calls = {91: 0, 92: 0}

        def download_track(self, track, *_args, **_kwargs):
            self.calls[track.id] += 1
            if track.id == 91 and self.calls[91] == 1:
                raise gates.GateProfileRequired("email")
            path = tmp_path / f"{track.id}.mp3"
            path.write_bytes(b"audio")
            return path

        def close(self):
            pass

    class Session:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    client = Client()
    app._client = client
    monkeypatch.setattr("dj_digger.tui.downloads.soundcloud.create_requests_session", Session)

    async def scenario():
        async with app.run_test() as pilot:
            items = [(row, app._find_gate_url(row)) for row in app.visible_rows]
            worker = app.batch_download_in_background(items)
            await worker.wait()
            await pilot.pause()
            assert state.get(records[1].track.key) == GOT
            assert state.get(records[0].track.key) != GOT
            assert isinstance(app.screen, GateProfileScreen)

            app.screen.query_one("#gate-profile-name", Input).value = "Filip"
            app.screen.query_one("#gate-profile-email", Input).value = "filip@example.com"
            await pilot.click("#gate-profile-save")
            for _ in range(20):
                await pilot.pause()
                if state.get(records[0].track.key) == GOT:
                    break

    run(scenario)
    assert client.calls == {91: 2, 92: 1}


def test_browser_required_batch_is_one_call_for_several_tracks(
    state, tmp_path, monkeypatch
):
    records = [
        LinkRecord(
            category="gate",
            track=Track(
                id=index,
                title=f"Gate {index}",
                permalink_url=f"https://soundcloud.com/a/{index}",
            ),
            link_url=f"https://hypeddit.com/track/{index}",
            link_text="Download",
        )
        for index in (201, 202)
    ]
    app = make_app(records, state)

    class Client:
        def download_track(self, *_args, **_kwargs):
            raise gates.GateManualActionRequired("browser")

        def close(self):
            pass

    class Session:
        def close(self):
            pass

    calls = []

    def browser_batch(items, _directory, _cancel):
        calls.append(items)
        completed = []
        for track, _url in items:
            path = tmp_path / f"{track.id}.mp3"
            path.write_bytes(b"audio")
            completed.append((track.key, path))
        return gates.HypedditBrowserBatchResult(completed=tuple(completed))

    app._client = Client()
    monkeypatch.setattr(
        "dj_digger.tui.downloads.soundcloud.create_requests_session", Session
    )
    monkeypatch.setattr(
        "dj_digger.tui.downloads.gates.download_hypeddit_batch_in_browser",
        browser_batch,
    )

    async def scenario():
        async with app.run_test():
            items = [(row, app._find_gate_url(row)) for row in app.visible_rows]
            worker = app.batch_download_in_background(items)
            await worker.wait()

    run(scenario)
    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert all(state.get(record.track.key) == GOT for record in records)


def test_batch_starts_downloads_before_every_hypeddit_preflight_finishes(
    state, tmp_path, monkeypatch
):
    records = []
    for index in (205, 206):
        gate_url = f"https://hypeddit.com/track/{index}"
        track = Track(
            id=index,
            title=f"Gate {index}",
            permalink_url=f"https://soundcloud.com/a/{index}",
            extra_links=[(gate_url, "Download")],
        )
        records.append(
            LinkRecord(
                category="gate",
                track=track,
                link_url=gate_url,
                link_text="Download",
            )
        )
    app = make_app(records, state)
    app.crate = library.CrateRecord(
        source="https://soundcloud.com/a/sets/batch",
        title="Batch",
        tracks=[record.track for record in records],
    )
    first_download = Event()
    started = []

    def normalise(row, gate_url):
        if row.track.id == 206:
            assert first_download.wait(2)
        return gate_url, False

    class Client:
        def download_track(self, track, directory, **_kwargs):
            started.append(track.id)
            if track.id == 205:
                first_download.set()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{track.id}.wav"
            path.write_bytes(b"audio")
            return path

        def close(self):
            pass

    class Session:
        def close(self):
            pass

    app._client = Client()
    monkeypatch.setattr(app, "_normalise_hypeddit_item", normalise)
    monkeypatch.setattr(
        "dj_digger.tui.downloads.soundcloud.create_requests_session", Session
    )
    monkeypatch.setattr(
        "dj_digger.tui.downloads.library_module.save", lambda _crate: None
    )

    async def scenario():
        async with app.run_test():
            items = [(row, app._find_gate_url(row)) for row in app.visible_rows]
            worker = app.batch_download_in_background(items)
            await worker.wait()

    run(scenario)
    assert sorted(started) == [205, 206]


def test_stop_browser_batch_leaves_unfinished_tracks_new(
    state, tmp_path, monkeypatch
):
    record = LinkRecord(
        category="gate",
        track=Track(
            id=203,
            title="Manual gate",
            permalink_url="https://soundcloud.com/a/203",
        ),
        link_url="https://hypeddit.com/track/203",
        link_text="Download",
    )
    app = make_app([record], state)

    class Client:
        def download_track(self, *_args, **_kwargs):
            raise gates.GateManualActionRequired("browser")

        def close(self):
            pass

    class Session:
        def close(self):
            pass

    entered = Event()

    def browser_batch(items, _directory, cancel):
        entered.set()
        assert cancel.wait(2), "the UI did not signal the browser worker"
        return gates.HypedditBrowserBatchResult(
            failures=((items[0][0].key, gates.GateManualActionRequired("cancelled")),),
            cancelled=True,
        )

    app._client = Client()
    monkeypatch.setattr(
        "dj_digger.tui.downloads.soundcloud.create_requests_session", Session
    )
    monkeypatch.setattr(
        "dj_digger.tui.downloads.gates.download_hypeddit_batch_in_browser",
        browser_batch,
    )

    async def scenario():
        async with app.run_test() as pilot:
            row = app.visible_rows[0]
            worker = app.batch_download_in_background([(row, record.link_url)])
            assert await asyncio.to_thread(entered.wait, 2)
            for _ in range(20):
                await pilot.pause(0.01)
                if app._browser_batch_active:
                    break
            app.action_stop_browser_batch()
            await worker.wait()
            await pilot.pause()

    run(scenario)
    assert app._gate_cancel.is_set()
    assert state.get(record.track.key) != GOT
    assert app._browser_batch_active is False


def test_saved_hypeddit_hub_is_normalised_before_batch_and_never_opens_chromium(
    state, monkeypatch
):
    wrapper = "https://hypeddit.com/duxnbass/epitome"
    track = Track(
        id=204,
        title="Epitome",
        permalink_url="https://soundcloud.com/duxnbass/epitome",
        description=f"Download: {wrapper}",
    )
    app = make_app(links.categorise(track), state)
    app.crate = library.CrateRecord(source="saved", title="Saved", tracks=[track])

    class Client:
        def download_track(self, *_args, **_kwargs):
            pytest.fail("a pure hub is not a downloadable gate")

        def close(self):
            pass

    class Session:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        "dj_digger.tui.downloads.gates.inspect_link_page",
        lambda *_args, **_kwargs: gates.LinkPageInspection(
            shops=(
                ("https://www.beatport.com/release/epitome/4194268", "Beatport"),
                ("https://duxnbass.bandcamp.com/album/epitome", "Bandcamp"),
            ),
            recognized=True,
        ),
    )
    monkeypatch.setattr(
        "dj_digger.tui.downloads.soundcloud.create_requests_session", Session
    )
    monkeypatch.setattr(
        "dj_digger.tui.downloads.library_module.save", lambda _crate: None
    )
    monkeypatch.setattr(
        "dj_digger.tui.downloads.gates.download_hypeddit_batch_in_browser",
        lambda *_args, **_kwargs: pytest.fail("a hub must not enter Chromium"),
    )
    app._client = Client()

    async def scenario():
        async with app.run_test() as pilot:
            row = app.visible_rows[0]
            worker = app.batch_download_in_background([(row, wrapper)])
            await worker.wait()
            await pilot.pause()

    run(scenario)
    assert sorted(app.rows[0].categories) == ["bandcamp", "beatport"]
    assert wrapper not in track.description
    assert state.get(track.key) != GOT


def test_soundcloud_login_refreshes_the_client_then_retries_once(
    state, tmp_path, monkeypatch
):
    record = LinkRecord(
        category="soundcloud",
        track=Track(
            title="Account download", artist="Artist",
            permalink_url="https://soundcloud.com/a/account", id=101,
            downloadable=True, has_downloads_left=True,
        ),
        link_url="https://soundcloud.com/a/account",
        link_text=links.FREE_DOWNLOAD,
    )
    app = make_app([record], state)

    class OldClient:
        client_id = "client-id"

        def __init__(self):
            self.calls = 0
            self.closed = False

        def download_track(self, *_args, **_kwargs):
            self.calls += 1
            raise soundcloud.SoundCloudLoginRequired("login")

        def close(self):
            self.closed = True

    class NewClient:
        def __init__(self, **_kwargs):
            self.calls = 0

        def download_track(self, *_args, **_kwargs):
            self.calls += 1
            path = tmp_path / "account.mp3"
            path.write_bytes(b"audio")
            return path

        def close(self):
            pass

    old = OldClient()
    new = NewClient()
    refreshed_with = []
    app._client = old
    monkeypatch.setattr(
        "dj_digger.tui.downloads.soundcloud.SoundCloudClient",
        lambda **kwargs: refreshed_with.append(kwargs.get("oauth_token")) or new,
    )
    monkeypatch.setattr(
        "dj_digger.tui.screens.auth_module.verify_and_save",
        lambda token, client_id: (token, "DJ", 1),
    )

    async def scenario():
        async with app.run_test() as pilot:
            row = app.visible_rows[0]
            worker = app.download_track_in_background(row.track)
            await worker.wait()
            await pilot.pause()
            assert isinstance(app.screen, SoundCloudAuthScreen)
            app.screen.query_one("#soundcloud-token", Input).value = "hidden-token"
            await pilot.click("#soundcloud-paste")
            for _ in range(30):
                await pilot.pause()
                if state.get(row.track.key) == GOT:
                    break

    run(scenario)
    assert old.calls == 1
    assert old.closed is True
    assert new.calls == 1
    assert refreshed_with == ["hidden-token"]
    assert state.get(record.track.key) == GOT


def test_soundcloud_client_refresh_waits_for_every_active_download_worker(state):
    app = make_app(synthetic_records(1), state)

    class Client:
        closed = False

        def close(self):
            self.closed = True

    client = Client()
    app._client = client
    resumed = []

    async def scenario():
        async with app.run_test() as pilot:
            app._begin_download_worker()
            app._request_client_refresh("fresh-token", lambda: resumed.append(True))
            assert app._client is client
            assert client.closed is False
            assert resumed == []

            await asyncio.to_thread(app._end_download_worker)
            await pilot.pause()
            assert app._client.oauth_token == "fresh-token"
            assert client.closed is True
            assert resumed == [True]

    run(scenario)


@pytest.mark.parametrize("batch", [False, True])
def test_download_stays_at_zero_while_the_link_is_being_resolved(
    state, monkeypatch, tmp_path, batch
):
    record = LinkRecord(
        category="soundcloud",
        track=Track(
            title="Free",
            permalink_url="https://soundcloud.com/a/free",
            id=7,
            downloadable=True,
            has_downloads_left=True,
        ),
        link_url="https://soundcloud.com/a/free",
        link_text=links.FREE_DOWNLOAD,
    )
    app = make_app([record], state)
    seen = []
    app._client = ProgressProbeClient(app, seen, tmp_path / "free.mp3")

    class Session:
        def close(self):
            pass

    monkeypatch.setattr("dj_digger.tui.downloads.soundcloud.create_requests_session", Session)

    async def scenario():
        async with app.run_test():
            row = app.visible_rows[0]
            worker = (
                app.batch_download_in_background([(row, None)])
                if batch
                else app.download_track_in_background(row.track)
            )
            await worker.wait()

    run(scenario)
    assert seen == [0.0]


@pytest.mark.parametrize(
    ("outcome", "completed"),
    [
        ("single_success", True),
        ("single_failure", False),
        ("batch_success", True),
        ("batch_failure", False),
        ("batch_complete", False),
    ],
)
def test_download_results_do_not_move_the_viewport(state, monkeypatch, tmp_path, outcome, completed):
    app = make_app(synthetic_records(60), state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            table.move_cursor(row=30)
            await scroll_table(pilot, table, 20)
            row = app.visible_rows[35]
            key = row.track.key
            app.download_progress[key] = 0.42
            app._paint_download_row(key)
            await pilot.pause()
            cursor = table.cursor_row
            viewport = table.scroll_offset
            rebuilds = []
            monkeypatch.setattr(table, "clear", lambda *a, **k: rebuilds.append(1))

            if outcome == "single_success":
                app._download_finished(key, tmp_path / "track.mp3")
            elif outcome == "single_failure":
                app._download_failed(key, "network broke")
            elif outcome == "batch_success":
                app._on_batch_track_finished(row, str(tmp_path / "track.mp3"))
            elif outcome == "batch_failure":
                app._on_batch_track_failed(row, "network broke")
            else:
                app._on_batch_download_complete(0, 1, 1)
            await pilot.pause()

            assert rebuilds == []
            assert table.cursor_row == cursor
            assert table.scroll_offset == viewport
            assert "[42%]" not in str(table.get_row_at(35)[TITLE_CELL])
            assert (state.get(key) == GOT) is completed

    run(scenario)


def test_hidden_completion_outside_the_current_view_does_not_rebuild(state, monkeypatch, tmp_path):
    app = make_app(synthetic_records(4), state)

    async def scenario():
        async with app.run_test():
            hidden_row = app.visible_rows[0]
            app.search_term = "Track 3"
            app.hide_handled = True
            app.refresh_rows(keep_cursor=False)
            table = app.query_one("#tracks", tui.TrackTable)
            rebuilds = []
            monkeypatch.setattr(table, "clear", lambda *a, **k: rebuilds.append(1))

            app._download_finished(hidden_row.track.key, tmp_path / "track.mp3")

            assert rebuilds == []
            assert len(app.visible_rows) == 1

    run(scenario)


@pytest.mark.parametrize(("cursor_row", "scroll_y"), [(30, 20), (10, 40)])
def test_refresh_rows_preserves_the_viewport(state, cursor_row, scroll_y):
    app = make_app(synthetic_records(60), state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            table.move_cursor(row=cursor_row)
            await scroll_table(pilot, table, scroll_y)
            cursor = table.cursor_row
            viewport = table.scroll_offset

            app.refresh_rows()
            await pilot.pause()

            assert table.cursor_row == cursor
            assert table.scroll_offset == viewport

    run(scenario)


def test_refresh_rows_keeps_the_same_tracks_after_rows_above_are_removed(state):
    app = make_app(synthetic_records(60), state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            table.move_cursor(row=45)
            await scroll_table(pilot, table, 40)
            cursor_key = app.visible_rows[table.cursor_row].track.key
            top_key = app.visible_rows[table.scroll_offset.y].track.key

            app.hide_handled = True
            state.set(app.visible_rows[10].track.key, GOT)
            app.refresh_rows()
            await pilot.pause()

            assert app.visible_rows[table.cursor_row].track.key == cursor_key
            assert app.visible_rows[table.scroll_offset.y].track.key == top_key

    run(scenario)


def test_back_to_back_refreshes_keep_the_same_tracks(state):
    app = make_app(synthetic_records(60), state)

    async def scenario():
        async with app.run_test(size=(100, 24)) as pilot:
            table = app.query_one("#tracks", tui.TrackTable)
            table.move_cursor(row=10)
            await scroll_table(pilot, table, 40)
            cursor_key = app.visible_rows[table.cursor_row].track.key
            top_key = app.visible_rows[table.scroll_offset.y].track.key
            assert table.scroll_offset.y > 0

            app.hide_handled = True
            state.set(app.rows[20].track.key, GOT)
            app.refresh_rows()
            state.set(app.rows[21].track.key, GOT)
            app.refresh_rows()
            await pilot.pause()

            assert app.visible_rows[table.cursor_row].track.key == cursor_key
            assert app.visible_rows[table.scroll_offset.y].track.key == top_key

    run(scenario)


def test_a_row_that_is_about_to_be_hidden_is_not_lit(state):
    """With handled rows hidden, the row leaves rather than flashing in place."""

    app = make_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("h")  # hide handled
            await pilot.press("g")
            await pilot.pause()
            assert app.query_one("#tracks", DataTable).row_count == 2

    run(scenario)


def test_the_counts_keep_up_without_a_rebuild(state):
    app = make_app(synthetic_records(3), state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("g")
            await pilot.pause()
            assert "got 1" in bar_text(app)

    run(scenario)


# Drawing frames only when there are frames worth drawing


def test_the_frame_timer_sleeps_until_something_plays(state, monkeypatch):
    """Waking thirty times a second to watch a still list is just a warm laptop."""

    app = player_app(synthetic_records(3), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._ticker._active.is_set() is False

            await pilot.press("space")
            await pilot.pause()
            assert app._ticker._active.is_set() is True

    run(scenario)


def test_the_frame_timer_wakes_when_the_audio_actually_arrives(state, monkeypatch):
    """It slept through the half second the stream took to resolve, and stayed
    asleep - so the clock read 0:00 for the whole track."""

    app = player_app(synthetic_records(2), state)
    asked = []
    monkeypatch.setattr(app, "fetch_audio", lambda track: asked.append(track))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()
            app._tick()  # a frame lands while the stream is still resolving
            assert app._ticker._active.is_set() is False

            app._audio_ready(asked[0], a_stream(), [1, 2])
            await pilot.pause()
            assert app._ticker._active.is_set() is True

    run(scenario)


def test_the_frame_timer_stops_again_when_playback_does(state, monkeypatch):
    app = player_app(synthetic_records(3), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()
            app.player.playing = False
            app._tick()
            assert app._ticker._active.is_set() is False

    run(scenario)


def test_turning_animation_off_slows_the_frame_timer_down(state):
    """The thing that repaints most cannot ignore the setting that says not to."""

    app = player_app(synthetic_records(1), state)

    async def scenario():
        async with app.run_test():
            app.animation_level = "full"
            assert app.frame_interval == tui.TICK
            app.animation_level = "none"
            assert app.frame_interval == tui.CALM_TICK

    run(scenario)


def test_the_player_bar_grows_instead_of_appearing_from_nothing(state, monkeypatch):
    app = player_app(synthetic_records(2), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))

    async def scenario():
        async with app.run_test() as pilot:
            bar = app.query_one("#player", PlayerBar)
            assert bar.wanted_height == 0

            await pilot.press("space")
            await pilot.pause()
            assert bar.wanted_height == 3
            assert bar.styles.height.value == 3

    run(scenario)


def test_the_player_bar_folds_away_when_there_is_nothing_to_say(state, monkeypatch):
    app = player_app(synthetic_records(2), state)
    monkeypatch.setattr(app, "fetch_audio", loading_fetch(app, []))

    async def scenario():
        async with app.run_test() as pilot:
            bar = app.query_one("#player", PlayerBar)
            await pilot.press("space")
            await pilot.pause()

            app.player.loaded = None
            bar.refresh_bar()
            assert bar.wanted_height == 0

    run(scenario)


def test_digging_shows_something_turning(state, monkeypatch):
    """A spinner is the difference between "working" and "hung"."""

    monkeypatch.setattr("dj_digger.dig.dig", lambda target, **kwargs: crate_of(1))
    app = make_app([], state, export_format="none")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._digging = True
            app._dig_message = "Fetching tracks 3/9"

            app._frame = tui.SPINNER_EVERY - 1
            app._tick()
            first = bar_text(app)
            app._frame = 2 * tui.SPINNER_EVERY - 1
            app._tick()

            assert "Fetching tracks 3/9" in first
            assert any(glyph in first for glyph in tui.SPINNER)
            assert bar_text(app) != first
            app._digging = False

    run(scenario)


def test_space_toggles_a_track_that_is_already_loaded(records, state, monkeypatch):
    app = player_app(records, state)
    monkeypatch.setattr(app, "fetch_audio", lambda track: None)

    async def scenario():
        async with app.run_test() as pilot:
            track = app.visible_rows[0].track
            app.player.load(track, a_stream(), None)
            await pilot.press("space")
            assert app.player.playing is True
            await pilot.press("space")
            assert app.player.playing is False

    run(scenario)


def test_seek_keys_nudge_the_position(records, state):
    app = player_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            app.player.load(app.visible_rows[0].track, a_stream(), None)
            app.player.position = 100.0
            await pilot.press("right_square_bracket")
            await pilot.press("left_square_bracket")

    run(scenario)
    assert app.player.seeks == [110.0, 100.0]


def test_volume_and_mute_keys(records, state):
    app = player_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("minus")
            assert app.player.volume == pytest.approx(0.7)
            await pilot.press("equals_sign")
            assert app.player.volume == pytest.approx(0.8)
            await pilot.press("m")
            assert app.player.muted is True

    run(scenario)


def test_a_track_without_an_id_cannot_be_previewed(state, monkeypatch):
    track = Track(title="No id", permalink_url="https://soundcloud.com/a/x")
    app = player_app(links.categorise_all([track]), state)
    monkeypatch.setattr(app, "fetch_audio", lambda t: pytest.fail("should not fetch"))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("space")
            await pilot.pause()

    run(scenario)


def test_pressing_play_twice_without_audio_does_not_crash(records, state, monkeypatch):
    """The first press went through the guarded loader, the second did not."""

    app = make_app(records, state)  # the real Player, so the real toggle path runs

    def no_device(*args, **kwargs):
        raise PlaybackUnavailable("No audio output on this machine")

    monkeypatch.setattr(app.player, "_device_for", no_device)
    track = records[0].track

    async def scenario():
        async with app.run_test() as pilot:
            app.player._loaded = Loaded(track=track, stream=a_stream(), duration=200.0)
            app.player._info = SimpleNamespace(sample_rate=44100, nchannels=2)
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            assert "No audio output" in str(app.query_one("#player", PlayerBar).render())

    run(scenario)


def test_a_dead_audio_device_is_only_probed_once(monkeypatch):
    from dj_digger.player import Player

    attempts = []

    class Boom:
        def __init__(self, **kwargs):
            attempts.append(kwargs)
            raise RuntimeError("failed to init device")

    subject = Player()
    subject._miniaudio = SimpleNamespace(PlaybackDevice=Boom)

    for _ in range(3):
        with pytest.raises(PlaybackUnavailable):
            subject._device_for(44100, 2)

    assert len(attempts) == 1


def test_a_playback_failure_shows_a_message_instead_of_crashing(records, state):
    app = player_app(records, state)

    async def scenario():
        async with app.run_test() as pilot:
            app._playback_failed("This machine has no audio output")
            await pilot.pause()
            bar = app.query_one("#player", PlayerBar)
            assert "no audio output" in str(bar.render())

    run(scenario)


def test_clicking_the_waveform_maps_to_a_time(records, state):
    app = player_app(records, state)

    async def scenario():
        async with app.run_test():
            bar = app.query_one("#player", PlayerBar)
            app.player.load(app.visible_rows[0].track, a_stream(), None)
            width = bar._bar_width()
            assert bar.seconds_at(1) == pytest.approx(0.0)
            assert bar.seconds_at(1 + width) == pytest.approx(app.player.duration)

    run(scenario)


def test_the_player_is_closed_on_exit(records, state):
    app = player_app(records, state)

    async def scenario():
        async with app.run_test():
            pass

    run(scenario)
    assert app.player.closed is True


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


def test_clean_gate_badge_name(state):
    rec = LinkRecord(
        track=Track("Test Track", "Artist", "https://soundcloud.com/test/track"),
        category="gate",
        link_url="https://hypeddit.com/exaltation/krvzyintotheabyss-1",
        link_text="Download",
    )
    app = make_app([rec], state)
    row = app.rows[0]
    badges = app._store_badges(row)
    assert str(badges) == "gate(hypeddit)"


def test_batch_download_skips_skipped_tracks(state, monkeypatch):
    rec1 = LinkRecord(
        track=Track("Track 1", "Artist", "https://soundcloud.com/1", downloadable=True),
        category="gate",
        link_url="https://hypeddit.com/test1",
        link_text="Download",
    )
    rec2 = LinkRecord(
        track=Track("Track 2", "Artist", "https://soundcloud.com/2", downloadable=True),
        category="gate",
        link_url="https://hypeddit.com/test2",
        link_text="Download",
    )
    state.set(rec2.track.key, SKIP)
    app = make_app([rec1, rec2], state)

    started = []
    monkeypatch.setattr(app, "batch_download_in_background", lambda items: started.extend(items))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("W")

    run(scenario)
    assert len(started) == 1
    assert started[0][0].track.key == rec1.track.key


def test_batch_marks_an_existing_local_file_got_without_downloading(
    state, tmp_path, monkeypatch
):
    track = Track(
        id=303,
        title="Already here",
        permalink_url="https://soundcloud.com/a/already-here",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://api-v2.soundcloud.com/tracks/303/download",
    )
    local_file = tmp_path / "Already here.wav"
    local_file.write_bytes(b"RIFF-audio")
    track.local_path = str(local_file)
    state.set_local_file(track.key, local_file)
    app = make_app(links.categorise(track), state)
    started = []
    monkeypatch.setattr(
        app, "batch_download_in_background", lambda items: started.extend(items)
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("W")

    run(scenario)
    assert started == []
    assert state.get(track.key) == GOT


def test_a_missing_file_clears_its_path_and_file_backed_got(state, tmp_path):
    track = Track(
        id=304,
        title="Gone",
        permalink_url="https://soundcloud.com/a/gone",
    )
    missing = tmp_path / "deleted.wav"
    track.local_path = str(missing)
    state.set_local_file(track.key, missing)
    app = make_app(links.categorise(track), state)

    async def scenario():
        async with app.run_test() as pilot:
            app.apply_local_file_matches(StubScanner({}))
            await pilot.pause()

            assert track.local_path is None
            assert state.local_file(track.key) is None
            assert state.get(track.key) == "new"

            await pilot.click("#tracks", offset=(10, 1), button=3)
            await pilot.pause()
            assert isinstance(app.screen, ContextMenuScreen)
            assert not any(
                action in {"copy", "copy_file"}
                for action, _label in app.screen.options
            )

    run(scenario)


def test_a_legacy_stale_cache_match_clears_its_old_got_status(records, state):
    app = make_app(records, state)
    key = records[0].track.key
    state.set(key, GOT)

    class StaleScanner:
        def match_track(self, _track):
            return None

        def had_stale_match(self, track):
            return track.key == key

    async def scenario():
        async with app.run_test():
            app.apply_local_file_matches(StaleScanner())

    run(scenario)
    assert state.get(key) == "new"
    assert records[0].track.local_path is None


def test_context_menu_can_copy_a_local_track_into_the_playlist_folder(
    state, tmp_path
):
    source = tmp_path / "Music" / "Artist - Track.wav"
    source.parent.mkdir()
    source.write_bytes(b"RIFF-local-audio")
    track = Track(
        id=305,
        title="Track",
        artist="Artist",
        permalink_url="https://soundcloud.com/a/track",
        local_path=str(source),
    )
    state.set_local_file(track.key, source)
    app = make_app(links.categorise(track), state)
    app.crate_title = "Playlist / One"
    app.config.download_directory = str(tmp_path / "Downloads")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#tracks", offset=(10, 1), button=3)
            await pilot.pause()
            assert isinstance(app.screen, ContextMenuScreen)
            assert ("copy_file", "Copy file to playlist folder") in app.screen.options
            await pilot.press("escape")

            worker = app.copy_local_file_in_background(track)
            await worker.wait()
            await pilot.pause()

    run(scenario)
    target = tmp_path / "Downloads" / "Playlist One" / source.name
    assert target.read_bytes() == b"RIFF-local-audio"
    assert track.local_path == str(target)
    assert state.local_file(track.key) == str(target)


class ClosingSource:
    """A prepared stream that records whether anybody let go of it."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_leaving_stops_the_ticker_and_lets_go_of_everything(records, state, monkeypatch):
    """There were two on_unmount methods, so only the second one ever ran.

    Which meant the thirty-a-second ticker was left running - and nothing
    noticed, because no test covered the way out at all.
    """

    app = make_app(records, state)
    closed = []
    source = ClosingSource()

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                type(app.player), "close", lambda self: closed.append("player")
            )
            app._prepared = tui.Prepared(
                track=Track(title="next", permalink_url="https://soundcloud.com/a/2", id=2),
                stream=Stream(url="https://cdn/2.mp3"),
                source=source,
            )
            app._download_executor = ThreadPoolExecutor(max_workers=1)
            executor = app._download_executor
            app.exit()

        assert app._ticker is None, "the ticker was left running"
        assert closed == ["player"], "the audio device was not handed back"
        assert source.closed, "the prefetched stream was left open"
        assert app._download_executor is None
        assert app._cart_cancel.is_set(), "the store browser worker was not signalled"
        with pytest.raises(RuntimeError):
            executor.submit(lambda: None)

    run(scenario)


def gate_row(*pairs):
    """One row carrying (category, url) links, in the order given."""

    track = Track(title="T", permalink_url="https://soundcloud.com/a/b", id=5)
    return tui.Row(
        position=1,
        track=track,
        records=[
            LinkRecord(category=category, track=track, link_url=url, link_text="Buy")
            for category, url in pairs
        ],
    )


@pytest.mark.parametrize(
    "row,expected",
    [
        # An explicit gate beats a shop that happens to come first.
        (
            gate_row(("bandcamp", "https://x.bandcamp.com/a"), ("gate", "https://hypeddit.com/track/z")),
            "https://hypeddit.com/track/z",
        ),
        # Cloud storage has no category of its own, but gates can still unwrap it.
        (
            gate_row(("others", "https://www.mediafire.com/file/abc")),
            "https://www.mediafire.com/file/abc",
        ),
        (
            gate_row(("others", "https://drive.google.com/file/d/abc/view")),
            "https://drive.google.com/file/d/abc/view",
        ),
        # A shop page is not something a resolver can turn into a file.
        (gate_row(("bandcamp", "https://x.bandcamp.com/a"), ("beatport", "https://beatport.com/t/1")), None),
        (gate_row(("streaming", "https://open.spotify.com/track/1")), None),
        # Anything else unrecognised is worth handing over as a last resort.
        (gate_row(("smartlink", "https://lnk.to/abc")), "https://lnk.to/abc"),
        # Nothing to hand over at all.
        (gate_row(("soundcloud", "https://soundcloud.com/a/b")), None),
    ],
)
def test_the_gate_link_w_would_use(state, row, expected):
    """Three passes: the declared gate, then a host gates knows, then anything left."""

    assert make_app([], state)._find_gate_url(row) == expected


def test_no_two_parts_of_the_app_define_the_same_method():
    """A name defined twice is how this class lost an on_unmount for two releases.

    DiggerApp is assembled from seven mixins. Python resolves a clash silently by
    taking the first in the MRO, so the only thing standing between a rename and
    a method that quietly stops running is this.
    """

    ours = [base for base in DiggerApp.__mro__ if base.__module__.startswith("dj_digger.tui")]
    # Not an exact count, which would need editing every time a concern moves
    # out - just enough to prove the scan below is looking at something.
    assert len(ours) >= 8, f"expected DiggerApp and its mixins, got {len(ours)}"

    owner = {}
    clashes = []
    for base in ours:
        for name, value in vars(base).items():
            if name.startswith("__") or not (callable(value) or isinstance(value, property)):
                continue
            if name in owner:
                clashes.append(f"{name} ({owner[name].__name__} and {base.__name__})")
            owner[name] = base

    assert len(owner) > 100, "the scan found almost nothing, so it proves nothing"
    assert not clashes, "defined more than once: " + ", ".join(clashes)


class StubScanner:
    """Answers match_track from a dict, so no test touches a real music folder."""

    def __init__(self, matches):
        self.matches = matches

    def match_track(self, track):
        return self.matches.get(track.key)


def test_a_confident_match_marks_an_untouched_track_as_got(records, state):
    app = make_app(records, state)
    key = records[0].track.key
    scanner = StubScanner({key: LocalMatch("/music/a.mp3", confident=True)})

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.apply_local_file_matches(scanner)

            assert state.get(key) == GOT
            assert app.rows[0].track.local_path == "/music/a.mp3"

    run(scenario)


def test_a_confident_match_promotes_an_opened_track_to_got(records, state):
    app = make_app(records, state)
    key = records[0].track.key
    state.set(key, OPENED)
    scanner = StubScanner({key: LocalMatch("/music/a.mp3", confident=True)})

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.apply_local_file_matches(scanner)

            assert state.get(key) == GOT
            assert app.rows[0].track.local_path == "/music/a.mp3"

    run(scenario)


def test_a_loose_match_points_at_the_file_without_claiming_you_have_it(records, state):
    """A title that happens to agree is not evidence you own the track."""

    app = make_app(records, state)
    key = records[0].track.key
    scanner = StubScanner({key: LocalMatch("/music/maybe.mp3", confident=False)})

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.apply_local_file_matches(scanner)

            assert app.rows[0].track.local_path == "/music/maybe.mp3"
            assert state.get(key) == "new", "a loose match must not mark anything"

    run(scenario)


def test_a_confident_scan_marks_even_a_skipped_track_as_got(records, state):
    """A confirmed file on disk is stronger evidence than a stale status."""

    app = make_app(records, state)
    key = records[0].track.key
    state.set(key, SKIP)
    scanner = StubScanner({key: LocalMatch("/music/a.mp3", confident=True)})

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.apply_local_file_matches(scanner)

            assert state.get(key) == GOT
            assert app.rows[0].track.local_path == "/music/a.mp3", "the badge still belongs"

    run(scenario)


def test_a_matched_track_is_badged_in_the_table(records, state):
    app = make_app(records, state)
    key = records[0].track.key
    scanner = StubScanner({key: LocalMatch("/music/a.mp3", confident=True)})

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.apply_local_file_matches(scanner)
            await pilot.pause()

            table = app.query_one("#tracks", DataTable)
            title = table.get_cell_at(Coordinate(0, TITLE_CELL))
            assert "\U0001f4c1" in str(title)

    run(scenario)


def test_copying_the_path_says_so_either_way(records, state, monkeypatch, tmp_path):
    app = make_app(records, state)
    key = records[0].track.key
    local_file = tmp_path / "a.mp3"
    local_file.write_bytes(b"audio")
    said = []
    monkeypatch.setattr(DiggerApp, "notify", lambda self, msg, **kw: said.append(msg))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("y")
            assert "No local file matched" in said[-1]

            app.apply_local_file_matches(
                StubScanner({key: LocalMatch(str(local_file), confident=True)})
            )
            monkeypatch.setattr(
                "dj_digger.tui.library_scan.copy_to_clipboard", lambda text: True
            )
            await pilot.press("y")
            assert str(local_file) in said[-1]

    run(scenario)


def test_the_first_launch_asks_for_the_settings_before_anything_else(state, tmp_path):
    """No config file means nothing is configured, including the scan folders."""

    (tmp_path / "config.json").unlink()  # conftest wrote one; a first run has none
    app = make_app([], state)
    assert app.config.first_run is True

    async def scenario():
        # Left on screen deliberately: dismissing it would start the library
        # scan, and this profile still points at the real ~/Music.
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, SettingsScreen)

    run(scenario)


def test_tracks_a_refresh_brought_in_are_marked_and_sorted_to_the_top(state):
    record = saved_crate(2, title="Grown")
    library.refresh(record, Crate(
        source=record.source,
        title=record.title,
        tracks=list(record.tracks) + [
            Track(
                title="Arrived",
                permalink_url="https://soundcloud.com/a/new",
                id=900,
                purchase_url="https://label.bandcamp.com/track/new",
            )
        ],
    ))
    library.save(record)
    app = make_app([], state)

    async def scenario():
        async with app.run_test() as pilot:
            app.load_crate(record)
            await pilot.pause()
            table = app.query_one("#tracks", DataTable)
            first = table.get_cell_at(Coordinate(0, TITLE_CELL))
            assert first.plain.startswith("NEW ")
            assert "Arrived" in first.plain
            second = table.get_cell_at(Coordinate(1, TITLE_CELL))
            assert not second.plain.startswith("NEW ")

    run(scenario)

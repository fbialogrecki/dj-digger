import threading

import pytest
from helpers import a_crate

from dj_digger import gate_models
from dj_digger.models import Cancelled, Track
from dj_digger.services import collection as dig


def test_a_soundcloud_link_goes_through_the_api(monkeypatch):
    seen = {}

    def fake_collect(url, **kwargs):
        seen["url"] = url
        seen["limit"] = kwargs.get("limit")
        return a_crate()

    monkeypatch.setattr("dj_digger.soundcloud.collect_tracks", fake_collect)
    dig.dig("https://soundcloud.com/a/sets/b", limit=5)

    assert seen == {"url": "https://soundcloud.com/a/sets/b", "limit": 5}


def test_anything_but_a_soundcloud_link_is_rejected_with_a_readable_message(tmp_path):
    saved = tmp_path / "saved.html"
    saved.write_text("<html></html>", encoding="utf-8")
    for target in ("definitely-not-here.html", str(saved), "https://example.com/sets/b"):
        with pytest.raises(dig.TargetNotFound, match="not a soundcloud.com link"):
            dig.dig(target)


def test_surrounding_whitespace_is_forgiven(monkeypatch):
    """People paste links with a trailing newline all the time."""

    monkeypatch.setattr("dj_digger.soundcloud.collect_tracks", lambda url, **kw: a_crate())
    dig.dig("  https://soundcloud.com/a/sets/b\n")


def test_progress_is_reported_by_stage(monkeypatch):
    def fake_collect(url, **kwargs):
        kwargs["on_progress"](50, 120)
        kwargs["on_progress"](120, 120)
        return a_crate()

    monkeypatch.setattr("dj_digger.soundcloud.collect_tracks", fake_collect)

    seen = []
    dig.dig(
        "https://soundcloud.com/a/sets/b",
        on_progress=lambda stage, done, total: seen.append((stage, done, total)),
    )

    assert seen[0] == (dig.STAGE_LINK, 0, None)
    assert seen[1:] == [(dig.STAGE_TRACKS, 50, 120), (dig.STAGE_TRACKS, 120, 120)]


def test_default_options():
    options = dig.DigOptions()
    assert (options.limit, options.timeout) == (None, 20.0)


def a_hub_track():
    return Track(
        title="Know Your Place",
        permalink_url="https://soundcloud.com/a/kyp",
        id=1,
        purchase_url="https://sonaxx.ampsuite.com/releases/links?id=447",
        purchase_title="Buy",
    )


def test_a_link_hub_is_replaced_by_the_shops_behind_it(monkeypatch):
    from dj_digger import links

    monkeypatch.setattr(
        "dj_digger.gates.hubs.inspect_link_page",
        lambda url, session, timeout=10.0: gate_models.LinkPageInspection(
            shops=(
                ("https://www.beatport.com/release/kyp/7057750", "Beatport"),
                ("https://label.bandcamp.com/album/kyp", "Bandcamp"),
            )
        ),
    )
    monkeypatch.setattr("dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession())

    track = a_hub_track()
    assert dig.expand_link_hubs([track]) == 1

    assert track.purchase_url is None, "the hub itself does not survive"
    assert [url for url, _text in track.extra_links] == [
        "https://www.beatport.com/release/kyp/7057750",
        "https://label.bandcamp.com/album/kyp",
    ]
    # And the point of all of it: no gate badge, two shops instead.
    assert sorted(record.category for record in links.categorise(track)) == ["bandcamp", "beatport"]


def test_hub_expansion_stops_when_cancelled(monkeypatch):
    cancel = threading.Event()
    inspected = []

    def inspect(url, session, timeout=10.0):
        inspected.append(url)
        cancel.set()
        return gate_models.LinkPageInspection(shops=(("https://label.bandcamp.com/album/x", "Bandcamp"),))

    monkeypatch.setattr("dj_digger.gates.hubs.inspect_link_page", inspect)
    monkeypatch.setattr("dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession())
    tracks = [a_hub_track() for _ in range(40)]

    with pytest.raises(Cancelled):
        dig.expand_link_hubs(tracks, cancel=cancel)

    assert len(inspected) < 40, "the queue is dropped once the event is set"


def test_a_hub_that_turned_out_to_be_a_gate_is_left_alone(monkeypatch):
    from dj_digger import links

    monkeypatch.setattr(
        "dj_digger.gates.hubs.inspect_link_page",
        lambda *a, **kw: gate_models.LinkPageInspection(keep_original=True),
    )
    monkeypatch.setattr("dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession())

    track = Track(
        title="T",
        permalink_url="https://soundcloud.com/a/t",
        purchase_url="https://hypeddit.com/track/abc",
    )
    assert dig.expand_link_hubs([track]) == 0
    assert track.purchase_url == "https://hypeddit.com/track/abc"
    assert [record.category for record in links.categorise(track)] == ["gate"]


def test_hypeddit_smartlink_becomes_nested_gate_and_shops(monkeypatch):
    nested = "https://hypeddit.com/track/nmqt0z"
    monkeypatch.setattr(
        "dj_digger.gates.hubs.inspect_link_page",
        lambda *a, **kw: gate_models.LinkPageInspection(
            shops=(
                ("https://www.beatport.com/release/ghetto-bass/2470877", "Beatport"),
                ("https://terrenceandphillip.bandcamp.com/", "Bandcamp"),
            ),
            gate_urls=(nested,),
        ),
    )
    monkeypatch.setattr("dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession())
    track = Track(
        title="Ghetto Bass",
        permalink_url="https://soundcloud.com/a/ghetto-bass",
        purchase_url="https://hypeddit.com/link/ky9i8z",
    )

    assert dig.expand_link_hubs([track]) == 1

    assert track.purchase_url is None
    assert [url for url, _label in track.extra_links] == [
        "https://www.beatport.com/release/ghetto-bass/2470877",
        "https://terrenceandphillip.bandcamp.com/",
        nested,
    ]


def test_hypeddit_in_a_description_is_expanded_without_scraping_other_description_links(
    monkeypatch,
):
    from dj_digger import links

    hypeddit = "https://hypeddit.com/duxnbass/epitome"
    inspected = []

    def inspect(url, *_args, **_kwargs):
        inspected.append(url)
        return gate_models.LinkPageInspection(
            shops=(
                ("https://www.beatport.com/release/epitome/4194268", "Beatport"),
                ("https://duxnbass.bandcamp.com/album/epitome", "Bandcamp"),
            ),
            recognized=True,
        )

    monkeypatch.setattr("dj_digger.gates.hubs.inspect_link_page", inspect)
    monkeypatch.setattr(
        "dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession()
    )
    track = Track(
        title="Epitome",
        permalink_url="https://soundcloud.com/duxnbass/epitome",
        description=(
            f"Download: {hypeddit}\n"
            "Label boilerplate: https://linktr.ee/duxnbass"
        ),
    )

    assert dig.expand_link_hubs([track]) == 1
    assert inspected == [hypeddit]
    assert hypeddit not in track.description
    assert sorted(record.category for record in links.categorise(track)) == [
        "bandcamp",
        "beatport",
    ]


def test_a_recognised_empty_hypeddit_hub_becomes_no_link(monkeypatch):
    from dj_digger import links

    wrapper = "https://hypeddit.com/empty"
    monkeypatch.setattr(
        "dj_digger.gates.hubs.inspect_link_page",
        lambda *_args, **_kwargs: gate_models.LinkPageInspection(recognized=True),
    )
    monkeypatch.setattr(
        "dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession()
    )
    track = Track(
        title="Empty hub",
        permalink_url="https://soundcloud.com/a/empty",
        extra_links=[(wrapper, "Download")],
    )

    assert dig.expand_link_hubs([track]) == 1
    assert track.extra_links == []
    assert [record.category for record in links.categorise(track)] == ["no-link"]


def test_a_crate_of_plain_shop_links_costs_no_requests(monkeypatch):
    """Nothing to expand means no session, no page fetch, no progress noise."""

    monkeypatch.setattr(
        "dj_digger.soundcloud.create_requests_session",
        lambda **kw: pytest.fail("should not open a session"),
    )
    track = Track(
        title="T",
        permalink_url="https://soundcloud.com/a/t",
        purchase_url="https://label.bandcamp.com/track/a",
    )
    seen = []
    assert dig.expand_link_hubs([track], on_progress=lambda *args: seen.append(args)) == 0
    assert seen == []


def test_a_host_that_stops_answering_is_asked_twice_and_no_more(monkeypatch):
    """A playlist names the same smart-link domain over and over.

    Each attempt costs the full connect timeout, so one dead host used to be one
    wasted wait per track that mentioned it - minutes, on a real crate.
    """

    asked = []

    def never_answers(url, session, timeout=10.0):
        asked.append(url)
        return None  # the host said nothing at all

    monkeypatch.setattr("dj_digger.gates.hubs.inspect_link_page", never_answers)
    monkeypatch.setattr("dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession())

    # Forty tracks, all pointing at the same dead host.
    tracks = [
        Track(
            title=f"T{index}",
            permalink_url=f"https://soundcloud.com/a/{index}",
            id=index,
            purchase_url=f"https://smartlinks.gone.example/l/{index}",
        )
        for index in range(40)
    ]

    assert dig.expand_link_hubs(tracks) == 0
    # Not exactly HOST_FAILURE_LIMIT: whatever the pool already had in flight
    # when the count reached the limit still finishes. The guarantee is that no
    # further request is started, which bounds it by the worker count.
    assert len(asked) <= dig.HUB_WORKERS
    assert len(asked) < len(tracks)


def test_a_host_that_answers_is_asked_every_time(monkeypatch):
    """The breaker must not trip on a working host that has nothing to give."""

    asked = []

    def answers_with_nothing(url, session, timeout=10.0):
        asked.append(url)
        return gate_models.LinkPageInspection()

    monkeypatch.setattr("dj_digger.gates.hubs.inspect_link_page", answers_with_nothing)
    monkeypatch.setattr("dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession())

    tracks = [
        Track(
            title=f"T{index}",
            permalink_url=f"https://soundcloud.com/a/{index}",
            id=index,
            purchase_url=f"https://hypeddit.com/track/{index}",
        )
        for index in range(5)
    ]

    dig.expand_link_hubs(tracks)
    assert len(asked) == 5


def test_hub_expansion_reports_progress(monkeypatch):
    monkeypatch.setattr(
        "dj_digger.gates.hubs.inspect_link_page",
        lambda *a, **kw: gate_models.LinkPageInspection(
            shops=(("https://label.bandcamp.com/album/a", "Bandcamp"),)
        ),
    )
    monkeypatch.setattr("dj_digger.soundcloud.create_requests_session", lambda **kw: FakeSession())

    seen = []
    dig.expand_link_hubs([a_hub_track()], on_progress=lambda *args: seen.append(args))
    assert seen == [(dig.STAGE_HUBS, 1, 1)]


class FakeSession:
    def close(self):
        pass

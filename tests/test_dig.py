import json

import pytest

from dj_digger import dig
from dj_digger.models import Crate, Track


def a_crate(count=1):
    return Crate(
        source="x",
        tracks=[
            Track(title=f"T{index}", permalink_url=f"https://soundcloud.com/a/{index}")
            for index in range(count)
        ],
    )


def page_with_ids(*ids):
    payload = [{"hydratable": "playlist", "data": {"track_count": len(ids), "tracks": [{"id": i} for i in ids]}}]
    return (
        "<html><head><title>Saved | SoundCloud</title></head><body><script>"
        "window.__sc_hydration = " + json.dumps(payload) + ";</script></body></html>"
    )


def test_a_soundcloud_link_goes_through_the_api(monkeypatch):
    seen = {}

    def fake_collect(url, **kwargs):
        seen["url"] = url
        seen["limit"] = kwargs.get("limit")
        return a_crate()

    monkeypatch.setattr("dj_digger.soundcloud.collect_tracks", fake_collect)
    dig.dig("https://soundcloud.com/a/sets/b", limit=5)

    assert seen == {"url": "https://soundcloud.com/a/sets/b", "limit": 5}


def test_a_missing_target_is_rejected_with_a_readable_message():
    with pytest.raises(dig.TargetNotFound, match="neither a soundcloud.com link"):
        dig.dig("definitely-not-here.html")


def test_surrounding_whitespace_is_forgiven(monkeypatch):
    """People paste links with a trailing newline all the time."""

    monkeypatch.setattr("dj_digger.soundcloud.collect_tracks", lambda url, **kw: a_crate())
    dig.dig("  https://soundcloud.com/a/sets/b\n")


def test_a_saved_page_with_track_ids_uses_the_batch_hydrator(tmp_path, monkeypatch):
    path = tmp_path / "saved.html"
    path.write_text(page_with_ids(11, 22, 33), encoding="utf-8")

    hydrated = {}

    def fake_hydrate(ids, **kwargs):
        hydrated["ids"] = list(ids)
        return a_crate(3).tracks

    monkeypatch.setattr("dj_digger.soundcloud.hydrate_ids", fake_hydrate)
    monkeypatch.setattr(
        "dj_digger.html_fallback.scrape_track_page",
        lambda *a, **k: pytest.fail("should not scrape when ids are available"),
    )

    crate = dig.dig(str(path))

    assert hydrated["ids"] == [11, 22, 33]
    assert crate.declared_count == 3
    assert crate.title == "saved"


def test_the_limit_applies_before_hydration(tmp_path, monkeypatch):
    path = tmp_path / "saved.html"
    path.write_text(page_with_ids(1, 2, 3, 4, 5), encoding="utf-8")

    hydrated = {}
    monkeypatch.setattr(
        "dj_digger.soundcloud.hydrate_ids",
        lambda ids, **kw: hydrated.setdefault("ids", list(ids)) or a_crate(2).tracks,
    )
    dig.dig(str(path), limit=2)

    assert hydrated["ids"] == [1, 2]


def test_a_page_without_ids_falls_back_to_scraping(tmp_path, monkeypatch):
    path = tmp_path / "anchors.html"
    path.write_text(
        '<a href="https://soundcloud.com/artist/one">a</a>'
        '<a href="https://soundcloud.com/artist/two">b</a>',
        encoding="utf-8",
    )

    scraped = []
    monkeypatch.setattr(
        "dj_digger.html_fallback.scrape_track_page",
        lambda url, session, timeout: scraped.append(url)
        or Track(title="scraped", permalink_url=url),
    )

    crate = dig.dig(str(path), delay=0)

    assert sorted(scraped) == [
        "https://soundcloud.com/artist/one",
        "https://soundcloud.com/artist/two",
    ]
    assert len(crate.tracks) == 2


def test_an_empty_page_yields_an_empty_crate(tmp_path):
    path = tmp_path / "empty.html"
    path.write_text("<html><body>nothing here</body></html>", encoding="utf-8")
    assert dig.dig(str(path), delay=0).tracks == []


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


def test_scraping_reports_progress_per_page(tmp_path, monkeypatch):
    path = tmp_path / "anchors.html"
    path.write_text('<a href="https://soundcloud.com/artist/one">a</a>', encoding="utf-8")
    monkeypatch.setattr(
        "dj_digger.html_fallback.scrape_track_page",
        lambda url, session, timeout: Track(title="s", permalink_url=url),
    )

    seen = []
    dig.dig(str(path), delay=0, on_progress=lambda *args: seen.append(args))

    assert (dig.STAGE_PAGES, 1, 1) in seen


def test_default_options():
    options = dig.DigOptions()
    assert (options.limit, options.timeout, options.delay) == (None, 20.0, 0.5)

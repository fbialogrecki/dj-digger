import json

import pytest

from dj_digger import library
from dj_digger.models import Crate, Track


@pytest.fixture
def crates_in_tmp(tmp_path):
    """The redirect itself lives in conftest, autouse for every test."""

    return tmp_path / "crates"


def a_crate(count=3, *, source="https://soundcloud.com/a/sets/b", title="A crate"):
    return Crate(
        source=source,
        title=title,
        tracks=[
            Track(
                title=f"T{index}",
                permalink_url=f"https://soundcloud.com/a/{index}",
                id=100 + index,
                genre="Techno",
            )
            for index in range(count)
        ],
    )


def test_a_crate_survives_a_round_trip():
    record = library.CrateRecord.from_crate(a_crate())
    library.save(record)

    reloaded = library.load(record.slug)
    assert reloaded.title == "A crate"
    assert [track.id for track in reloaded.tracks] == [100, 101, 102]
    assert reloaded.tracks[0].genre == "Techno"
    assert reloaded.imported_at


def test_the_same_source_overwrites_instead_of_duplicating():
    library.save(library.CrateRecord.from_crate(a_crate(2)))
    library.save(library.CrateRecord.from_crate(a_crate(5)))

    crates = library.list_crates()
    assert len(crates) == 1
    assert len(crates[0].tracks) == 5


def test_different_sources_get_different_slugs():
    first = library.slug_for("https://soundcloud.com/a/sets/b")
    second = library.slug_for("https://soundcloud.com/a/sets/c")
    assert first != second
    assert first == library.slug_for("https://soundcloud.com/a/sets/b")


def test_slugs_stay_readable_and_filesystem_safe():
    slug = library.slug_for("https://soundcloud.com/antarcticae/sets/techno-vinyl")
    assert slug.startswith("antarcticae-sets-techno-vinyl-")
    assert "/" not in slug and " " not in slug


def test_crates_are_listed_by_title():
    library.save(library.CrateRecord.from_crate(a_crate(1, source="s://z", title="Zulu")))
    library.save(library.CrateRecord.from_crate(a_crate(1, source="s://a", title="alpha")))
    assert [record.title for record in library.list_crates()] == ["alpha", "Zulu"]


def test_removed_tracks_disappear_from_active_tracks():
    record = library.CrateRecord.from_crate(a_crate(3))
    record.remove("101")

    assert [track.id for track in record.active_tracks] == [100, 102]
    library.save(record)
    assert library.load(record.slug).removed_track_keys == ["101"]


def test_restoring_brings_a_track_back():
    record = library.CrateRecord.from_crate(a_crate(2))
    record.remove("100")
    record.restore("100")
    assert [track.id for track in record.active_tracks] == [100, 101]


def test_removing_the_same_track_twice_is_harmless():
    record = library.CrateRecord.from_crate(a_crate(2))
    record.remove("100")
    record.remove("100")
    assert record.removed_track_keys == ["100"]


def test_refresh_replaces_tracks_but_keeps_local_deletions():
    """A refresh must not resurrect what you deleted."""

    record = library.CrateRecord.from_crate(a_crate(3))
    record.remove("101")

    library.refresh(record, a_crate(5, title="A crate, now longer"))

    assert len(record.tracks) == 5
    assert record.removed_track_keys == ["101"]
    assert [track.id for track in record.active_tracks] == [100, 102, 103, 104]
    assert record.title == "A crate, now longer"
    assert record.refreshed_at


def test_refresh_clears_the_partial_flag():
    record = library.CrateRecord.from_crate(a_crate(1), partial=True)
    assert record.partial is True
    library.refresh(record, a_crate(2))
    assert record.partial is False


def test_delete_removes_the_file():
    record = library.CrateRecord.from_crate(a_crate())
    library.save(record)
    library.delete(record.slug)
    assert library.list_crates() == []


def test_deleting_something_that_is_not_there_is_harmless():
    library.delete("no-such-crate")


def test_an_unreadable_crate_is_skipped_not_fatal(crates_in_tmp):
    library.save(library.CrateRecord.from_crate(a_crate(1, source="s://good")))
    crates_in_tmp.mkdir(parents=True, exist_ok=True)
    (crates_in_tmp / "broken.json").write_text("{not json", encoding="utf-8")

    assert len(library.list_crates()) == 1


def test_unknown_fields_in_a_stored_track_are_ignored(crates_in_tmp):
    """A crate written by a newer version must still load."""

    record = library.CrateRecord.from_crate(a_crate(1))
    library.save(record)

    raw = json.loads(record.path.read_text(encoding="utf-8"))
    raw["tracks"][0]["something_from_the_future"] = 42
    record.path.write_text(json.dumps(raw), encoding="utf-8")

    assert library.load(record.slug).tracks[0].id == 100


def test_scraped_extra_links_come_back_as_tuples():
    crate = a_crate(1)
    crate.tracks[0].extra_links = [("https://x.bandcamp.com/track/y", "Buy")]
    record = library.CrateRecord.from_crate(crate)
    library.save(record)

    assert library.load(record.slug).tracks[0].extra_links == [
        ("https://x.bandcamp.com/track/y", "Buy")
    ]

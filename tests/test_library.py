import pytest

from dj_digger import library
from dj_digger.db import database
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

    reloaded = library.load(record.source)
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


def test_crates_are_listed_by_title():
    library.save(library.CrateRecord.from_crate(a_crate(1, source="s://z", title="Zulu")))
    library.save(library.CrateRecord.from_crate(a_crate(1, source="s://a", title="alpha")))
    assert [record.title for record in library.list_crates()] == ["alpha", "Zulu"]


def test_removed_tracks_disappear_from_active_tracks():
    record = library.CrateRecord.from_crate(a_crate(3))
    record.remove("101")

    assert [track.id for track in record.active_tracks] == [100, 102]
    library.save(record)
    assert library.load(record.source).removed_track_keys == ["101"]


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
    # 103 and 104 arrived with this refresh, so they sort to the top.
    assert [track.id for track in record.active_tracks] == [103, 104, 100, 102]
    assert record.title == "A crate, now longer"
    assert record.refreshed_at


def test_refresh_marks_what_it_brought_in_and_puts_it_first():
    record = library.CrateRecord.from_crate(a_crate(2))
    assert record.new_track_keys == [], "a first import has nothing to compare against"

    library.refresh(record, a_crate(4))

    assert record.new_track_keys == ["102", "103"]
    assert [track.id for track in record.active_tracks] == [102, 103, 100, 101]


def test_a_refresh_that_brought_nothing_keeps_the_previous_marks():
    """Pressing r twice must not lose what the first press turned up."""

    record = library.CrateRecord.from_crate(a_crate(2))
    library.refresh(record, a_crate(3))
    library.refresh(record, a_crate(3))

    assert record.new_track_keys == ["102"]


def test_the_new_marks_survive_a_round_trip():
    record = library.CrateRecord.from_crate(a_crate(2))
    library.refresh(record, a_crate(3))
    library.save(record)

    assert library.load(record.source).new_track_keys == ["102"]


def test_refresh_clears_the_partial_flag():
    record = library.CrateRecord.from_crate(a_crate(1), partial=True)
    assert record.partial is True
    library.refresh(record, a_crate(2))
    assert record.partial is False


def test_delete_removes_the_crate():
    record = library.CrateRecord.from_crate(a_crate())
    library.save(record)
    library.delete(record.source)
    assert library.list_crates() == []


def test_deleting_something_that_is_not_there_is_harmless():
    library.delete("no-such-crate")


def test_delete_finds_the_crate_by_its_source_with_no_file_involved():
    """Delete used to happen entirely inside `if the JSON file exists`.

    A crate whose row outlived its file could not be removed at all: the sidebar
    drew it, X ran and changed nothing, and it was back on the next reload. Since
    0.9 there is no file - the source is the primary key of the stored records.
    """

    record = library.CrateRecord.from_crate(a_crate())
    library.save(record)
    assert [rec.source for rec in library.list_crates()] == [record.source]

    library.delete(record.source)

    assert library.list_crates() == []


def test_unknown_fields_in_a_stored_track_are_ignored(crates_in_tmp):
    """A crate written by a newer version must still load."""

    record = library.CrateRecord.from_crate(a_crate(1))
    raw = record.to_json()
    raw["tracks"][0]["something_from_the_future"] = 42
    database().save_crate(raw)

    assert library.load(record.source).tracks[0].id == 100


def test_scraped_extra_links_come_back_as_tuples():
    crate = a_crate(1)
    crate.tracks[0].extra_links = [("https://x.bandcamp.com/track/y", "Buy")]
    record = library.CrateRecord.from_crate(crate)
    library.save(record)

    assert library.load(record.source).tracks[0].extra_links == [
        ("https://x.bandcamp.com/track/y", "Buy")
    ]


import library_db as library
from helpers import a_crate

from dj_digger import crate_models
from dj_digger.db import database
from dj_digger.services.library import LibraryService
from dj_digger.state import TrackState


def test_a_crate_survives_a_round_trip():
    record = crate_models.CrateRecord.from_crate(a_crate(genre="Techno"))
    library.save(record)

    reloaded = library.load(record.source)
    assert reloaded.title == "A crate"
    assert [track.id for track in reloaded.tracks] == [100, 101, 102]
    assert reloaded.tracks[0].genre == "Techno"
    assert reloaded.imported_at


def test_the_same_source_overwrites_instead_of_duplicating():
    library.save(crate_models.CrateRecord.from_crate(a_crate(2)))
    library.save(crate_models.CrateRecord.from_crate(a_crate(5)))

    crates = library.list_crates()
    assert len(crates) == 1
    assert len(crates[0].tracks) == 5


def test_headers_are_listed_by_title():
    zulu = crate_models.CrateRecord.from_crate(a_crate(1, source="s://z", title="Zulu"), partial=True)
    zulu.refreshed_at = "2099-01-01T00:00:00+00:00"  # SQLite returns the most recently updated first.
    library.save(zulu)
    library.save(crate_models.CrateRecord.from_crate(a_crate(1, source="s://a", title="alpha")))

    headers = LibraryService(TrackState()).headers()

    assert [header.title for header in headers] == ["alpha", "Zulu"]
    assert headers[1].partial is True
    assert headers[0].updated, "imported_at is stamped on save and comes back as updated"
    assert not hasattr(headers[0], "tracks")


def test_removed_tracks_disappear_from_active_tracks():
    record = crate_models.CrateRecord.from_crate(a_crate(3))
    record.remove("101")

    assert [track.id for track in record.active_tracks] == [100, 102]
    library.save(record)
    assert library.load(record.source).removed_track_keys == ["101"]


def test_removing_the_same_track_twice_is_harmless():
    record = crate_models.CrateRecord.from_crate(a_crate(2))
    record.remove("100")
    record.remove("100")
    assert record.removed_track_keys == ["100"]


def test_delete_by_source_is_idempotent():
    service = LibraryService(TrackState())
    record = crate_models.CrateRecord.from_crate(a_crate())
    library.save(record)
    assert [header.source for header in service.headers()] == [record.source]
    service.delete(record.source)
    assert service.headers() == []
    service.delete(record.source)
    assert service.headers() == []


def test_new_track_fields_survive_a_round_trip():
    crate = a_crate(1)
    crate.tracks[0].bpm = 128.0
    crate.tracks[0].key_signature = "F#m"
    crate.tracks[0].release_year = 2024
    crate.tracks[0].label_name = "Fixture Records"
    record = crate_models.CrateRecord.from_crate(crate)
    library.save(record)

    track = library.load(record.source).tracks[0]
    assert (track.bpm, track.key_signature, track.release_year, track.label_name) == (
        128.0, "F#m", 2024, "Fixture Records"
    )


def test_an_old_crate_without_bpm_loads():
    record = crate_models.CrateRecord.from_crate(a_crate(1))
    raw = record.to_json()
    for name in ("bpm", "key_signature", "release_year", "label_name"):
        raw["tracks"][0].pop(name)
    database().save_crate(raw)

    track = library.load(record.source).tracks[0]
    assert track.bpm is None and track.release_year is None
    assert track.key_signature == "" and track.label_name == ""


def test_unknown_fields_in_a_stored_track_are_ignored():
    """A crate written by a newer version must still load."""

    record = crate_models.CrateRecord.from_crate(a_crate(1))
    raw = record.to_json()
    raw["tracks"][0]["something_from_the_future"] = 42
    database().save_crate(raw)

    assert library.load(record.source).tracks[0].id == 100


def test_scraped_extra_links_come_back_as_tuples():
    crate = a_crate(1)
    crate.tracks[0].extra_links = [("https://x.bandcamp.com/track/y", "Buy")]
    record = crate_models.CrateRecord.from_crate(crate)
    library.save(record)

    assert library.load(record.source).tracks[0].extra_links == [
        ("https://x.bandcamp.com/track/y", "Buy")
    ]

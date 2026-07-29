from __future__ import annotations

import csv
import json

import pytest

from dj_digger import links
from dj_digger.models import Track

BANDCAMP_PURCHASE = 1011828484
UNKNOWN_PURCHASE = 846243631
DESCRIPTION_ONLY = 914785309
NO_LINK = 1187243407


def categories_for(track: Track) -> list:
    return [record.category for record in links.categorise(track)]


def test_purchase_url_on_a_known_store_wins(tracks_by_id):
    records = links.categorise(tracks_by_id[BANDCAMP_PURCHASE])
    assert [record.category for record in records] == ["bandcamp"]
    assert records[0].link_url.startswith("https://sinxergy.bandcamp.com/")


def test_purchase_url_on_an_unknown_domain_lands_in_others(tracks_by_id):
    """Artists hang interviews and press pieces off purchase_url too."""

    records = links.categorise(tracks_by_id[UNKNOWN_PURCHASE])
    assert [record.category for record in records] == ["others"]
    assert "nofu.de" in records[0].link_url
    assert records[0].link_text == "Info & Buy"


def test_store_link_is_found_in_the_description(tracks_by_id):
    records = links.categorise(tracks_by_id[DESCRIPTION_ONLY])
    assert "bandcamp" in [record.category for record in records]
    assert any("bandcamp.com" in record.link_url for record in records)


def test_track_without_any_link_still_produces_a_row(tracks_by_id):
    records = links.categorise(tracks_by_id[NO_LINK])
    assert len(records) == 1
    assert records[0].category == "others"
    assert records[0].link_text == links.NO_STORE_LINK
    assert records[0].link_url == records[0].track.permalink_url


def test_unknown_domains_in_the_description_are_ignored():
    """Only purchase_url earns an others row - descriptions are full of noise."""

    track = Track(
        title="Noise",
        permalink_url="https://soundcloud.com/a/b",
        description="follow me https://instagram.com/someone and https://example.com/x",
    )
    records = links.categorise(track)
    assert [record.category for record in records] == ["others"]
    assert records[0].link_text == links.NO_STORE_LINK


def test_trailing_punctuation_is_stripped_from_description_links():
    track = Track(
        title="Punctuated",
        permalink_url="https://soundcloud.com/a/b",
        description="out now (https://label.bandcamp.com/album/thing).",
    )
    records = links.categorise(track)
    assert records[0].link_url == "https://label.bandcamp.com/album/thing"


def test_extra_links_from_page_scraping_are_categorised():
    track = Track(
        title="Scraped",
        permalink_url="https://soundcloud.com/a/b",
        extra_links=[("https://www.beatport.com/track/x/1", "Buy on Beatport")],
    )
    assert categories_for(track) == ["beatport"]


def test_duplicate_urls_are_collapsed():
    url = "https://label.bandcamp.com/track/x"
    track = Track(
        title="Dupe",
        permalink_url="https://soundcloud.com/a/b",
        purchase_url=url,
        description=f"buy it here {url}",
    )
    assert len(links.categorise(track)) == 1


def test_only_the_best_link_per_store_survives():
    """purchase_url beats the label's homepage that the description name-drops."""

    track = Track(
        title="Two bandcamp links",
        permalink_url="https://soundcloud.com/a/b",
        purchase_url="https://label.bandcamp.com/album/the-actual-record",
        description="more of our stuff at https://label.bandcamp.com",
    )
    records = links.categorise(track)
    assert len(records) == 1
    assert records[0].link_url.endswith("/album/the-actual-record")


def test_different_stores_all_survive():
    track = Track(
        title="Everywhere",
        permalink_url="https://soundcloud.com/a/b",
        purchase_url="https://label.bandcamp.com/album/x",
        description="also on https://www.beatport.com/track/x/1 and https://hypeddit.com/x/y",
    )
    assert sorted(record.category for record in links.categorise(track)) == [
        "bandcamp",
        "beatport",
        "hypeddit",
    ]


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://foo.bandcamp.com/track/x", "bandcamp"),
        ("https://www.beatport.com/track/x/1", "beatport"),
        ("https://www.junodownload.com/products/x", "junodownload"),
        ("https://juno.co.uk/products/x", "junodownload"),
        ("https://hypeddit.com/x/y", "hypeddit"),
        ("https://hypd.it/abc", "hypeddit"),
        ("https://example.com/x", None),
    ],
)
def test_store_for_url(url, expected):
    assert links.store_for_url(url) == expected


def test_summary_keeps_every_category_key(tracks):
    summary = links.build_summary(links.categorise_all(tracks))
    assert list(summary) == links.CATEGORY_NAMES


def test_export_json_roundtrips(tmp_path, tracks):
    records = links.categorise_all(tracks)
    path = links.export_records(records, "json", tmp_path / "out.json")
    assert path is not None

    written = json.loads(path.read_text(encoding="utf-8"))
    assert sum(len(items) for items in written.values()) == len(records)

    reloaded = links.load_summary(path)
    assert len(reloaded) == len(records)
    assert {record.link_url for record in reloaded} == {
        record.link_url for record in records
    }


def test_export_json_keeps_the_v01_keys(tmp_path, tracks):
    path = links.export_records(links.categorise_all(tracks), "json", tmp_path / "out.json")
    entry = next(item for items in json.loads(path.read_text()).values() for item in items)
    assert {"title", "track_url", "shop_link"} <= set(entry)


def test_export_csv_is_flat(tmp_path, tracks):
    records = links.categorise_all(tracks)
    path = links.export_records(records, "csv", tmp_path / "out.csv")
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(records)
    assert rows[0]["category"] in links.CATEGORY_NAMES


def test_export_none_writes_nothing(tmp_path, tracks):
    assert links.export_records(links.categorise_all(tracks), "none", tmp_path / "x") is None
    assert not (tmp_path / "x").exists()


def test_load_summary_rejects_entries_without_a_track_url(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"bandcamp": [{"title": "no url"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="track_url"):
        links.load_summary(path)


def test_load_summary_rejects_a_non_mapping(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        links.load_summary(path)

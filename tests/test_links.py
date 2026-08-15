import csv
import json

import pytest

from dj_digger import links
from dj_digger.models import Track, parse_tags

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
    """A track with no actionable link is visible but not labelled as a download."""

    records = links.categorise(tracks_by_id[NO_LINK])
    assert len(records) == 1
    assert records[0].category == "no-link"
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
    assert [record.category for record in records] == ["no-link"]
    assert records[0].link_text == links.NO_STORE_LINK


@pytest.mark.parametrize(
    "url",
    [
        "https://open.spotify.com/track/x",
        "https://youtube.com/watch?v=x",
        "https://linktr.ee/label",
        "https://dawningrecords.link/abc",
    ],
)
def test_streaming_and_smart_links_in_a_description_are_dropped(url):
    """Every description carries the label's linktree and Spotify - that is not a find."""

    track = Track(
        title="Promo boilerplate",
        permalink_url="https://soundcloud.com/a/b",
        description=f"follow us, stream here {url}",
    )
    assert [record.category for record in links.categorise(track)] == ["no-link"]


def test_the_same_links_do_count_when_they_are_the_purchase_field():
    track = Track(
        title="Explicit",
        permalink_url="https://soundcloud.com/a/b",
        purchase_url="https://dawningrecords.link/abc",
    )
    assert [record.category for record in links.categorise(track)] == ["smartlink"]


def test_a_buyable_link_is_still_harvested_from_a_description():
    track = Track(
        title="Buried treasure",
        permalink_url="https://soundcloud.com/a/b",
        description="grab it at https://label.bandcamp.com/album/x and follow https://linktr.ee/y",
    )
    assert [record.category for record in links.categorise(track)] == ["bandcamp"]


def test_present_categories_skips_the_empty_ones(tracks):
    present = links.present_categories(links.categorise_all(tracks))
    assert present
    assert set(present) <= set(links.CATEGORY_NAMES)
    assert all(links.count_by_category(links.categorise_all(tracks))[name] for name in present)
    # Canonical order is preserved so the TUI number keys stay stable.
    assert present == [name for name in links.CATEGORY_NAMES if name in present]


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
        "gate",
    ]


def test_a_free_download_on_soundcloud_beats_the_shops():
    track = Track(
        title="Handed out",
        permalink_url="https://soundcloud.com/a/b",
        downloadable=True,
        has_downloads_left=True,
        download_url="https://api-v2.soundcloud.com/tracks/1/download",
        purchase_url="https://label.bandcamp.com/album/x",
    )
    records = links.categorise(track)
    assert [record.category for record in records] == ["soundcloud", "bandcamp"]
    assert records[0].link_text == links.FREE_DOWNLOAD
    assert records[0].link_url == track.download_url


def test_download_flags_without_a_url_are_presented_as_soundcloud_download():
    track = Track(
        title="No direct endpoint",
        permalink_url="https://soundcloud.com/a/b",
        downloadable=True,
        has_downloads_left=True,
    )
    assert categories_for(track) == ["soundcloud"]


def test_a_used_up_free_download_is_not_offered():
    """downloadable stays true after the artist's quota runs out, so it lies alone."""

    track = Track(
        title="All gone",
        permalink_url="https://soundcloud.com/a/b",
        downloadable=True,
        has_downloads_left=False,
        purchase_url="https://label.bandcamp.com/album/x",
    )
    assert categories_for(track) == ["bandcamp"]


def test_group_by_track_puts_the_best_link_first():
    track = Track(
        title="Everywhere",
        permalink_url="https://soundcloud.com/a/b",
        purchase_url="https://hypeddit.com/x/y",
        description="also at https://label.bandcamp.com/album/x",
    )
    other = Track(title="Alone", permalink_url="https://soundcloud.com/c/d")
    groups = links.group_by_track(links.categorise_all([track, other]))
    assert [[record.category for record in group] for group in groups] == [
        ["bandcamp", "gate"],
        ["no-link"],
    ]


def test_group_by_track_keeps_the_order_tracks_arrived_in():
    tracks = [
        Track(title=str(index), permalink_url=f"https://soundcloud.com/a/{index}")
        for index in range(5)
    ]
    groups = links.group_by_track(links.categorise_all(tracks))
    assert [group[0].track.title for group in groups] == ["0", "1", "2", "3", "4"]


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://foo.bandcamp.com/track/x", "bandcamp"),
        ("https://www.beatport.com/track/x/1", "beatport"),
        ("https://pro.beatport.com/track/x/1", "beatport"),
        ("https://btprt.dj/abc", "beatport"),
        ("https://www.traxsource.com/title/x", "traxsource"),
        ("https://www.junodownload.com/products/x", "junodownload"),
        ("https://juno.co.uk/products/x", "junodownload"),
        ("https://itunes.apple.com/album/x", "apple"),
        ("https://music.apple.com/album/x", "apple"),
        ("https://boomkat.com/products/x", "shop"),
        ("https://www.redeyerecords.co.uk/x", "shop"),
        ("https://someone.gumroad.com/l/x", "shop"),
        # Every follow-to-download gate is the same chore, so they share a name.
        ("https://hypeddit.com/x/y", "gate"),
        ("https://hypd.it/abc", "gate"),
        ("https://wump.io/x", "gate"),
        ("https://theartistunion.com/tracks/x", "gate"),
        ("https://gaterush.me/abc", "gate"),
        ("https://droploud.com/gate/x", "gate"),
        ("https://distrokid.com/hyperfollow/x", "smartlink"),
        ("https://lnk.to/abc", "smartlink"),
        ("https://mezzanotte.lnk.to/abc", "smartlink"),
        ("https://ffm.to/abc", "smartlink"),
        ("https://fanlink.to/abc", "smartlink"),
        ("https://orcd.co/abc", "smartlink"),
        ("https://open.spotify.com/track/x", "streaming"),
        ("https://youtu.be/abc", "streaming"),
        ("https://example.com/x", None),
    ],
)
def test_store_for_url(url, expected):
    assert links.store_for_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # Labels buy their own domain on the .link TLD for exactly this purpose.
        "https://dawningrecords.link/abc",
        "https://music.lovestyle.link/abc",
    ],
)
def test_the_link_tld_is_treated_as_a_smart_link(url):
    assert links.store_for_url(url) == "smartlink"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil-bandcamp.com/x",
        "https://bandcamp.com.attacker.net/x",
        "https://notbeatport.com/x",
    ],
)
def test_lookalike_domains_do_not_match_a_store(url):
    """Matching has to respect domain boundaries, not just contain the name."""

    assert links.store_for_url(url) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('EBM "Hard Techno" Techno', ["EBM", "Hard Techno", "Techno"]),
        ("premiere techno house", ["premiere", "techno", "house"]),
        ("", []),
        # Artists leave quotes unclosed; shlex raises on this one.
        ('EBM "Hard Techno', ["EBM", "Hard", "Techno"]),
    ],
)
def test_parse_tags(raw, expected):
    assert parse_tags(raw) == expected


def test_genre_falls_back_to_the_first_tag():
    assert Track(title="t", permalink_url="u", genre="Techno", tags=["Acid"]).genre_label == "Techno"
    assert Track(title="t", permalink_url="u", tags=["Acid", "EBM"]).genre_label == "Acid"
    assert Track(title="t", permalink_url="u").genre_label == ""


def test_tags_come_off_the_api_payload():
    track = Track.from_api(
        {"title": "t", "permalink_url": "u", "tag_list": 'Techno "Hard Techno"'}
    )
    assert track.tags == ["Techno", "Hard Techno"]


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


# A purchase_url is whatever the artist typed into SoundCloud, and a summary
# file is whatever was on disk. Neither is trusted to be a web address.

HOSTILE_URLS = [
    "javascript:alert`1`",
    "data:text/html;base64,PHNjcmlwdD4=",
    "file:///etc/passwd",
    # The domain tables match on the host alone, so a known store name in front
    # of a hostile scheme used to earn a real category - and a category is what
    # makes a link openable.
    "file://bandcamp.com/etc/passwd",
    r"\\attacker\share\payload",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_a_hostile_scheme_never_earns_a_store_category(url):
    assert links.store_for_url(url) is None


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_a_hostile_purchase_url_never_becomes_an_openable_link(url):
    track = Track(
        title="Trap",
        permalink_url="https://soundcloud.com/artist/track",
        purchase_url=url,
        purchase_title="Buy",
    )
    records = links.categorise(track)

    assert [record.link_url for record in records] == [
        "https://soundcloud.com/artist/track"
    ]
    assert [record.category for record in records] == ["no-link"]


@pytest.mark.parametrize("field", ["track_url", "shop_link"])
def test_load_summary_rejects_a_link_that_is_not_http(tmp_path, field):
    item = {
        "title": "Trap",
        "track_url": "https://soundcloud.com/artist/track",
        "shop_link": "https://bandcamp.com/album/x",
        "link_text": "Buy",
    }
    item[field] = "file:///etc/passwd"
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"bandcamp": [item]}), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        links.load_summary(path)


def test_a_yaml_summary_says_what_happened_rather_than_failing_to_parse(tmp_path):
    """0.5 and earlier could write these; the message has to be better than a
    complaint about a colon on line one."""

    path = tmp_path / "soundcloud_links.yaml"
    path.write_text("bandcamp:\n  - title: Trap\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no longer reads"):
        links.load_summary(path)


def test_yaml_is_not_offered_as_an_export_format():
    assert "yaml" not in links.EXPORT_FORMATS
    assert links.EXPORT_FORMATS == ["json", "csv", "none"]

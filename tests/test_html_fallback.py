from helpers import page_with_hydration

from dj_digger import html_fallback


def test_hydration_gives_every_track_id_in_playlist_order():
    payload = [
        {"hydratable": "anonymousId", "data": "abc"},
        {
            "hydratable": "playlist",
            "data": {
                "track_count": 3,
                "tracks": [
                    {"id": 30, "kind": "track"},
                    {
                        "id": 10,
                        "kind": "track",
                        "permalink_url": "https://soundcloud.com/a/ten",
                    },
                    {"id": 20, "kind": "track"},
                ],
            },
        },
    ]
    ids, urls, declared = html_fallback.extract_from_hydration(
        html_fallback.parse_hydration(page_with_hydration(payload))
    )

    assert ids == [30, 10, 20]
    assert urls == {"https://soundcloud.com/a/ten"}
    assert declared == 3


def test_hydration_deduplicates_repeated_ids():
    payload = [
        {
            "hydratable": "playlist",
            "data": {"tracks": [{"id": 1}, {"id": 2}, {"id": 1}]},
        }
    ]
    ids, _, _ = html_fallback.extract_from_hydration(
        html_fallback.parse_hydration(page_with_hydration(payload))
    )
    assert ids == [1, 2]


def test_hydration_handles_a_single_track_page():
    payload = [
        {
            "hydratable": "sound",
            "data": {"id": 99, "permalink_url": "https://soundcloud.com/a/b"},
        }
    ]
    ids, urls, _ = html_fallback.extract_from_hydration(
        html_fallback.parse_hydration(page_with_hydration(payload))
    )
    assert ids == [99]
    assert urls == {"https://soundcloud.com/a/b"}


def test_hydration_stops_at_the_end_of_the_array():
    """raw_decode has to ignore the JavaScript that follows the payload."""

    html = (
        "<script>window.__sc_hydration = [{\"hydratable\":\"playlist\","
        "\"data\":{\"tracks\":[{\"id\":5}]}}];"
        "window.something = [1,2,3];</script>"
    )
    ids, _, _ = html_fallback.extract_from_hydration(html_fallback.parse_hydration(html))
    assert ids == [5]


def test_missing_hydration_is_not_an_error():
    assert html_fallback.parse_hydration("<html><body>nothing</body></html>") is None


def test_broken_hydration_is_not_an_error():
    assert html_fallback.parse_hydration("<script>window.__sc_hydration = [{oops;</script>") is None


def test_anchors_are_filtered_down_to_real_tracks():
    html = """
    <a href="/artist/a-real-track">track</a>
    <a href="https://soundcloud.com/artist/another-track">track</a>
    <a href="https://soundcloud.com/artist/sets/some-playlist">playlist</a>
    <a href="https://soundcloud.com/artist/likes">likes</a>
    <a href="https://soundcloud.com/discover/whatever">discover</a>
    <a href="https://soundcloud.com/artist">profile</a>
    <a href="https://example.com/elsewhere">elsewhere</a>
    """
    assert html_fallback.parse_track_links_from_html(html) == {
        "https://soundcloud.com/artist/a-real-track",
        "https://soundcloud.com/artist/another-track",
    }


def test_playlist_context_parameter_is_stripped():
    html = '<a href="https://soundcloud.com/artist/track?in=someone/sets/thing">t</a>'
    assert html_fallback.parse_track_links_from_html(html) == {
        "https://soundcloud.com/artist/track"
    }


def test_declared_count_comes_from_metadata_first():
    html = '<meta itemprop="numTracks" content="42"><p>7 tracks</p>'
    assert html_fallback.extract_declared_track_count(html) == 42


def test_declared_count_falls_back_to_page_text():
    assert html_fallback.extract_declared_track_count("<p>Contains tracks 17</p>") == 17
    assert html_fallback.extract_declared_track_count("<p>12 tracks</p>") == 12
    assert html_fallback.extract_declared_track_count("<p>nothing here</p>") is None


def test_load_playlist_reads_ids_and_urls(tmp_path):
    payload = [{"hydratable": "playlist", "data": {"track_count": 2, "tracks": [{"id": 1}, {"id": 2}]}}]
    path = tmp_path / "saved.html"
    path.write_text(page_with_hydration(payload), encoding="utf-8")

    ids, urls, declared = html_fallback.load_playlist(path)
    assert ids == [1, 2]
    assert urls == []
    assert declared == 2


def test_title_loses_the_soundcloud_suffix():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<title>Great Set | SoundCloud</title>", "html.parser")
    assert html_fallback.extract_title(soup) == "Great Set"


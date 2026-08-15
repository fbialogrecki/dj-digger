from unittest.mock import MagicMock

import requests

from dj_digger.gates import (
    resolve_gate_download_url,
    store_links_on_page,
    resolve_hypeddit_download_url,
    resolve_toneden_download_url,
)


def test_resolve_hypeddit_var_in_html():
    session = MagicMock(spec=requests.Session)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = '<html><script>var download_url = "https://s3.amazonaws.com/bucket/track.wav";</script></html>'
    session.get.return_value = resp

    url = "https://hypeddit.com/track/abc1234"
    result = resolve_hypeddit_download_url(url, session)
    assert result == "https://s3.amazonaws.com/bucket/track.wav"


def test_resolve_toneden_api():
    session = MagicMock(spec=requests.Session)
    resp_page = MagicMock()
    resp_page.status_code = 200
    resp_page.text = '<html><script>window.__INITIAL_STATE__ = {"download_url": "https://cdn.toneden.io/track.mp3"};</script></html>'
    session.get.return_value = resp_page

    url = "https://www.toneden.io/artist/post/trackslug"
    result = resolve_toneden_download_url(url, session)
    assert result == "https://cdn.toneden.io/track.mp3"


def test_resolve_gate_download_url_routing():
    session = MagicMock(spec=requests.Session)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = 'var download_url = "https://s3.amazonaws.com/test.flac";'
    session.get.return_value = resp

    assert resolve_gate_download_url("https://hypeddit.com/test", session) == "https://s3.amazonaws.com/test.flac"
    assert resolve_gate_download_url("https://example.com/other", session) is None


def test_resolve_droploud_gate():
    session = MagicMock(spec=requests.Session)
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"stream_url": "/api/stream/4b0a4c1d-a3da-474d-8099-b63f3b0abe67"}
    session.get.return_value = resp

    url = "https://droploud.com/gate/4b0a4c1d-a3da-474d-8099-b63f3b0abe67"
    result = resolve_gate_download_url(url, session)
    assert result == "https://api.droploud.com/api/stream/4b0a4c1d-a3da-474d-8099-b63f3b0abe67"



class StubConfig:
    """A profile a gate form can be filled in from, without touching real config."""

    def __init__(self, email):
        self.user_name = "Music Listener"
        self.user_email = email

    def has_real_email(self):
        return not self.user_email.endswith(".invalid")

    def random_comment(self):
        return "Fire!"


def _gate_session():
    session = MagicMock(spec=requests.Session)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = 'var download_url = "https://s3.amazonaws.com/bucket/track.wav";'
    session.get.return_value = resp
    return session


def test_a_placeholder_email_is_warned_about_before_it_reaches_a_gate(caplog):
    """These resolvers post a name and an address to somebody else's server."""

    with caplog.at_level("WARNING"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/abc1234",
            _gate_session(),
            config=StubConfig("dj-digger@example.invalid"),
        )

    assert any("placeholder address" in record.message for record in caplog.records)


def test_an_address_the_user_set_is_submitted_without_complaint(caplog):
    with caplog.at_level("WARNING"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/abc1234",
            _gate_session(),
            config=StubConfig("dj@example.com"),
        )

    assert not any("placeholder address" in record.message for record in caplog.records)


class HubSession:
    """Answers a hub page and the redirect wrappers on it, without a network."""

    def __init__(self, page, redirects=None, landed="https://label.ampsuite.com/releases/links?id=1"):
        self.page = page
        self.redirects = redirects or {}
        self.landed = landed
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        response = MagicMock()
        response.close.return_value = None
        if url in self.redirects:
            response.status_code = 302
            response.headers = {"Location": self.redirects[url]}
            response.text = ""
            response.url = url
            return response
        response.status_code = 200
        response.headers = {}
        response.text = self.page
        response.url = self.landed
        return response


HUB_PAGE = """
<html><body>
  <a class="retailer-link" href="/releases/link-redirect?store_id=1"><span>Beatport</span></a>
  <a class="retailer-link" href="/releases/link-redirect?store_id=20"><span>Bandcamp</span></a>
  <a class="retailer-link" href="https://open.spotify.com/album/xyz"><span>Spotify</span></a>
  <a href="/about">About the label</a>
</body></html>
"""


def test_a_hub_page_gives_up_the_shops_behind_its_own_redirects():
    session = HubSession(
        HUB_PAGE,
        redirects={
            "https://label.ampsuite.com/releases/link-redirect?store_id=1":
                "https://www.beatport.com/release/know-your-place/7057750",
            "https://label.ampsuite.com/releases/link-redirect?store_id=20":
                "https://label.bandcamp.com/album/know-your-place",
        },
    )

    found = store_links_on_page("https://label.ampsuite.com/releases/links?id=1", session)

    assert [url for url, _text in found] == [
        "https://www.beatport.com/release/know-your-place/7057750",
        "https://label.bandcamp.com/album/know-your-place",
    ]
    assert [text for _url, text in found] == ["Beatport", "Bandcamp"]


def test_a_hub_page_does_not_offer_the_streaming_services_it_lists():
    """Spotify is on the page, but you cannot buy the record there."""

    session = HubSession(HUB_PAGE)
    found = store_links_on_page("https://label.ampsuite.com/releases/links?id=1", session)
    assert not any("spotify" in url for url, _text in found)


def test_a_page_that_hands_over_a_file_is_left_as_a_gate():
    """A real follow-to-download gate keeps its badge, shop link or not."""

    page = (
        '<html><body><a href="https://label.bandcamp.com/track/a">Buy</a>'
        "<button>Free Download</button></body></html>"
    )
    assert store_links_on_page("https://hypeddit.com/track/abc", HubSession(page)) == []


def test_a_shop_linked_twice_is_returned_once():
    page = (
        '<html><body><a href="https://label.bandcamp.com/album/a">Bandcamp</a>'
        '<a href="/go?store=1">Bandcamp</a></body></html>'
    )
    session = HubSession(
        page,
        redirects={"https://label.ampsuite.com/go?store=1": "https://label.bandcamp.com/album/a"},
    )
    found = store_links_on_page("https://label.ampsuite.com/releases/links?id=1", session)
    assert len(found) == 1


def test_an_unreachable_hub_changes_nothing():
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.RequestException("nope")
    assert store_links_on_page("https://label.ampsuite.com/x", session) == []

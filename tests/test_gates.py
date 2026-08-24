from unittest.mock import MagicMock

import pytest
import requests

from dj_digger import gates, spotify
from dj_digger.gates import (
    resolve_gate_download_url,
    resolve_gaterush_download_url,
    resolve_hypeddit_download_url,
    resolve_toneden_download_url,
    store_links_on_page,
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

    def __init__(self, email, gate_social_actions=True):
        self.user_name = "Music Listener"
        self.user_email = email
        self.gate_social_actions = gate_social_actions

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


def _stepping_gate_session():
    """A gate page with no download URL in it, so the step calls actually run.

    ``_gate_session`` hands over an S3 link straight from the HTML, which is the
    shortcut this resolver takes before it posts anything at all.
    """

    session = MagicMock(spec=requests.Session)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = (
        '<html><head><meta name="csrf-token" content="tok123"></head>'
        '<body><input name="fan_gate_id" value="42">'
        '<input name="nwSteps" value="email,sc"></body></html>'
    )
    session.get.return_value = resp
    post_resp = MagicMock()
    post_resp.status_code = 200
    post_resp.text = ""
    post_resp.json.return_value = {
        "status": "T",
        "download_status": True,
        "URL": "https://hypeddit.com/download/file.wav",
    }
    session.post.return_value = post_resp
    return session


def _posted_to(session, endpoint):
    """Every payload this session posted to an endpoint whose URL ends in ``endpoint``."""

    return [
        call.kwargs.get("data", {})
        for call in session.post.call_args_list
        if call.args and call.args[0].endswith(endpoint)
    ]


def gate_html(*, steps, spotify_value=""):
    spotify_input = (
        f'<input name="additional_sp_user_id[]" value="{spotify_value}">'
        if spotify_value
        else ""
    )
    return (
        '<html><head><meta name="csrf-token" content="tok123"></head><body>'
        '<input name="fan_gate_id" value="42">'
        '<input name="current_download_file_listner" value="gate-file">'
        f'<input name="nwSteps" value="{steps}">'
        '<input name="wrndk" value="42x9">'
        '<input name="gvf" value="0">'
        f"{spotify_input}</body></html>"
    )


def session_for_gate(page, download_payload=None, email_status="T"):
    session = MagicMock(spec=requests.Session)
    session.get.return_value = MagicMock(status_code=200, text=page)

    def post(url, **kwargs):
        response = MagicMock(status_code=200)
        if url.endswith("verifyEmailAddress"):
            response.json.return_value = {"status": email_status}
        elif url.endswith("/gate/download/ul"):
            response.json.return_value = download_payload or {
                "download_status": True,
                "URL": "https://hypeddit.com/download/file.wav",
            }
        else:
            response.json.return_value = {}
        return response

    session.post.side_effect = post
    return session


def test_placeholder_email_stops_before_hypeddit_receives_any_post():
    session = session_for_gate(gate_html(steps="email,sc"))

    with pytest.raises(RuntimeError, match="real email"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/zrw7vu",
            session,
            config=StubConfig("dj-digger@example.invalid"),
        )

    session.post.assert_not_called()


def test_spotify_artist_is_saved_before_hypeddit_download(monkeypatch):
    session = session_for_gate(
        gate_html(steps="sc,sp", spotify_value="ART|0oVDzp5DK2caqb6FuL2mhp")
    )
    saved = []
    monkeypatch.setattr(spotify, "save_uris", lambda uris: saved.extend(uris))

    result = resolve_hypeddit_download_url(
        "https://hypeddit.com/track/xngfus",
        session,
        config=StubConfig("dj@example.com", gate_social_actions=True),
    )

    assert saved == ["spotify:artist:0oVDzp5DK2caqb6FuL2mhp"]
    assert result == "https://hypeddit.com/download/file.wav"


def test_spotify_step_respects_the_social_actions_switch(monkeypatch):
    session = session_for_gate(
        gate_html(steps="sc,sp", spotify_value="ART|0oVDzp5DK2caqb6FuL2mhp")
    )
    monkeypatch.setattr(
        spotify,
        "save_uris",
        lambda uris: pytest.fail("Spotify must not be changed"),
    )

    with pytest.raises(RuntimeError, match="social actions"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/xngfus",
            session,
            config=StubConfig("dj@example.com", gate_social_actions=False),
        )

    session.post.assert_not_called()


def test_unknown_spotify_gate_action_stays_manual():
    session = session_for_gate(
        gate_html(steps="sp", spotify_value="PLAYLIST|abc")
    )

    with pytest.raises(RuntimeError, match="unsupported Spotify action"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/unknown",
            session,
            config=StubConfig("dj@example.com"),
        )

    session.post.assert_not_called()


def test_rejected_hypeddit_email_is_actionable():
    session = session_for_gate(gate_html(steps="email"), email_status="F")

    with pytest.raises(RuntimeError, match="rejected the configured email"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/email",
            session,
            config=StubConfig("dj@example.com"),
        )


def test_rejected_hypeddit_download_is_actionable():
    session = session_for_gate(
        gate_html(steps="sc"), download_payload={"download_status": False}
    )

    with pytest.raises(RuntimeError, match="did not unlock"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/rejected",
            session,
            config=StubConfig("dj@example.com"),
        )


def test_a_gate_is_told_about_the_repost_only_when_it_was_allowed():
    """Every version up to 0.8 sent these, and said so in no interface at all."""

    session = _stepping_gate_session()
    resolve_hypeddit_download_url(
        "https://hypeddit.com/track/abc1234",
        session,
        config=StubConfig("dj@example.com", gate_social_actions=True),
    )

    payloads = _posted_to(session, "/setSC")
    assert payloads, "the SoundCloud step should still run"
    assert payloads[0]["is_repost"] == 1
    assert payloads[0]["is_subscribe"] == 1
    assert payloads[0]["comment_sc"] == "Fire!"


def test_a_gate_gets_no_repost_no_follow_and_no_comment_when_it_was_refused():
    session = _stepping_gate_session()
    resolve_hypeddit_download_url(
        "https://hypeddit.com/track/abc1234",
        session,
        config=StubConfig("dj@example.com", gate_social_actions=False),
    )

    payloads = _posted_to(session, "/setSC")
    assert payloads, "the step itself still runs - the gate counts it"
    assert payloads[0]["is_repost"] == 0
    assert payloads[0]["is_subscribe"] == 0
    assert payloads[0]["comment_sc"] == ""
    # And nothing is written under your name on the YouTube step either.
    assert all(payload["comment_yt"] == "" for payload in _posted_to(session, "/setYT"))


def test_gaterush_posts_no_comment_when_social_actions_are_refused():
    session = MagicMock(spec=requests.Session)
    resp = MagicMock()
    resp.status_code = 200
    resp.text = 'var download_url = "https://s3.amazonaws.com/bucket/track.wav";'
    session.get.return_value = resp

    resolve_gaterush_download_url(
        "https://gaterush.me/someslug",
        session,
        config=StubConfig("dj@example.com", gate_social_actions=False),
    )

    assert _posted_to(session, "/save-comment/someslug") == []
    # The address is what the download is actually for, so it still goes.
    assert _posted_to(session, "/save-email/someslug")


def test_a_shop_page_that_merely_mentions_downloads_is_still_a_shop():
    """The word used to be matched anywhere on the page, footers included."""

    page = """
    <html><body>
      <a class="retailer-link" href="https://label.bandcamp.com/album/x">Bandcamp</a>
      <footer>Instant download with every vinyl order. Digital downloads FAQ.</footer>
      <script>var downloadTracker = init("download");</script>
    </body></html>
    """
    assert gates._offers_a_download(page) is False


def test_a_gate_in_another_language_is_recognised_as_a_gate():
    """A German or Spanish gate used to be rewritten into a list of shops."""

    for button in ("Herunterladen", "Descargar", "Télécharger", "Pobierz"):
        page = f'<html><body><button class="gate-cta">{button}</button></body></html>'
        assert gates._offers_a_download(page) is True, button


def test_a_download_button_is_still_found_when_it_is_a_submit_input():
    page = '<html><body><input type="submit" value="Free Download"></body></html>'
    assert gates._offers_a_download(page) is True


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


def test_hypeddit_smart_link_keeps_only_its_beatport_destination():
    page = """
    <html><head><title>Whiplash EP by Sota</title></head><body>
      <a class="hype-btn" href="https://www.beatport.com/release/whiplash/3629013">Buy</a>
      <a href="https://open.spotify.com/album/stream-only">Listen</a>
    </body></html>
    """

    found = store_links_on_page(
        "https://hypeddit.com/l87679",
        HubSession(page, landed="https://hypeddit.com/l87679"),
    )

    assert found == [
        ("https://www.beatport.com/release/whiplash/3629013", "Buy")
    ]


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
    """None, not []: the caller writes the host off after two of these."""

    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.RequestException("nope")
    assert store_links_on_page("https://label.ampsuite.com/x", session) is None


def test_a_host_that_answered_404_is_not_reported_as_unreachable():
    """Something replied. Only silence counts against a host."""

    session = MagicMock(spec=requests.Session)
    session.get.return_value = MagicMock(status_code=404, text="", url="https://label.ampsuite.com/x")
    assert store_links_on_page("https://label.ampsuite.com/x", session) == []


def test_an_address_on_our_own_network_is_never_fetched():
    session = MagicMock(spec=requests.Session)
    assert store_links_on_page("http://169.254.169.254/latest/meta-data/", session) == []
    session.get.assert_not_called()

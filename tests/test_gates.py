import requests
from unittest.mock import MagicMock
from dj_digger.gates import resolve_hypeddit_download_url, resolve_toneden_download_url, resolve_gate_download_url


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

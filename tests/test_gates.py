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


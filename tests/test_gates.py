import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from bs4 import BeautifulSoup

from dj_digger import gate_models
from dj_digger.config import DEFAULT_NAME
from dj_digger.gates import browser as gate_browser
from dj_digger.gates import hubs as gate_hubs
from dj_digger.gates import providers as gates
from dj_digger.gates.hubs import inspect_link_page
from dj_digger.gates.providers import (
    resolve_gate_download_url,
    resolve_gaterush_download_url,
    resolve_hypeddit_download_url,
    resolve_toneden_download_url,
)
from dj_digger.models import Track

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_hypeddit_gate_ignores_foreign_hot_or_not_audio_and_soundcloud_preview():
    inspection = gates.inspect_hypeddit_html(
        "https://hypeddit.com/sinexvsylum/starryeyed",
        fixture_html("hypeddit_gate_hot_or_not.html"),
    )

    assert inspection.kind == "gate"
    assert inspection.manifest is not None


def test_hypeddit_gate_uses_its_manifest_post_not_a_recommendation_file():
    session = session_for_gate(fixture_html("hypeddit_gate_hot_or_not.html"))

    result = resolve_hypeddit_download_url(
        "https://hypeddit.com/sinexvsylum/starryeyed",
        session,
        config=StubConfig("dj@example.com"),
    )

    assert result == "https://hypeddit.com/download/file.wav"
    assert len(_posted_to(session, "/gate/download/ul")) == 1


def test_soundcloud_preview_path_is_never_a_gate_file():
    assert (
        gates._clean_url(
            "https://cf-preview-media.sndcdn.com/preview/example.128.mp3"
        )
        is None
    )


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
    session = session_for_gate(gate_html(steps="sc"))

    assert resolve_gate_download_url(
        "https://hypeddit.com/test",
        session,
        config=StubConfig("dj@example.com"),
    ) == "https://hypeddit.com/download/file.wav"
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
    return session_for_gate(gate_html(steps="sc"))


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
    """A gate page used to exercise exact request payloads."""

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


def session_for_gate(page, download_payload=None):
    session = MagicMock(spec=requests.Session)
    session.get.return_value = MagicMock(status_code=200, text=page)

    def post(url, **kwargs):
        response = MagicMock(status_code=200)
        if url.endswith("/gate/download/ul"):
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

    with pytest.raises(RuntimeError, match="real email") as caught:
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/zrw7vu",
            session,
            config=StubConfig("dj-digger@example.invalid"),
        )

    assert type(caught.value).__name__ == "GateProfileRequired"
    session.post.assert_not_called()


def test_hypeddit_captcha_stays_an_actionable_manual_case():
    session = session_for_gate(
        gate_html(steps="email,sc").replace(
            "</body>", '<div class="g-recaptcha"></div></body>'
        )
    )

    with pytest.raises(RuntimeError, match="CAPTCHA.*browser completion"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/captcha",
            session,
            config=StubConfig("dj@example.com"),
        )

    session.post.assert_not_called()


def test_makeba_marks_desktop_social_steps_complete_without_pathway_posts():
    fixtures = Path(__file__).parent / "fixtures"
    page = (fixtures / "hypeddit_makeba.html").read_text(encoding="utf-8")
    response = json.loads(
        (fixtures / "hypeddit_makeba_download.json").read_text(encoding="utf-8")
    )
    session = session_for_gate(page, response)

    result = resolve_hypeddit_download_url(
        "https://hypeddit.com/corruptedmind/makeba",
        session,
        config=StubConfig("dj@example.com", gate_social_actions=True),
    )

    assert result == "https://hypeddit.com/download/fixture-makeba.wav"
    payload = _posted_to(session, "/gate/download/ul")[0]
    assert payload["skip_gate_steps[]"] == ["ig", "sc"]
    assert payload["additional_ig_user_id[]"] == ["fixture-instagram-user"]
    assert payload["additional_sc_user_id[]"] == ["fixture-soundcloud-user"]
    assert payload["external_id"] == "fixture-external-id"
    assert payload["wrndk"] == "fixture-wrndk"
    assert _posted_to(session, "/setGatePathway") == []
    assert _posted_to(session, "/setGatePathwayOr") == []


def test_gaterush_never_submits_a_placeholder_email():
    session = MagicMock(spec=requests.Session)

    with pytest.raises(RuntimeError, match="real email") as caught:
        resolve_gaterush_download_url(
            "https://gaterush.me/a-gate",
            session,
            config=StubConfig("dj-digger@example.invalid"),
        )

    assert type(caught.value).__name__ == "GateProfileRequired"
    session.get.assert_not_called()
    session.post.assert_not_called()


def test_spotify_step_is_a_click_through_marker():
    """Hypeddit clears its Spotify step through its own OAuth app and session.

    Nothing done with a user's own Spotify login reaches the gate, so the step
    is reported like the other click-throughs and no provider is called.
    """

    session = session_for_gate(
        gate_html(steps="sc,sp", spotify_value="ART|0oVDzp5DK2caqb6FuL2mhp")
    )

    result = resolve_hypeddit_download_url(
        "https://hypeddit.com/track/xngfus",
        session,
        config=StubConfig("dj@example.com"),
    )

    assert result
    payloads = _posted_to(session, "/gate/download/ul")
    assert len(payloads) == 1
    assert payloads[0]["skip_gate_steps[]"] == ["sc", "sp"]


def test_social_steps_respect_the_social_actions_switch():
    session = session_for_gate(
        gate_html(steps="sc,sp", spotify_value="ART|0oVDzp5DK2caqb6FuL2mhp")
    )

    with pytest.raises(RuntimeError, match="social steps"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/xngfus",
            session,
            config=StubConfig("dj@example.com", gate_social_actions=False),
        )

    session.post.assert_not_called()


def test_email_is_submitted_only_in_the_single_desktop_download_post():
    session = session_for_gate(gate_html(steps="email"))

    resolve_hypeddit_download_url(
        "https://hypeddit.com/track/email",
        session,
        config=StubConfig("dj@example.com"),
    )

    assert _posted_to(session, "/verifyEmailAddress") == []
    payloads = _posted_to(session, "/gate/download/ul")
    assert len(payloads) == 1
    assert payloads[0]["email"] == "dj@example.com"


def test_a_refused_unlock_is_retried_once_as_skippable():
    """The page's skipper buttons send is_skippable=1; a refusal earns one such retry."""

    session = session_for_gate(gate_html(steps="sc"))
    replies = iter([{"download_status": False}, {"download_status": True, "URL": "https://hypeddit.com/download/file.wav"}])
    real_post = session.post.side_effect

    def post(url, **kwargs):
        response = real_post(url, **kwargs)
        if url.endswith("/gate/download/ul"):
            response.json.return_value = next(replies)
        return response

    session.post.side_effect = post

    result = resolve_hypeddit_download_url(
        "https://hypeddit.com/track/retry", session, config=StubConfig("dj@example.com")
    )

    assert result == "https://hypeddit.com/download/file.wav"
    payloads = _posted_to(session, "/gate/download/ul")
    assert [p["is_skippable"] for p in payloads] == ["0", "1"]
    assert all(p["skip_gate_steps[]"] == ["sc"] for p in payloads)


def test_no_retry_when_nothing_was_skipped():
    session = session_for_gate(gate_html(steps="email"), download_payload={"download_status": False})

    with pytest.raises(RuntimeError, match="did not unlock"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/email-only", session, config=StubConfig("dj@example.com")
        )

    assert len(_posted_to(session, "/gate/download/ul")) == 1


def test_steps_select_groups_pick_the_cheapest_alternative():
    """dw beats a follow, a follow beats a provider login; email needs a real address."""

    page = gate_html(steps="sc,sp,email").replace(
        "</body>", '<input name="steps_select" value="dw|sc,sp|dz,email"></body>'
    )
    session = session_for_gate(page)

    resolve_hypeddit_download_url(
        "https://hypeddit.com/track/groups", session, config=StubConfig("dj@example.com")
    )

    payload = _posted_to(session, "/gate/download/ul")[0]
    assert payload["skip_gate_steps[]"] == ["sp"], "dw is not social, sp is the cheap half of sp|dz"
    assert payload["steps"] == "sc,sp,email", "the declared list is sent as the page declares it"
    assert payload["email"] == "dj@example.com"


def test_a_group_offering_only_provider_logins_still_needs_the_browser():
    page = gate_html(steps="dz").replace(
        "</body>", '<input name="steps_select" value="dz|ap"></body>'
    )
    session = session_for_gate(page)

    with pytest.raises(gate_models.GateAuthenticationRequired):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/oauth-only", session, config=StubConfig("dj@example.com")
        )


def test_hypeddit_flows_are_bounded_per_host_not_serialized():
    """Two workers on two gates used to queue behind one global lock."""

    import threading

    inside = threading.Barrier(2, timeout=2)

    def make_session():
        session = session_for_gate(gate_html(steps="sc"))
        real_get = session.get

        def get(url, **kwargs):
            inside.wait()  # both flows must be in here at the same time
            return real_get(url, **kwargs)

        session.get = get
        return session

    results = []

    def flow(slug):
        results.append(
            resolve_hypeddit_download_url(
                f"https://hypeddit.com/track/{slug}", make_session(), config=StubConfig("dj@example.com")
            )
        )

    workers = [threading.Thread(target=flow, args=(slug,)) for slug in ("one", "two")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)

    assert len(results) == 2 and all(results)


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


def test_download_flow_captcha_reply_is_typed_for_browser_completion():
    session = session_for_gate(
        gate_html(steps="sc"),
        download_payload={"download_status": False, "captcha_required": True},
    )

    with pytest.raises(gate_models.GateCaptchaRequired, match="CAPTCHA"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/captcha-reply",
            session,
            config=StubConfig("dj@example.com"),
        )

    assert len(_posted_to(session, "/gate/download/ul")) == 1


def test_gate_telemetry_is_best_effort_and_never_blocks_download():
    page = gate_html(steps="sc").replace(
        '<input name="gvf" value="0">',
        '<input name="gvf" value="0"><input name="gvt" value="visit-token">',
    )
    session = session_for_gate(page)

    original = session.post.side_effect

    def post(url, **kwargs):
        if url.endswith("/gate/ge"):
            raise requests.RequestException("telemetry down")
        return original(url, **kwargs)

    session.post.side_effect = post
    assert resolve_hypeddit_download_url(
        "https://hypeddit.com/track/telemetry",
        session,
        config=StubConfig("dj@example.com"),
    ) == "https://hypeddit.com/download/file.wav"
    assert len(_posted_to(session, "/gate/ge")) == 1
    assert len(_posted_to(session, "/gate/download/ul")) == 1


def test_missing_steps_is_a_hub_not_an_invented_email_soundcloud_gate():
    page = '<html><body><a href="https://label.bandcamp.com/track/a">Buy</a></body></html>'
    inspection = gates.inspect_hypeddit_html("https://hypeddit.com/a", page)

    assert inspection.kind == "hub"
    assert inspection.manifest is None


def test_global_captcha_asset_does_not_turn_a_smartlink_into_a_challenge():
    inspection = gates.inspect_hypeddit_html(
        "https://hypeddit.com/duxnbass/epitome",
        fixture_html("hypeddit_smartlink_captcha_asset.html"),
    )

    assert inspection.kind == "hub"
    assert {gates.store_for_url(url) for url, _label in inspection.shops} == {
        "bandcamp",
        "beatport",
    }


def test_duxnbass_fixture_is_a_removable_hub_with_only_purchase_stores():
    inspection = inspect_link_page(
        "https://hypeddit.com/duxnbass/epitome",
        HubSession(
            fixture_html("hypeddit_hub_shops.html"),
            landed="https://hypeddit.com/duxnbass/epitome",
        ),
    )

    assert inspection.recognized is True
    assert inspection.keep_original is False
    assert {gates.store_for_url(url) for url, _label in inspection.shops} == {
        "bandcamp",
        "beatport",
    }


def test_a_recognised_empty_smartlink_is_still_a_hub():
    inspection = gates.inspect_hypeddit_html(
        "https://hypeddit.com/empty",
        fixture_html("hypeddit_hub_empty.html"),
    )

    assert inspection.kind == "hub"
    assert inspection.shops == ()


def test_unknown_hypeddit_page_is_a_protocol_error_not_no_download():
    session = session_for_gate("<html><body>new client-rendered gate</body></html>")

    with pytest.raises(gate_models.GateProtocolChanged, match="no supported download manifest"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/future/gate", session, config=StubConfig("dj@example.com")
        )

    session.post.assert_not_called()


def test_ky9i8z_smartlink_id_does_not_turn_the_wrapper_into_a_gate():
    page = """
    <html><body>
      <input name="fan_gate_id" value="806138">
      <a href="https://hypeddit.com/track/nmqt0z" data-button_type="DOWNLOAD">Free 320kbps</a>
      <a href="https://www.beatport.com/release/ghetto-bass/2470877">Beatport</a>
      <a href="https://terrenceandphillip.bandcamp.com/">Bandcamp</a>
    </body></html>
    """

    inspection = gates.inspect_hypeddit_html(
        "https://hypeddit.com/link/ky9i8z", page
    )

    assert inspection.kind == "hub"
    assert inspection.manifest is None
    assert inspection.nested_gates == ("https://hypeddit.com/track/nmqt0z",)
    assert [url for url, _label in inspection.shops] == [
        "https://www.beatport.com/release/ghetto-bass/2470877",
        "https://terrenceandphillip.bandcamp.com/",
    ]


def test_gate_hosts_must_match_exactly():
    assert gates.can_resolve("https://www.hypeddit.com/track/ok") is True
    assert gates.can_resolve("https://hypeddit.com.attacker.example/track/no") is False
    assert gates.can_resolve("https://evil.toneden.io/post/no") is False


def test_hypeddit_page_redirect_to_localhost_is_blocked_before_second_get():
    session = MagicMock(spec=requests.Session)
    redirect = MagicMock(
        status_code=302,
        headers={"Location": "http://127.0.0.1:8080/admin"},
    )
    session.get.return_value = redirect

    with pytest.raises(gate_models.GateProtocolChanged, match="unsafe address"):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/x",
            session,
            config=StubConfig("dj@example.com"),
        )

    assert session.get.call_count == 1


def test_hypeddit_fallback_shares_the_soundcloud_browser_profile_lock(tmp_path):
    from dj_digger import auth

    assert auth.BROWSER_PROFILE_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(gate_models.GateUnavailable, match="profile is already in use"):
            gate_browser.download_hypeddit_in_browser(
                Track(title="T", permalink_url="https://soundcloud.com/a/t"),
                "https://hypeddit.com/track/x",
                tmp_path,
                None,
            )
    finally:
        auth.BROWSER_PROFILE_LOCK.release()


def test_hypeddit_browser_batch_uses_one_context_and_maps_each_tab_download(
    tmp_path, monkeypatch
):
    contexts = []

    class Download:
        def __init__(self, name, body):
            self.suggested_filename = name
            self.body = body

        def save_as(self, destination):
            Path(destination).write_bytes(self.body)

    class Page:
        def __init__(self, context):
            self.context = context
            self.url = "about:blank"
            self.handlers = {}
            self.opener = None

        def on(self, event, callback):
            self.handlers[event] = callback

        def goto(self, url, **_kwargs):
            self.url = url
            marker = url.rsplit("/", 1)[-1]
            self.handlers["download"](
                Download(f"{marker}.wav", f"RIFF-{marker}".encode())
            )

        def wait_for_timeout(self, _timeout):
            pass

    class Context:
        def __init__(self):
            self.pages = [Page(self)]
            self.handlers = {}

        def on(self, event, callback):
            self.handlers[event] = callback

        def new_page(self):
            page = Page(self)
            self.pages.append(page)
            self.handlers["page"](page)
            return page

        def clear_cookies(self, *, name):
            pass

    @contextmanager
    def browser_context(*_args, **_kwargs):
        context = Context()
        contexts.append(context)
        yield context

    monkeypatch.setattr("dj_digger.browser_session.sync_browser_context", browser_context)
    tracks = [
        Track(
            id=index,
            title=f"Track {index}",
            permalink_url=f"https://soundcloud.com/a/{index}",
        )
        for index in (1, 2)
    ]

    result = gate_browser.download_hypeddit_batch_in_browser(
        [
            (tracks[0], "https://hypeddit.com/track/one"),
            (tracks[1], "https://hypeddit.com/track/two"),
        ],
        tmp_path,
        None,
    )

    assert len(contexts) == 1
    assert len(contexts[0].pages) == 2
    assert result.failures == ()
    assert [key for key, _path in result.completed] == ["1", "2"]
    assert [path.read_bytes() for _key, path in result.completed] == [
        b"RIFF-one",
        b"RIFF-two",
    ]


class _Popup:
    """A window the gate opened: a provider page to look at, or its OAuth login."""

    def __init__(self, opener, url):
        self.opener = opener
        self.url = url
        self.closed = False
        self.polls = 0

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


class _Control:
    """What one selector matched; what happens on click is the page's business."""

    def __init__(self, page, selector, slide):
        self.page, self.selector, self.slide = page, selector, slide

    @property
    def first(self):
        return self

    def locator(self, selector):
        return _Control(self.page, selector, self.slide)

    def count(self):
        return self.page.count(self.selector, self.slide)

    def is_visible(self):
        return self.page.visible(self.selector)

    def get_attribute(self, name):
        return self.page.attribute(name, self.slide)

    def fill(self, value):
        self.page.fill(self.selector, value)

    def click(self, timeout=None, force=False):
        self.page.click(self.selector, self.slide)


_CLICK_STEPS = {"sc", "ig", "yt"}
_SOCIAL_PAGE = "https://soundcloud.com/artist"
_SPOTIFY_LOGIN = "https://accounts.spotify.com/authorize?client_id=x"
_SPOTIFY_CALLBACK = "https://hypeddit.com/spotify_callback?code=ok"
_GATE = "https://hypeddit.com/track/seven"
_HOT_OR_NOT = "https://hypeddit.com/hot-or-not/tech-house"


class _GatePage:
    """A desktop Hypeddit gate: Download reveals step slides, one current at a time.

    A follow link opens the provider's page; Spotify's Connect opens its
    login, which comes back to Hypeddit's callback by itself only when the
    profile is signed in - or when a person is at the window to sign in.
    """

    def __init__(
        self,
        context,
        *,
        steps=("sc", "sp", "ig", "email", "dw"),
        spotify_signed_in=True,
        callback_closes=True,
        captcha=False,
        person_finishes=False,
        late_popup=False,
        asks_name=False,
        detours=0,
    ):
        self.context = context
        self.url = "about:blank"
        self.detours = detours
        self.visited = []
        self.handlers = {}
        self.opener = None
        self.steps = list(steps)
        self.index = 0
        self.clicked = []
        self.email = None
        self.name = None
        self.asks_name = asks_name
        self.polls = 0
        self.popups = []
        self.pending = {n: 1 for n, kind in enumerate(self.steps) if kind in _CLICK_STEPS}
        self.spotify_signed_in = spotify_signed_in
        self.callback_closes = callback_closes
        self.captcha = captcha
        self.captcha_shown = False
        self.person_finishes = person_finishes
        self.late_popup = late_popup
        self.opening = None

    def on(self, event, callback):
        self.handlers[event] = callback

    def goto(self, url, **_kwargs):
        self.visited.append(url)
        if self.detours:
            self.detours -= 1
            url = _HOT_OR_NOT
        self.url = url

    @property
    def on_gate(self):
        return self.url != _HOT_OR_NOT

    def is_closed(self):
        return False

    @property
    def kind(self):
        return self.steps[self.index] if self.index < len(self.steps) else None

    def locator(self, selector):
        slide = self.index if selector == gate_browser.GATE_CURRENT_SLIDE else None
        return _Control(self, selector, slide)

    def count(self, selector, slide):
        if selector == gate_browser.GATE_CURRENT_SLIDE:
            return 1 if self.kind and self.on_gate else 0
        if selector == gate_browser.GATE_PENDING_ACTION:
            return self.pending.get(slide, 0)
        if selector == gate_browser.GATE_NAME_INPUT:
            return int(self.asks_name)
        return 1

    def visible(self, selector):
        if selector == gate_browser.GATE_START_BUTTON:
            return self.on_gate and not self.clicked
        if selector == gate_browser.GATE_CAPTCHA:
            return self.captcha_shown
        if selector == gate_browser.HYPEDDIT_DOWNLOAD_BUTTON:
            return self.kind == "dw"
        return True

    def attribute(self, name, slide):
        if name == "class":
            return f"{self.steps[slide]} fangate-slider-content"
        return str(slide)

    def fill(self, selector, value):
        if selector == gate_browser.GATE_EMAIL_INPUT:
            self.email = value
        elif selector == gate_browser.GATE_NAME_INPUT:
            self.name = value

    def click(self, selector, slide):
        self.clicked.append((self.kind, selector))
        if selector == gate_browser.GATE_PENDING_ACTION:
            self.pending[slide] -= 1
            self._open(_SOCIAL_PAGE)
        elif selector == gate_browser.GATE_NEXT_BUTTON:
            if not self.pending.get(slide):
                self.index += 1
        elif selector == gate_browser.GATE_CONNECT_BUTTON:
            if self.late_popup:
                self.opening = _SPOTIFY_LOGIN  # reaches the client on the next poll
            else:
                self._open(_SPOTIFY_LOGIN)
        elif selector == gate_browser.GATE_EMAIL_SUBMIT:
            if self.captcha:
                self.captcha_shown = True
            elif self.asks_name and not self.name:
                pass  # "Please enter your name." - the slide stays.
            else:
                self.index += 1
        elif selector == gate_browser.HYPEDDIT_DOWNLOAD_BUTTON:
            if gate_browser.GATE_DOWNLOAD_COOKIE in self.context.cookies:
                return  # Hypeddit answers download_status false; nothing arrives
            self.context.cookies.add(gate_browser.GATE_DOWNLOAD_COOKIE)
            self.handlers["download"](_download("gate.wav", b"RIFF-gate"))

    def _open(self, url):
        popup = _Popup(self, url)
        self.popups.append(popup)
        self.context.pages.append(popup)

    def wait_for_timeout(self, milliseconds):
        self.polls += 1
        self.context.clock["t"] += milliseconds / 1000
        if self.opening:
            self._open(self.opening)
            self.opening = None
        for popup in self.popups:
            if popup.closed or popup.url != _SPOTIFY_LOGIN and popup.url != _SPOTIFY_CALLBACK:
                continue
            popup.polls += 1
            if not (self.spotify_signed_in or self.context.attended):
                continue  # Spotify keeps asking for a login nobody types.
            if popup.polls == 2:
                # The callback loads, tells the gate to move on, and closes.
                popup.url = _SPOTIFY_CALLBACK
                self.index += 1
            elif popup.polls >= 3 and self.callback_closes:
                popup.closed = True
        if self.person_finishes and self.context.attended and self.polls >= 3:
            self.handlers["download"](_download("hand.wav", b"RIFF-hand"))


class _GateContext:
    def __init__(self, page_factory, clock, *, attended):
        self.factory = page_factory
        self.clock = clock
        self.attended = attended
        self.pages = [page_factory(self)]
        self.handlers = {}
        self.cookies = set()

    def on(self, event, callback):
        self.handlers[event] = callback

    def new_page(self):
        page = self.factory(self)
        self.pages.append(page)
        self.handlers["page"](page)
        return page

    def clear_cookies(self, *, name):
        self.cookies.discard(name)


def _gate_browser(monkeypatch, page_factory):
    """Every context the batch opens, as (headless, context), on a clock the fakes advance."""

    clock = {"t": 0.0}
    monkeypatch.setattr(gate_browser, "_now", lambda: clock["t"])
    launches = []

    @contextmanager
    def browser_context(_profile, *, accept_downloads=False, headless=False):
        context = _GateContext(page_factory, clock, attended=not headless)
        launches.append((headless, context))
        yield context

    monkeypatch.setattr("dj_digger.browser_session.sync_browser_context", browser_context)
    return launches


def _download(name, body):
    class Download:
        suggested_filename = name

        def save_as(self, destination):
            Path(destination).write_bytes(body)

    return Download()


class _Profile:
    def __init__(self, email, *, real=True, name="DJ Seven"):
        self.user_email = email
        self.user_name = name
        self.real = real

    def has_real_email(self):
        return self.real


_DJ = _Profile("dj@example.com")
_PLACEHOLDER = _Profile("digger@example.invalid", real=False)
_NAMELESS = _Profile("dj@example.com", name=DEFAULT_NAME)
_WALKED = [
    gate_browser.GATE_START_BUTTON,
    gate_browser.GATE_PENDING_ACTION,
    gate_browser.GATE_NEXT_BUTTON,
    gate_browser.GATE_CONNECT_BUTTON,
    gate_browser.GATE_PENDING_ACTION,
    gate_browser.GATE_NEXT_BUTTON,
    gate_browser.GATE_EMAIL_SUBMIT,
    gate_browser.HYPEDDIT_DOWNLOAD_BUTTON,
]


def _track():
    return Track(id=7, title="Seven", permalink_url="https://soundcloud.com/a/7")


def test_a_hidden_browser_walks_the_gate_and_downloads_without_a_window(tmp_path, monkeypatch):
    launches = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx))
    messages = []

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None,
        status=messages.append, config=_DJ,
    )

    assert [headless for headless, _context in launches] == [True]
    page = launches[0][1].pages[0]
    assert [selector for _kind, selector in page.clicked] == _WALKED
    assert page.email == "dj@example.com"
    assert [popup.url for popup in page.popups] == [_SOCIAL_PAGE, _SPOTIFY_CALLBACK, _SOCIAL_PAGE]
    assert all(popup.closed for popup in page.popups), "provider pages are closed unread"
    assert messages == []
    assert path.read_bytes() == b"RIFF-gate"


def test_a_gate_detoured_to_the_hot_or_not_poll_is_opened_again_hidden(tmp_path, monkeypatch):
    launches = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx, detours=1))

    path = gate_browser.download_hypeddit_in_browser(_track(), _GATE, tmp_path, None, config=_DJ)

    assert [headless for headless, _context in launches] == [True], "no window for a detour"
    page = launches[0][1].pages[0]
    assert page.visited == [_GATE, _GATE]
    assert [selector for _kind, selector in page.clicked] == _WALKED
    assert path.read_bytes() == b"RIFF-gate"


def test_a_second_gate_downloads_although_the_first_left_its_cookie(tmp_path, monkeypatch):
    launches = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx, steps=("email", "dw")))
    tracks = [Track(id=n, title=f"Track {n}", permalink_url=f"https://soundcloud.com/a/{n}") for n in (1, 2)]

    result = gate_browser.download_hypeddit_batch_in_browser(
        [(tracks[0], _GATE), (tracks[1], "https://hypeddit.com/track/eight")],
        tmp_path, None, config=_DJ,
    )

    assert [headless for headless, _context in launches] == [True]
    assert result.failures == ()
    assert [key for key, _path in result.completed] == ["1", "2"]


def test_a_provider_that_wants_a_login_moves_the_gate_to_a_window(tmp_path, monkeypatch):
    launches = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx, spotify_signed_in=False))
    messages = []

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None,
        status=messages.append, config=_DJ,
    )

    assert [headless for headless, _context in launches] == [True, False]
    hidden = launches[0][1].pages[0]
    assert hidden.kind == "sp" and hidden.clicked[-1][1] == gate_browser.GATE_CONNECT_BUTTON
    assert messages[0] == (
        "Opening the browser window for 1 gate: accounts.spotify.com wants you to sign in"
    )
    assert "Complete accounts.spotify.com in the browser window" in messages[1]
    window = launches[1][1].pages[0]
    assert [selector for _kind, selector in window.clicked] == _WALKED, (
        "the steps before and after the login are still walked for the person"
    )
    assert path.read_bytes() == b"RIFF-gate"


def test_a_login_popup_that_shows_up_a_moment_after_the_click_is_still_waited_for(
    tmp_path, monkeypatch
):
    launches = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx, late_popup=True))

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None, config=_DJ
    )

    assert [headless for headless, _context in launches] == [True]
    page = launches[0][1].pages[0]
    assert [selector for _kind, selector in page.clicked] == _WALKED
    assert path.read_bytes() == b"RIFF-gate"


def test_a_callback_popup_that_stays_open_is_closed_once_it_is_home(tmp_path, monkeypatch):
    launches = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx, callback_closes=False))

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None, config=_DJ
    )

    assert [headless for headless, _context in launches] == [True]
    callback = launches[0][1].pages[0].popups[1]
    assert callback.url == _SPOTIFY_CALLBACK and callback.closed
    assert path.read_bytes() == b"RIFF-gate"


def test_a_missing_email_is_left_to_the_person_at_the_window(tmp_path, monkeypatch):
    launches = _gate_browser(
        monkeypatch, lambda ctx: _GatePage(ctx, steps=("email", "dw"), person_finishes=True)
    )
    messages = []

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None,
        status=messages.append, config=_PLACEHOLDER,
    )

    assert [headless for headless, _context in launches] == [True, False]
    assert launches[0][1].pages[0].email is None
    assert messages == [
        "Opening the browser window for 1 gate: the gate wants an email address and the profile has none",
        "Seven: the gate wants an email address and the profile has none; finish it in the browser window",
    ]
    assert path.read_bytes() == b"RIFF-hand"


def test_a_gate_that_asks_for_a_name_gets_it_from_the_profile(tmp_path, monkeypatch):
    launches = _gate_browser(
        monkeypatch, lambda ctx: _GatePage(ctx, steps=("email", "dw"), asks_name=True)
    )
    messages = []

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None,
        status=messages.append, config=_DJ,
    )

    assert [headless for headless, _context in launches] == [True]
    page = launches[0][1].pages[0]
    assert (page.name, page.email) == ("DJ Seven", "dj@example.com")
    assert messages == []
    assert path.read_bytes() == b"RIFF-gate"


def test_a_missing_name_is_left_to_the_person_at_the_window(tmp_path, monkeypatch):
    launches = _gate_browser(
        monkeypatch,
        lambda ctx: _GatePage(ctx, steps=("email", "dw"), asks_name=True, person_finishes=True),
    )
    messages = []

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None,
        status=messages.append, config=_NAMELESS,
    )

    assert [headless for headless, _context in launches] == [True, False]
    assert launches[0][1].pages[0].email is None, "nothing is typed before the name stops it"
    assert messages == [
        "Opening the browser window for 1 gate: the gate wants a name and the profile has none",
        "Seven: the gate wants a name and the profile has none; finish it in the browser window",
    ]
    assert path.read_bytes() == b"RIFF-hand"


def test_a_captcha_on_the_email_step_needs_the_window(tmp_path, monkeypatch):
    launches = _gate_browser(
        monkeypatch,
        lambda ctx: _GatePage(ctx, steps=("email", "dw"), captcha=True, person_finishes=True),
    )
    messages = []

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/seven", tmp_path, None,
        status=messages.append, config=_DJ,
    )

    assert [headless for headless, _context in launches] == [True, False]
    assert messages[0] == "Opening the browser window for 1 gate: the gate wants a CAPTCHA solved"
    assert path.read_bytes() == b"RIFF-hand"


def test_social_actions_disabled_uses_passive_watcher_only(tmp_path, monkeypatch):
    def page_factory(ctx):
        page = _GatePage(ctx)
        original_goto = page.goto

        def goto(url, **kwargs):
            original_goto(url, **kwargs)
            page.handlers["download"](_download("hand.wav", b"RIFF-hand"))

        page.goto = goto
        return page

    launches = _gate_browser(monkeypatch, page_factory)

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/nine", tmp_path, None, social=False
    )

    assert [headless for headless, _context in launches] == [True]
    assert launches[0][1].pages[0].clicked == []
    assert path.read_bytes() == b"RIFF-hand"


def test_unknown_gate_dom_is_handed_to_the_window(tmp_path, monkeypatch):
    class BarePage:
        def __init__(self, context):
            self.context = context
            self.url = "about:blank"
            self.handlers = {}
            self.opener = None

        def on(self, event, callback):
            self.handlers[event] = callback

        def goto(self, url, **_kwargs):
            self.url = url
            if self.context.attended:
                self.handlers["download"](_download("bare.wav", b"RIFF-bare"))

        def wait_for_timeout(self, _timeout):
            pass

    launches = _gate_browser(monkeypatch, BarePage)
    messages = []

    path = gate_browser.download_hypeddit_in_browser(
        _track(), "https://hypeddit.com/track/ten", tmp_path, None, status=messages.append
    )

    assert [headless for headless, _context in launches] == [True, False]
    assert messages == [
        "Opening the browser window for 1 gate: the gate page has no step controls this program knows"
    ]
    assert path.read_bytes() == b"RIFF-bare"


def test_soundcloud_click_through_never_calls_soundcloud_or_mobile_step_endpoints():

    session = _stepping_gate_session()
    resolve_hypeddit_download_url(
        "https://hypeddit.com/track/abc1234",
        session,
        config=StubConfig("dj@example.com", gate_social_actions=True),
    )

    assert _posted_to(session, "/setSC") == []
    assert _posted_to(session, "/setYT") == []
    payloads = _posted_to(session, "/gate/download/ul")
    assert payloads[0]["skip_gate_steps[]"] == ["sc"]


def test_all_social_click_through_steps_are_skipped_without_external_requests():
    session = session_for_gate(gate_html(steps="sc,ig,yt"))
    gate_url = "https://hypeddit.com/track/click-through"

    resolve_hypeddit_download_url(
        gate_url,
        session,
        config=StubConfig("dj@example.com", gate_social_actions=True),
    )

    assert [call.args[0] for call in session.get.call_args_list] == [gate_url]
    assert _posted_to(session, "/gate/download/ul")[0]["skip_gate_steps[]"] == [
        "sc",
        "ig",
        "yt",
    ]
    assert len(session.post.call_args_list) == 1


def test_social_steps_stop_before_any_post_when_they_were_refused():
    session = _stepping_gate_session()
    with pytest.raises(gate_models.GateSocialActionsDisabled):
        resolve_hypeddit_download_url(
            "https://hypeddit.com/track/abc1234",
            session,
            config=StubConfig("dj@example.com", gate_social_actions=False),
        )

    session.post.assert_not_called()


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


def _soup(page):
    return BeautifulSoup(page, "html.parser")


def test_a_shop_page_that_merely_mentions_downloads_is_still_a_shop():
    """The word used to be matched anywhere on the page, footers included."""

    page = """
    <html><body>
      <a class="retailer-link" href="https://label.bandcamp.com/album/x">Bandcamp</a>
      <footer>Instant download with every vinyl order. Digital downloads FAQ.</footer>
      <script>var downloadTracker = init("download");</script>
    </body></html>
    """
    assert gate_hubs._offers_a_download(_soup(page)) is False


def test_a_gate_in_another_language_is_recognised_as_a_gate():
    """A German or Spanish gate used to be rewritten into a list of shops."""

    for button in ("Herunterladen", "Descargar", "Télécharger", "Pobierz"):
        page = f'<html><body><button class="gate-cta">{button}</button></body></html>'
        assert gate_hubs._offers_a_download(_soup(page)) is True, button


def test_a_download_button_is_still_found_when_it_is_a_submit_input():
    page = '<html><body><input type="submit" value="Free Download"></body></html>'
    assert gate_hubs._offers_a_download(_soup(page)) is True


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

    inspection = inspect_link_page(
        "https://label.ampsuite.com/releases/links?id=1", session
    )

    assert inspection.keep_original is False
    assert [url for url, _text in inspection.shops] == [
        "https://www.beatport.com/release/know-your-place/7057750",
        "https://label.bandcamp.com/album/know-your-place",
    ]
    assert [text for _url, text in inspection.shops] == ["Beatport", "Bandcamp"]


def test_a_hub_page_does_not_offer_the_streaming_services_it_lists():
    """Spotify is on the page, but you cannot buy the record there."""

    session = HubSession(HUB_PAGE)
    inspection = inspect_link_page(
        "https://label.ampsuite.com/releases/links?id=1", session
    )
    assert not any("spotify" in url for url, _text in inspection.shops)


def test_hypeddit_smart_link_keeps_only_its_beatport_destination():
    page = """
    <html><head><title>Whiplash EP by Sota</title></head><body>
      <a class="hype-btn" href="https://www.beatport.com/release/whiplash/3629013">Buy</a>
      <a href="https://open.spotify.com/album/stream-only">Listen</a>
    </body></html>
    """

    inspection = inspect_link_page(
        "https://hypeddit.com/l87679",
        HubSession(page, landed="https://hypeddit.com/l87679"),
    )

    assert inspection.keep_original is False
    assert inspection.shops == (
        ("https://www.beatport.com/release/whiplash/3629013", "Buy"),
    )


def test_a_page_that_hands_over_a_file_is_left_as_a_gate():
    """A real follow-to-download gate keeps its badge, shop link or not."""

    page = (
        '<html><body><a href="https://label.bandcamp.com/track/a">Buy</a>'
        "<button>Free Download</button></body></html>"
    )
    assert inspect_link_page(
        "https://hypeddit.com/track/abc", HubSession(page)
    ).keep_original is True

    inspection = inspect_link_page(
        "https://hypeddit.com/track/abc",
        HubSession(page, landed="https://hypeddit.com/track/abc"),
    )
    assert inspection.keep_original is True
    assert inspection.shops == (("https://label.bandcamp.com/track/a", "Buy"),)


def test_hypeddit_hybrid_keeps_shops_and_replaces_wrapper_with_nested_gate():
    page = """
    <html><body>
      <a href="https://www.beatport.com/release/ghetto-bass/2470877">Beatport</a>
      <a href="https://terrenceandphillip.bandcamp.com/">Bandcamp</a>
      <a href="https://hypeddit.com/track/nmqt0z">Free Download</a>
    </body></html>
    """
    inspection = inspect_link_page(
        "https://hypeddit.com/link/ky9i8z",
        HubSession(page, landed="https://hypeddit.com/link/ky9i8z"),
    )

    assert inspection.keep_original is False
    assert inspection.gate_urls == ("https://hypeddit.com/track/nmqt0z",)
    assert {url for url, _text in inspection.shops} == {
        "https://www.beatport.com/release/ghetto-bass/2470877",
        "https://terrenceandphillip.bandcamp.com/",
    }


def test_a_shop_linked_twice_is_returned_once():
    page = (
        '<html><body><a href="https://label.bandcamp.com/album/a">Bandcamp</a>'
        '<a href="/go?store=1">Bandcamp</a></body></html>'
    )
    session = HubSession(
        page,
        redirects={"https://label.ampsuite.com/go?store=1": "https://label.bandcamp.com/album/a"},
    )
    inspection = inspect_link_page(
        "https://label.ampsuite.com/releases/links?id=1", session
    )
    assert len(inspection.shops) == 1


def test_an_unreachable_hub_changes_nothing():
    """None, not []: the caller writes the host off after two of these."""

    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.RequestException("nope")
    assert inspect_link_page("https://label.ampsuite.com/x", session) is None


def test_a_host_that_answered_404_is_not_reported_as_unreachable():
    """Something replied. Only silence counts against a host."""

    session = MagicMock(spec=requests.Session)
    session.get.return_value = MagicMock(status_code=404, text="", url="https://label.ampsuite.com/x")
    inspection = inspect_link_page("https://label.ampsuite.com/x", session)
    assert inspection is not None
    assert inspection.shops == () and inspection.keep_original is False


def test_an_address_on_our_own_network_is_never_fetched():
    session = MagicMock(spec=requests.Session)
    inspection = inspect_link_page("http://169.254.169.254/latest/meta-data/", session)
    assert inspection.shops == () and inspection.keep_original is False
    session.get.assert_not_called()


def test_revoked_social_consent_stops_the_next_browser_click(tmp_path, monkeypatch):
    profile = _Profile("dj@example.com")
    profile.gate_social_actions = True

    class RevokingPage(_GatePage):
        def click(self, selector, slide):
            super().click(selector, slide)
            if selector == gate_browser.GATE_PENDING_ACTION:
                profile.gate_social_actions = False

    launches = _gate_browser(monkeypatch, RevokingPage)
    with pytest.raises(gate_models.GateSocialActionsDisabled):
        gate_browser.download_hypeddit_in_browser(_track(), _GATE, tmp_path, None, config=profile)
    assert len(launches) == 1
    clicks = [selector for _, selector in launches[0][1].pages[0].clicked]
    assert clicks == [gate_browser.GATE_START_BUTTON, gate_browser.GATE_PENDING_ACTION]


def test_profile_changed_during_fill_is_not_submitted(tmp_path, monkeypatch):
    profile = _Profile("dj@example.com")

    class RevokingPage(_GatePage):
        def fill(self, selector, value):
            super().fill(selector, value)
            if selector == gate_browser.GATE_EMAIL_INPUT:
                profile.real = False

    launches = _gate_browser(monkeypatch, RevokingPage)
    with pytest.raises(gate_models.GateProfileRequired):
        gate_browser.download_hypeddit_in_browser(_track(), _GATE, tmp_path, None, config=profile)
    clicks = [selector for _, selector in launches[0][1].pages[0].clicked]
    assert gate_browser.GATE_EMAIL_SUBMIT not in clicks


def test_cancellation_after_telemetry_prevents_unlock(monkeypatch):
    from threading import Event

    from dj_digger.models import Cancelled

    cancel = Event()
    session = session_for_gate(fixture_html("hypeddit_gate_hot_or_not.html"))
    monkeypatch.setattr(gates, "_ping_telemetry", lambda *args: cancel.set())
    with pytest.raises(Cancelled):
        resolve_hypeddit_download_url(_GATE, session, config=StubConfig("dj@example.com"), cancel=cancel)
    assert not _posted_to(session, "/gate/download/ul")


def test_browser_cancellation_keeps_completed_files_and_real_errors_only(tmp_path, monkeypatch):
    from threading import Event
    from types import SimpleNamespace

    from dj_digger.gates import browser as adapter
    from dj_digger.models import Cancelled, Track

    items = [(Track(id=i, title=str(i), permalink_url=f'https://soundcloud.com/a/{i}'),
              'https://hypeddit.com/a/b') for i in (1, 2, 3)]
    cancel = Event()
    cancel.set()
    result = adapter.download_hypeddit_batch_in_browser(items, tmp_path, cancel)
    assert result.cancelled and not result.failures and not result.completed
    with pytest.raises(Cancelled):
        adapter.download_hypeddit_in_browser(*items[0], tmp_path, cancel)

    watch = adapter._TabWatch({track.key: (track, url) for track, url in items}, tmp_path, cancel, {})
    finished = tmp_path / 'complete.wav'
    finished.write_bytes(b'audio')
    watch.completed['1'] = finished
    watch.failures['2'] = adapter.GateDownloadError('real failure')
    stopped = adapter._await_downloads(SimpleNamespace(pages=[]), [], watch, None,
                                      social=False, email=None, name=None,
                                      attended=False, time_limit=1)
    result = watch.result(stopped)
    assert result.cancelled
    assert result.completed == (('1', finished),)
    assert [key for key, _error in result.failures] == ['2']

    # The workflow sees the actual adapter result, including mixed outcomes.
    from dj_digger.config import AppConfig
    from dj_digger.services.downloads import DownloadRequest, DownloadService, DownloadWorkflow
    from dj_digger.services.operations import OperationCoordinator

    monkeypatch.setattr(adapter, 'download_hypeddit_batch_in_browser', lambda *a, **k: result)
    class State:
        def set_local_file(self, key, path):
            pass
    operations = OperationCoordinator()
    handle = operations.start('Downloading')
    events = []
    workflow = DownloadWorkflow(DownloadService(State()), DownloadRequest('', 'initial', tmp_path, 20),
                                handle, client=lambda: None, config=AppConfig(),
                                emit=events.append, prerequisites=lambda *args: [])
    from dj_digger.services.downloads import _BatchProgress
    progress = _BatchProgress(total=3, browser_items=items)
    workflow.browser_pass(progress)
    assert progress.completed == progress.failed == progress.cancelled == 1
    assert [event.key for event in events if event.kind == 'cancelled'] == ['3']
    operations.finish(handle)

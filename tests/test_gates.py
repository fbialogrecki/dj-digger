import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from dj_digger import gates
from dj_digger.gates import (
    inspect_link_page,
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
    assert inspection.direct_url is None


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

    with pytest.raises(gates.GateAuthenticationRequired):
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

    with pytest.raises(gates.GateCaptchaRequired, match="CAPTCHA"):
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

    with pytest.raises(gates.GateProtocolChanged, match="no supported download manifest"):
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

    with pytest.raises(gates.GateProtocolChanged, match="unsafe address"):
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
        with pytest.raises(gates.GateUnavailable, match="profile is already in use"):
            gates.download_hypeddit_in_browser(
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

    result = gates.download_hypeddit_batch_in_browser(
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


class _GatePage:
    """A Hypeddit tab with step buttons, a download button, and optional popups."""

    def __init__(self, context, *, steps=2, popup_on=(), download_on_click=True):
        self.context = context
        self.url = "about:blank"
        self.handlers = {}
        self.opener = None
        self.clicked = []
        self.popup_on = set(popup_on)
        self.download_on_click = download_on_click
        self.polls = 0
        self.steps = steps

    def on(self, event, callback):
        self.handlers[event] = callback

    def goto(self, url, **_kwargs):
        self.url = url

    def is_closed(self):
        return False

    def wait_for_timeout(self, _timeout):
        self.polls += 1
        # A popup the person has "completed" after a couple of polls.
        for popup in self.context.pages:
            if popup.opener is self and self.polls % 3 == 0:
                popup.closed = True

    def locator(self, selector):
        page = self

        class Element:
            def __init__(self, kind, index=0):
                self.kind, self.index = kind, index

            def is_visible(self):
                return True

            @property
            def first(self):
                return self

            def click(self, timeout=None):
                page.clicked.append((self.kind, self.index))
                if self.kind == "step" and self.index in page.popup_on:
                    popup = _Popup(page)
                    page.context.pages.append(popup)
                if self.kind == "download" and page.download_on_click:
                    page.handlers["download"](_download("gate.wav", b"RIFF-gate"))

        class Locator:
            def __init__(self, kind):
                self.kind = kind

            def count(self):
                return page.steps if self.kind == "step" else 1

            def nth(self, index):
                return Element(self.kind, index)

            @property
            def first(self):
                return Element(self.kind, 0)

        return Locator("step" if selector == gates.HYPEDDIT_STEP_BUTTON else "download")


class _Popup:
    def __init__(self, opener):
        self.opener = opener
        self.url = "https://accounts.spotify.com/authorize"
        self.closed = False

    def is_closed(self):
        return self.closed


def _download(name, body):
    class Download:
        suggested_filename = name

        def save_as(self, destination):
            Path(destination).write_bytes(body)

    return Download()


class _GateContext:
    def __init__(self, page_factory):
        self.pages = [page_factory(self)]
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback

    def new_page(self):
        page = self.pages[0].__class__(self)
        self.pages.append(page)
        return page


def _gate_browser(monkeypatch, page_factory):
    contexts = []

    @contextmanager
    def browser_context(*_args, **_kwargs):
        context = _GateContext(page_factory)
        contexts.append(context)
        yield context

    monkeypatch.setattr("dj_digger.browser_session.sync_browser_context", browser_context)
    return contexts


def test_semi_automated_gate_clicks_declared_steps_then_download(tmp_path, monkeypatch):
    contexts = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx, steps=2))
    track = Track(id=7, title="Seven", permalink_url="https://soundcloud.com/a/7")

    path = gates.download_hypeddit_in_browser(
        track, "https://hypeddit.com/track/seven", tmp_path, None, social=True
    )

    page = contexts[0].pages[0]
    assert page.clicked == [("step", 0), ("step", 1), ("download", 0)]
    assert path.read_bytes() == b"RIFF-gate"


def test_provider_popup_pauses_until_it_closes(tmp_path, monkeypatch):
    contexts = _gate_browser(monkeypatch, lambda ctx: _GatePage(ctx, steps=2, popup_on={0}))
    messages = []
    track = Track(id=8, title="Eight", permalink_url="https://soundcloud.com/a/8")

    gates.download_hypeddit_in_browser(
        track, "https://hypeddit.com/track/eight", tmp_path, None, social=True, status=messages.append
    )

    page = contexts[0].pages[0]
    assert page.clicked[0] == ("step", 0)
    assert page.polls >= 3, "the second step waited for the popup to be dealt with"
    assert page.clicked[1:] == [("step", 1), ("download", 0)]
    assert messages and "accounts.spotify.com" in messages[0]


def test_social_actions_disabled_uses_passive_watcher_only(tmp_path, monkeypatch):
    def page_factory(ctx):
        page = _GatePage(ctx, steps=2)
        original_goto = page.goto

        def goto(url, **kwargs):
            original_goto(url, **kwargs)
            page.handlers["download"](_download("hand.wav", b"RIFF-hand"))

        page.goto = goto
        return page

    contexts = _gate_browser(monkeypatch, page_factory)
    track = Track(id=9, title="Nine", permalink_url="https://soundcloud.com/a/9")

    path = gates.download_hypeddit_in_browser(
        track, "https://hypeddit.com/track/nine", tmp_path, None, social=False
    )

    assert contexts[0].pages[0].clicked == []
    assert path.read_bytes() == b"RIFF-hand"


def test_unknown_gate_dom_degrades_to_passive_watcher(tmp_path, monkeypatch):
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
            self.handlers["download"](_download("bare.wav", b"RIFF-bare"))

        def wait_for_timeout(self, _timeout):
            pass

    _gate_browser(monkeypatch, BarePage)
    track = Track(id=10, title="Ten", permalink_url="https://soundcloud.com/a/10")

    path = gates.download_hypeddit_in_browser(
        track, "https://hypeddit.com/track/ten", tmp_path, None, social=True
    )

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
    with pytest.raises(gates.GateSocialActionsDisabled):
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

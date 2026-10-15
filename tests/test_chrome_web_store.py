import io
import json
import subprocess
import urllib.error

import pytest

from scripts import chrome_web_store as cws

KEY = {"client_email": "ci@example.iam.gserviceaccount.com", "private_key": "-----BEGIN PRIVATE KEY-----\nSECRETKEY\n"}
ENV = {"CWS_SERVICE_ACCOUNT_JSON": json.dumps(KEY), "CWS_PUBLISHER_ID": "pub-1"}
TOKEN = "ya29.SECRETTOKEN"


class FakeStore:
    """Routes urlopen by URL suffix; records (method, url, headers, body)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout=None):
        self.calls.append((req.get_method(), req.full_url, dict(req.header_items()), req.data))
        for suffix, (code, body) in self.routes.items():
            if req.full_url.endswith(suffix):
                payload = json.dumps(body).encode()
                if code >= 400:
                    raise urllib.error.HTTPError(req.full_url, code, "error", {}, io.BytesIO(payload))
                return io.BytesIO(payload)
        raise AssertionError(f"unexpected request {req.full_url}")


@pytest.fixture
def store(monkeypatch, tmp_path):
    def install(**routes):
        fake = FakeStore({"/token": (200, {"access_token": TOKEN}), **routes})
        monkeypatch.setattr(cws.urllib.request, "urlopen", fake)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)  # the ::add-mask:: line names the token
        monkeypatch.setattr(cws.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"sig", b""))
        return fake
    return install


def status(version, state="PUBLISHED"):
    return (200, {"publishedItemRevisionStatus": {"state": state, "distributionChannels": [{"crxVersion": version}]}})


def run(tmp_path, env=ENV, draft=False):
    path = tmp_path / "dj-digger-chrome-1.4.3.zip"
    path.write_bytes(b"PK zip")
    return cws.run(path, "1.4.3", draft, env)


def test_missing_secrets_skip_without_a_request(store, tmp_path, capsys):
    fake = store()
    assert run(tmp_path, env={}) == 0
    assert fake.calls == [] and "skipped" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["1.4.3", "1.10.0"])
def test_same_or_newer_version_in_store_skips(store, tmp_path, capsys, version):
    fake = store(**{":fetchStatus": status(version)})
    assert run(tmp_path) == 0
    assert [c[1].rsplit(":", 1)[-1] for c in fake.calls[1:]] == ["fetchStatus"]
    assert f"already has {version}" in capsys.readouterr().out


def test_pending_review_skips(store, tmp_path):
    fake = store(**{":fetchStatus": (200, {"submittedItemRevisionStatus": {"state": "PENDING_REVIEW"}})})
    assert run(tmp_path) == 0 and not any(":upload" in c[1] for c in fake.calls)


def test_upload_then_publish(store, tmp_path, capsys):
    fake = store(**{
        ":fetchStatus": status("1.4.2"),
        ":upload": (200, {"uploadState": "SUCCEEDED", "crxVersion": "1.4.3"}),
        ":publish": (200, {"state": "PENDING_REVIEW"}),
    })
    assert run(tmp_path) == 0
    token, fetch, upload, publish = fake.calls
    assert token[1] == cws.TOKEN_URL and b"jwt-bearer" in token[3]
    base = f"{cws.API}/v2/publishers/pub-1/items/{cws.CHROME_STORE_ID}"
    assert fetch[:2] == ("GET", f"{base}:fetchStatus")
    assert upload[:2] == ("POST", f"{cws.API}/upload/v2/publishers/pub-1/items/{cws.CHROME_STORE_ID}:upload")
    assert upload[3] == b"PK zip" and upload[2]["Authorization"] == f"Bearer {TOKEN}"
    assert publish[:2] == ("POST", f"{base}:publish")
    out = capsys.readouterr().out
    assert "submitted: PENDING_REVIEW" in out and TOKEN not in out


def test_draft_uploads_without_publishing(store, tmp_path):
    fake = store(**{":fetchStatus": status("1.4.2"), ":upload": (200, {"uploadState": "SUCCEEDED"})})
    assert run(tmp_path, draft=True) == 0
    assert not any(":publish" in c[1] for c in fake.calls)


def test_upload_failure_reports_the_api_message_without_secrets(store, tmp_path, capsys):
    store(**{
        ":fetchStatus": status("1.4.2"),
        ":upload": (400, {"error": {"code": 400, "message": "Manifest version must be greater"}}),
    })
    assert run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "Manifest version must be greater" in out
    assert TOKEN not in out and "SECRETKEY" not in out

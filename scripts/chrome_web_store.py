"""Upload the Chrome zip to the Chrome Web Store and submit it for review (API v2).

Run by .github/workflows/chrome-web-store.yml after scripts/package_extension.py.
Credentials come from the environment, never from arguments:

    CWS_SERVICE_ACCOUNT_JSON  service account key (JSON) linked in the developer dashboard
    CWS_PUBLISHER_ID          the dashboard's publisher ID

Missing credentials, a store that already has this version (or newer) and an item
still in review end with exit 0 and a notice: re-uploading a version is rejected.

    python3 scripts/chrome_web_store.py ZIP [--draft]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dj_digger.webext.register import CHROME_STORE_ID  # noqa: E402

API = "https://chromewebstore.googleapis.com"
SCOPE = "https://www.googleapis.com/auth/chromewebstore"
TOKEN_URL = "https://oauth2.googleapis.com/token"
IN_PROGRESS = {"IN_PROGRESS", "UPLOAD_IN_PROGRESS"}  # the v2 reference uses both spellings


class StoreError(RuntimeError):
    pass


def notice(message: str) -> None:
    print(f"::notice title=Chrome Web Store::{message}")


def request(url: str, *, data: bytes | None = None, headers: dict | None = None, method: str | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            error = json.loads(detail)
            detail = error.get("error_description") or error["error"].get("message") or detail
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        raise StoreError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).path}: {detail}") from None
    return json.loads(body or b"{}")


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def access_token(key: dict) -> str:
    """OAuth 2.0 JWT bearer grant for a service account; RS256 signed by openssl (no crypto in stdlib)."""
    now = int(time.time())
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(json.dumps({
        "iss": key["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 600,
    }).encode())
    unsigned = f"{header}.{claims}"
    with tempfile.TemporaryDirectory() as tmp:  # mkdtemp is 0700, the key never leaves it
        pem = Path(tmp) / "key.pem"
        pem.write_text(key["private_key"])
        signed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(pem)],
            input=unsigned.encode(), capture_output=True, check=False,
        )
    if signed.returncode:
        raise StoreError("openssl could not sign with CWS_SERVICE_ACCOUNT_JSON's private_key")
    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": f"{unsigned}.{b64(signed.stdout)}",
    }).encode()
    token = request(TOKEN_URL, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})["access_token"]
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{token}")
    return token


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def store_versions(status: dict) -> list[str]:
    return [
        channel["crxVersion"]
        for revision in ("publishedItemRevisionStatus", "submittedItemRevisionStatus")
        for channel in (status.get(revision) or {}).get("distributionChannels", [])
        if channel.get("crxVersion")
    ]


def run(zip_path: Path, version: str, draft: bool, env: dict) -> int:
    raw_key, publisher = env.get("CWS_SERVICE_ACCOUNT_JSON"), env.get("CWS_PUBLISHER_ID")
    if not raw_key or not publisher:
        notice("CWS_SERVICE_ACCOUNT_JSON / CWS_PUBLISHER_ID are not set; skipped (see docs/store/PUBLISHING-pl.md).")
        return 0
    try:
        key = json.loads(raw_key)
    except ValueError:
        print("::error::CWS_SERVICE_ACCOUNT_JSON is not valid JSON")
        return 1
    item = f"{API}/v2/publishers/{publisher}/items/{CHROME_STORE_ID}"
    upload = f"{API}/upload/v2/publishers/{publisher}/items/{CHROME_STORE_ID}:upload"
    try:
        auth = {"Authorization": f"Bearer {access_token(key)}"}
        status = request(f"{item}:fetchStatus", headers=auth)
        if (status.get("submittedItemRevisionStatus") or {}).get("state") == "PENDING_REVIEW":
            notice("An item is pending review; skipped. Cancel it in the dashboard to submit a new one.")
            return 0
        newer = [v for v in store_versions(status) if version_key(v) >= version_key(version)]
        if newer:
            notice(f"The store already has {max(newer, key=version_key)} (this build is {version}); skipped. Bump manifest.json's version.")
            return 0
        result = request(upload, data=zip_path.read_bytes(), method="POST",
                         headers={**auth, "Content-Type": "application/zip"})
        for _ in range(30):  # large packages are processed asynchronously
            if result.get("uploadState") not in IN_PROGRESS:
                break
            time.sleep(10)
            result = {"uploadState": request(f"{item}:fetchStatus", headers=auth).get("lastAsyncUploadState")}
        if result.get("uploadState") != "SUCCEEDED":
            raise StoreError(f"upload state {result.get('uploadState')}: {json.dumps(result)}")
        print(f"Uploaded {zip_path.name} ({version}).")
        if draft:
            notice(f"{version} uploaded as a draft; submit it in the dashboard or rerun with publish.")
            return 0
        published = request(f"{item}:publish", data=b"{}", method="POST",
                            headers={**auth, "Content-Type": "application/json"})
        notice(f"{version} submitted: {published.get('state', 'unknown state')}.")
        return 0
    except StoreError as exc:
        print(f"::error title=Chrome Web Store::{exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("zip", type=Path)
    parser.add_argument("--draft", action="store_true", help="upload only, do not submit for review")
    args = parser.parse_args()
    with zipfile.ZipFile(args.zip) as zf:
        version = json.loads(zf.read("manifest.json"))["version"]
    return run(args.zip, version, args.draft, os.environ)


if __name__ == "__main__":
    sys.exit(main())

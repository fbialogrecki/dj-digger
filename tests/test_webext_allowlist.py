"""Offline checks of the browser extension's static security surface (plan section 4)."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from dj_digger import links, store_urls

EXTENSION = Path(__file__).parents[1] / "dj_digger" / "webext" / "extension"
ALLOWLIST = json.loads((EXTENSION / "allowlist.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
JS_FILES = sorted(EXTENSION.glob("*.js"))
GROUPS = ALLOWLIST["groups"]


def _host_patterns() -> set[str]:
    patterns = set()
    for group in GROUPS.values():
        for host in group["hosts"]:
            patterns.add(f"https://{host}/*")
            if group["www"]:
                patterns.add(f"https://www.{host}/*")
            if group["subdomains"]:
                patterns.add(f"https://*.{host}/*")
    return patterns


def _allowed(url: str) -> bool:
    """The allowlist.json rule as background.js applies it (host part only)."""

    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        return False
    for group in GROUPS.values():
        for base in group["hosts"]:
            if (
                host == base
                or (group["www"] and host == "www." + base)
                or (group["subdomains"] and host.endswith("." + base))
            ):
                return True
    return False


def test_hypeddit_hosts_mirror_links_py():
    group = GROUPS["hypeddit"]
    assert set(group["hosts"]) == links.HYPEDDIT_HOSTS
    assert group["www"] is True  # host_of strips one www.
    assert group["subdomains"] is False


def test_store_hosts_mirror_store_urls_py():
    for store, domain in store_urls.STORE_HOSTS.items():
        assert GROUPS[store]["hosts"] == [domain]
        assert GROUPS[store]["subdomains"] is True
    assert set(GROUPS) == {"hypeddit", "soundcloud", *store_urls.STORE_HOSTS}


def test_soundcloud_is_signin_only_and_never_scripted():
    group = GROUPS["soundcloud"]
    assert group["hosts"] == ["soundcloud.com"]
    assert group["allowedPaths"] == ["/signin"]
    assert group["dom"] is False
    assert not group["www"] and not group["subdomains"]


@pytest.mark.parametrize(
    "url",
    [
        "https://hypeddit.com/abc",
        "https://www.hypeddit.com/abc",
        "https://hypd.it/x",
        "https://sub.hypeddit.com/",
        "https://www.www.hypeddit.com/",
        "https://bandcamp.com/",
        "https://artist.bandcamp.com/track/x",
        "https://evil-bandcamp.com/",
        "https://bandcamp.com.evil.net/",
        "https://www.beatport.com/track/x/1",
        "https://beatport.com/",
        "https://notbeatport.com/",
        "https://soundcloud.com/signin",
        "https://example.com/",
        "https://user:pw@bandcamp.com/",
        "https://bandcamp.com:8443/",
        "http://bandcamp.com/",
    ],
)
def test_allowlist_agrees_with_the_python_url_checks(url):
    parsed = urlparse(url)
    plain = parsed.scheme == "https" and not (parsed.username or parsed.password or parsed.port)
    python_says = plain and (
        links.is_hypeddit_url(url)
        or store_urls.is_store_url(url, "bandcamp")
        or store_urls.is_store_url(url, "beatport")
        or links.host_of(url) == "soundcloud.com"
    )
    assert _allowed(url) == python_says


def test_manifest_host_permissions_match_allowlist():
    assert set(MANIFEST["host_permissions"]) == _host_patterns()
    assert len(MANIFEST["host_permissions"]) == len(_host_patterns())


def test_manifest_permissions_and_entry_points():
    assert MANIFEST["manifest_version"] == 3
    assert set(MANIFEST["permissions"]) == {
        "nativeMessaging", "tabs", "scripting", "downloads", "cookies",
        "webRequest", "webNavigation", "storage", "alarms", "offscreen",
    }  # fmt: skip
    assert "externally_connectable" not in MANIFEST
    assert "content_scripts" not in MANIFEST
    assert "optional_permissions" not in MANIFEST
    assert MANIFEST["version"] == "1.4.0"
    assert MANIFEST["name"] == "dj-digger"
    assert MANIFEST["minimum_chrome_version"] == "121"
    assert MANIFEST["browser_specific_settings"]["gecko"] == {
        "id": "dj-digger@fbialogrecki.github.io",
        "strict_min_version": "128.0",
    }
    background = MANIFEST["background"]
    assert background["service_worker"] == "background.js"
    assert background["scripts"] == ["dom.js", "background.js"]
    for name in (
        background["service_worker"],
        *background["scripts"],
        MANIFEST["options_ui"]["page"],
    ):
        assert (EXTENSION / name).is_file()


def test_manifest_key_is_a_public_rsa_2048_key():
    der = base64.b64decode(MANIFEST["key"], validate=True)
    assert der.startswith(bytes.fromhex("30820122300d06092a864886f70d01010105000382010f00"))
    assert len(der) == 294


def test_native_host_name_is_pinned():
    assert (
        'const HOST = "com.github.fbialogrecki.djdigger"'
        in (EXTENSION / "background.js").read_text()
    )


def test_forbidden_paths_cover_checkout_for_both_stores():
    for store in store_urls.STORE_HOSTS:
        assert {"/checkout", "/cart/checkout", "/payment"} <= set(GROUPS[store]["forbiddenPaths"])


def test_click_denylist():
    deny = ALLOWLIST["clickDenylist"]
    pattern = re.compile(deny["pattern"], re.IGNORECASE if "i" in deny["flags"] else 0)
    for name in (
        "Checkout",
        "check out",
        "Pay now",
        "Payment",
        "Purchase",
        "Place order",
        "Buy now",
        "Complete order",
    ):
        assert pattern.search(name), name
    for name in ("Add to cart", "Download", "Next", "Buy Digital Track", "Playlist"):
        assert not pattern.search(name), name
    assert [e["host"] for e in deny["exceptions"]] == ["bandcamp.com"]
    assert re.fullmatch(deny["exceptions"][0]["pattern"], "buy digital track", re.IGNORECASE)


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_no_remote_code_and_one_input_channel(path):
    source = path.read_text(encoding="utf-8")
    assert "eval(" not in source
    assert "new Function" not in source
    assert "importScripts(" not in source or path.name == "background.js"
    for forbidden in (
        "onMessageExternal", "onConnectExternal", "externally_connectable",
        "runtime.onMessage", 'addEventListener("message"',
        "executeScript({ target, code", "sendMessage(",
    ):  # fmt: skip
        assert forbidden not in source, forbidden
    # plan F4b: the offscreen document and the frame content script each open one port to
    # the background, which has the one runtime.onConnect (it admits only those two, see
    # the harness); nothing else listens for or opens a runtime channel.
    assert ("runtime.onConnect" in source) == (path.name == "background.js")
    assert ("runtime.connect(" in source) == (path.name in {"offscreen.js", "frame.js"})
    assert source.count("runtime.onConnect") <= 1


def test_the_frame_script_is_inert_outside_the_offscreen_document():
    source = (EXTENSION / "frame.js").read_text(encoding="utf-8")
    assert "window.top === window" in source
    assert "location.ancestorOrigins?.[0] !== new URL(api.runtime.getURL(\"\")).origin" in source
    assert source.index("ancestorOrigins") < source.index("runtime.connect(")


def test_the_frame_script_is_registered_dynamically_for_hypeddit_only():
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")
    assert "content_scripts" not in MANIFEST
    assert background.count("scripting.registerContentScripts(") == 1
    start = background.index("async function startOffscreen")
    assert "allow.groups.hypeddit" in background[start : background.index("scripting.registerContentScripts(")]
    assert 'js: ["dom.js", "frame.js"]' in background
    assert (EXTENSION / "offscreen.html").is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_javascript_parses(path):
    subprocess.run(["node", "--check", str(path)], check=True, capture_output=True, timeout=30)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_background_keeps_one_work_window_per_session():
    """The real background.js on a fake browser: sessions never touch each other's window."""

    harness = Path(__file__).parent / "js" / "background_harness.js"
    result = subprocess.run(
        ["node", str(harness), str(EXTENSION)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr

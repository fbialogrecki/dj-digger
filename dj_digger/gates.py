"""Bypass and resolver module for download gates (Hypeddit, ToneDen, etc.).

Extracts direct file download URLs from gate pages without requiring manual
social media login steps.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests

LOGGER = logging.getLogger(__name__)

HYPEDDIT_RE = re.compile(r'https?://(?:www\.)?hypeddit\.com/(?:track/)?([a-zA-Z0-9_-]+)')
TONEDEN_RE = re.compile(r'https?://(?:www\.)?toneden\.io/([^/]+)/post/([a-zA-Z0-9_-]+)')
URL_MATCH_RE = re.compile(r'https?://[^\s\'"<>]+')


def resolve_hypeddit_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> Optional[str]:
    """Resolve direct audio download URL from Hypeddit gate link."""
    match = HYPEDDIT_RE.search(url)
    if not match:
        return None

    try:
        # Request gate page HTML
        resp = session.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None

        text = resp.text

        # 1. Search JS variables in page HTML
        for pattern in (
            r'var\s+download_url\s*=\s*["\']([^"\']+)["\']',
            r'var\s+s3_url\s*=\s*["\']([^"\']+)["\']',
            r'data-download-url=["\']([^"\']+)["\']',
        ):
            found = re.search(pattern, text)
            if found and found.group(1).startswith("http"):
                return found.group(1)

        # 2. Try Hypeddit gate API download endpoint
        gate_id = match.group(1)
        api_url = "https://hypeddit.com/index.php?rm=gate/get_download_link"
        post_data = {"id": gate_id}
        api_resp = session.post(api_url, data=post_data, timeout=timeout)
        if api_resp.status_code == 200:
            try:
                data = api_resp.json()
                if isinstance(data, dict):
                    dl = data.get("download_url") or data.get("url") or data.get("s3_url")
                    if dl and isinstance(dl, str) and dl.startswith("http"):
                        return dl
            except ValueError:
                pass
    except requests.RequestException as exc:
        LOGGER.debug("Hypeddit gate resolution failed for %s: %s", url, exc)

    return None


def resolve_toneden_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> Optional[str]:
    """Resolve direct audio download URL from ToneDen fan gate link."""
    match = TONEDEN_RE.search(url)
    if not match:
        return None

    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None

        text = resp.text

        # 1. Extract JSON state from page HTML
        json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});</script>', text, re.DOTALL)
        if json_match:
            try:
                import json
                state = json.loads(json_match.group(1))
                # Deep search for download_url
                state_str = json.dumps(state)
                dl_match = re.search(r'"download_url"\s*:\s*"([^"]+)"', state_str)
                if dl_match:
                    dl = dl_match.group(1).replace("\\/", "/")
                    if dl.startswith("http"):
                        return dl
            except Exception:
                pass

        # 2. Try ToneDen fan gate API directly
        gate_slug = match.group(2)
        api_url = f"https://www.toneden.io/api/v1/fan_gates/slug/{gate_slug}"
        api_resp = session.get(api_url, timeout=timeout)
        if api_resp.status_code == 200:
            try:
                data = api_resp.json()
                if isinstance(data, dict):
                    dl = data.get("download_url") or data.get("free_download_location")
                    if dl and isinstance(dl, str) and dl.startswith("http"):
                        return dl
            except ValueError:
                pass
    except requests.RequestException as exc:
        LOGGER.debug("ToneDen gate resolution failed for %s: %s", url, exc)

    return None


def resolve_gate_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> Optional[str]:
    """Inspect and resolve direct download URL from supported gate providers."""
    if not url or not url.startswith("http"):
        return None

    if "hypeddit.com" in url:
        return resolve_hypeddit_download_url(url, session, timeout=timeout)
    if "toneden.io" in url:
        return resolve_toneden_download_url(url, session, timeout=timeout)

    return None

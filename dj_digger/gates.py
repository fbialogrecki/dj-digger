"""Bypass and resolver module for download gates (Hypeddit, ToneDen, etc.).

Extracts direct file download URLs from gate pages without requiring manual
social media login steps.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import unquote

import requests

LOGGER = logging.getLogger(__name__)

HYPEDDIT_RE = re.compile(r'https?://(?:www\.)?hypeddit\.com/(?:track/)?([a-zA-Z0-9_-]+)')
TONEDEN_RE = re.compile(r'https?://(?:www\.)?toneden\.io/([^/]+)/post/([a-zA-Z0-9_-]+)')

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _clean_url(raw_url: Optional[str]) -> Optional[str]:
    """Clean and validate an extracted download URL."""
    if not raw_url or not isinstance(raw_url, str):
        return None
    cleaned = unquote(raw_url.replace("\\/", "/").strip('"\' '))
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return None


def resolve_hypeddit_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> Optional[str]:
    """Resolve direct audio download URL from Hypeddit gate link."""
    match = HYPEDDIT_RE.search(url)
    if not match:
        return None

    gate_id = match.group(1)
    headers = {**DEFAULT_HEADERS, "Referer": url}

    try:
        # Request gate page HTML
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            text = resp.text

            # 1. Broad regex search for JS variables / HTML attributes
            patterns = [
                r'var\s+download_url\s*=\s*["\']([^"\']+)["\']',
                r'var\s+s3_url\s*=\s*["\']([^"\']+)["\']',
                r'var\s+file_url\s*=\s*["\']([^"\']+)["\']',
                r'var\s+file_download\s*=\s*["\']([^"\']+)["\']',
                r'data-download-url=["\']([^"\']+)["\']',
                r'data-s3-url=["\']([^"\']+)["\']',
                r'data-file=["\']([^"\']+)["\']',
                r'["\'](https?://[^"\']*(?:s3\.amazonaws\.com|hypeddit-downloads|hypeddit)[^"\']*\.(?:mp3|wav|zip|flac|aiff)[^"\']*)["\']',
                r'["\'](https?://s3[^\s"\'<>]+\.(?:mp3|wav|zip|flac|aiff)[^\s"\'<>]*)\b["\']',
                r'["\']download_url["\']\s*:\s*["\']([^"\']+)["\']',
                r'["\']s3_url["\']\s*:\s*["\']([^"\']+)["\']',
            ]
            for pattern in patterns:
                found = re.search(pattern, text, re.IGNORECASE)
                if found:
                    cleaned = _clean_url(found.group(1))
                    if cleaned:
                        return cleaned

            # 2. Extract hidden inputs
            input_match = re.search(r'name=["\'](?:download_key|key|id_downloads)["\']\s+value=["\']([^"\']+)["\']', text)
            download_key = input_match.group(1) if input_match else gate_id

            # 3. Try Hypeddit gate API download endpoints with AJAX headers
            ajax_headers = {
                **headers,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }

            api_urls = [
                "https://hypeddit.com/index.php?rm=gate/get_download_link",
                "https://hypeddit.com/index.php?rm=gate/download",
            ]

            for api_url in api_urls:
                for key_param in ("id", "gate_id", "fan_gate_id", "key"):
                    try:
                        post_data = {key_param: download_key}
                        api_resp = session.post(api_url, data=post_data, headers=ajax_headers, timeout=timeout)
                        if api_resp.status_code == 200:
                            try:
                                data = api_resp.json()
                                if isinstance(data, dict):
                                    for field in ("download_url", "url", "s3_url", "file", "download_link", "location"):
                                        cleaned = _clean_url(data.get(field))
                                        if cleaned:
                                            return cleaned
                            except ValueError:
                                html_dl = re.search(r'href=["\'](https?://[^"\']+)["\']', api_resp.text)
                                if html_dl:
                                    cleaned = _clean_url(html_dl.group(1))
                                    if cleaned and ("s3.amazonaws.com" in cleaned or "hypeddit" in cleaned or any(ext in cleaned for ext in (".mp3", ".wav", ".zip", ".flac", ".aiff"))):
                                        return cleaned
                    except requests.RequestException:
                        continue

    except requests.RequestException as exc:
        LOGGER.debug("Hypeddit gate resolution failed for %s: %s", url, exc)

    return None


def resolve_toneden_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> Optional[str]:
    """Resolve direct audio download URL from ToneDen fan gate link."""
    match = TONEDEN_RE.search(url)
    if not match:
        return None

    headers = {**DEFAULT_HEADERS, "Referer": url}

    try:
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            text = resp.text

            # 1. Extract JSON state from page HTML
            json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});</script>', text, re.DOTALL)
            if json_match:
                try:
                    state = json.loads(json_match.group(1))
                    state_str = json.dumps(state)
                    for key_name in ("download_url", "free_download_location", "s3_url", "file_url"):
                        dl_match = re.search(rf'"{key_name}"\s*:\s*"([^"]+)"', state_str)
                        if dl_match:
                            cleaned = _clean_url(dl_match.group(1))
                            if cleaned:
                                return cleaned
                except Exception:
                    pass

            # 2. Try ToneDen fan gate API directly
            gate_slug = match.group(2)
            api_url = f"https://www.toneden.io/api/v1/fan_gates/slug/{gate_slug}"
            api_resp = session.get(api_url, headers=headers, timeout=timeout)
            if api_resp.status_code == 200:
                try:
                    data = api_resp.json()
                    if isinstance(data, dict):
                        for field in ("download_url", "free_download_location", "s3_url", "file_url"):
                            cleaned = _clean_url(data.get(field))
                            if cleaned:
                                return cleaned
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


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
    """Resolve direct audio download URL from Hypeddit gate link by simulating step completion."""
    match = HYPEDDIT_RE.search(url)
    if not match:
        return None

    gate_id = match.group(1)
    headers = {**DEFAULT_HEADERS, "Referer": url}

    try:
        # Step 0: GET gate page HTML (establishes session cookies like PHPSESSID)
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None

        text = resp.text

        # 1. Immediate regex search in HTML source for pre-embedded download URLs
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

        # 2. Collect hidden inputs from page HTML
        form_data = {}
        for input_tag in re.findall(r'<input[^>]+>', text, re.IGNORECASE):
            name_m = re.search(r'name=["\']([^"\']+)["\']', input_tag, re.IGNORECASE)
            val_m = re.search(r'value=["\']([^"\']+)["\']', input_tag, re.IGNORECASE)
            if name_m and val_m:
                form_data[name_m.group(1)] = val_m.group(1)

        download_key = form_data.get("download_key") or form_data.get("key") or form_data.get("id") or gate_id

        # Prepare AJAX headers
        ajax_headers = {
            **headers,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        # 3. Simulate comment step ("Amazing track!")
        comment_endpoints = [
            "https://hypeddit.com/index.php?rm=gate/save_comment",
            "https://hypeddit.com/index.php?rm=gate/comment",
            "https://hypeddit.com/index.php?rm=gate/add_comment",
        ]
        for comment_url in comment_endpoints:
            try:
                comment_payload = {
                    "id": download_key,
                    "comment": "Amazing track!",
                    "email": "listener@gmail.com",
                    "name": "Listener",
                    **form_data,
                }
                session.post(comment_url, data=comment_payload, headers=ajax_headers, timeout=timeout)
            except requests.RequestException:
                pass

        # 4. Simulate step progression (clicking links, steps 1..5)
        step_endpoints = [
            "https://hypeddit.com/index.php?rm=gate/save_step",
            "https://hypeddit.com/index.php?rm=gate/step",
            "https://hypeddit.com/index.php?rm=gate/next_step",
            "https://hypeddit.com/index.php?rm=gate/complete_step",
            "https://hypeddit.com/index.php?rm=gate/step_done",
        ]
        for step_num in range(1, 6):
            for step_url in step_endpoints:
                try:
                    step_payload = {
                        "id": download_key,
                        "step": step_num,
                        "action": "complete",
                        "status": "done",
                        **form_data,
                    }
                    session.post(step_url, data=step_payload, headers=ajax_headers, timeout=timeout)
                except requests.RequestException:
                    pass

        # 5. Final download link resolution from Hypeddit API
        dl_endpoints = [
            "https://hypeddit.com/index.php?rm=gate/get_download_link",
            "https://hypeddit.com/index.php?rm=gate/download",
            "https://hypeddit.com/index.php?rm=gate/finish",
            f"https://hypeddit.com/sc_download.php?id={download_key}",
            f"https://hypeddit.com/download.php?id={download_key}",
        ]

        for dl_url in dl_endpoints:
            if "index.php" in dl_url:
                for key_param in ("id", "gate_id", "fan_gate_id", "key"):
                    try:
                        post_data = {key_param: download_key, **form_data}
                        api_resp = session.post(dl_url, data=post_data, headers=ajax_headers, timeout=timeout)
                        if api_resp.status_code == 200:
                            try:
                                data = api_resp.json()
                                if isinstance(data, dict):
                                    for field in ("download_url", "url", "s3_url", "file", "download_link", "location", "redirect"):
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
            else:
                try:
                    get_resp = session.get(dl_url, headers=headers, timeout=timeout, allow_redirects=False)
                    if get_resp.status_code in (200, 301, 302):
                        redirect = get_resp.headers.get("Location")
                        if redirect:
                            cleaned = _clean_url(redirect)
                            if cleaned:
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


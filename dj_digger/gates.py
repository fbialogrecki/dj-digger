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


def _clean_url(raw_url: Optional[str], *, allow_preview: bool = False) -> Optional[str]:
    """Clean and validate an extracted download URL. Rejects audio preview clips (_preview)."""
    if not raw_url or not isinstance(raw_url, str):
        return None
    import html
    cleaned = html.unescape(raw_url.replace("\\/", "/")).strip('"\' ')
    if not allow_preview and "_preview" in cleaned.lower():
        return None
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

        # 1. Immediate regex search in HTML source for pre-embedded full download URLs
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

        # 2. Extract input fields, jsonGateData, and CSRF token
        csrf_m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', text, re.IGNORECASE)
        if not csrf_m:
            csrf_m = re.search(r'content=["\']([^"\']+)["\']\s+name=["\']csrf-token["\']', text, re.IGNORECASE)
        csrf_token = csrf_m.group(1) if csrf_m else ""

        extern_id = ""
        gate_data_m = re.search(r'var\s+jsonGateData\s*=\s*({.*?});', text)
        if gate_data_m:
            try:
                import json
                extern_id = json.loads(gate_data_m.group(1)).get("externID", "")
            except Exception:
                pass

        inputs = {}
        for tag in re.findall(r'<input[^>]+>', text, re.IGNORECASE):
            name_m = re.search(r'name=["\']([^"\']+)["\']', tag, re.IGNORECASE)
            id_m = re.search(r'id=["\']([^"\']+)["\']', tag, re.IGNORECASE)
            val_m = re.search(r'value=["\']([^"\']*)["\']', tag, re.IGNORECASE)
            key = name_m.group(1) if name_m else (id_m.group(1) if id_m else None)
            if key and val_m:
                inputs[key] = val_m.group(1)

        fan_gate_id = inputs.get("fan_gate_id") or inputs.get("fangate_id") or gate_id
        download_key = inputs.get("current_download_file_listner") or inputs.get("fangate_id") or fan_gate_id
        email = "music.listener@yahoo.com"

        # Prepare AJAX headers with CSRF token
        ajax_headers = {
            **headers,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        if csrf_token:
            ajax_headers["X-CSRF-TOKEN"] = csrf_token

        # 3. Execute step completion calls for all steps declared in nwSteps
        step_calls = [
            ("https://hypeddit.com/verifyEmailAddress", {"_token": csrf_token, "validateEmailAddress": email, "fan_gate_id": fan_gate_id, "email_name": "Music Listener"}),
            ("https://hypeddit.com/setSC", {"_token": csrf_token, "fan_gate_id": fan_gate_id, "comment_sc": "Awesome track!", "is_repost": 1, "is_subscribe": 1}),
            ("https://hypeddit.com/setYT", {"_token": csrf_token, "fan_gate_id": fan_gate_id, "comment_yt": "Awesome track!"}),
        ]
        nw_steps = inputs.get("nwSteps", "email,sc").split(",")
        for st in nw_steps:
            st = st.strip()
            if st:
                step_calls.append(("https://hypeddit.com/setGatePathway", {"_token": csrf_token, "fan_gate_id": fan_gate_id, "stepName": st}))
                step_calls.append(("https://hypeddit.com/setGatePathwayOr", {"_token": csrf_token, "fan_gate_id": fan_gate_id, "skipSteps": "", "selectedStep": st}))

        for ep_url, payload in step_calls:
            try:
                session.post(ep_url, data=payload, headers=ajax_headers, timeout=timeout)
            except requests.RequestException:
                pass

        # 4. Try resolve full original file via gate/download/ul
        try:
            dl_resp = session.post(
                "https://hypeddit.com/gate/download/ul",
                data={
                    "_token": csrf_token,
                    "file": download_key,
                    "download_visit": "true",
                    "profile_downloads": "true",
                    "page": "nonsingle",
                    "is_skippable": inputs.get("is_skippable", "0"),
                    "steps": inputs.get("nwSteps", ""),
                    "email": email,
                    "download_action": "DOWNLOAD",
                    "wrndk": inputs.get("wrndk", ""),
                    "gvf": inputs.get("gvf", "0"),
                    "gvt": inputs.get("gvt", ""),
                    "external_id": extern_id,
                    "fan_gate_id": fan_gate_id,
                },
                headers=ajax_headers,
                timeout=timeout,
            )
            if dl_resp.status_code == 200:
                try:
                    data = dl_resp.json()
                    if isinstance(data, dict) and data.get("download_status") and data.get("URL"):
                        cleaned = _clean_url(data.get("URL"))
                        if cleaned:
                            return cleaned
                except ValueError:
                    pass
        except requests.RequestException:
            pass

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


DROPLOUD_RE = re.compile(
    r"https?://(?:www\.)?droploud\.com/track/([a-f0-9\-]{36}|[a-zA-Z0-9_\-]+)", re.I
)


def resolve_droploud_download_url(
    url: str, session: requests.Session, timeout: float = 10.0
) -> Optional[str]:
    """Resolve direct audio stream/download URL from Droploud track gate."""
    match = DROPLOUD_RE.search(url)
    if not match:
        return None
    track_id = match.group(1)
    api_url = f"https://api.droploud.com/api/tracks/{track_id}"
    try:
        resp = session.get(api_url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("stream_url"):
                stream_path = data["stream_url"]
                if stream_path.startswith("http"):
                    return stream_path
                return f"https://api.droploud.com{stream_path}"
    except requests.RequestException as exc:
        LOGGER.debug("Droploud gate resolution failed for %s: %s", url, exc)
    return None


def resolve_mediafire_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> Optional[str]:
    """Extract direct download link from MediaFire page."""
    try:
        resp = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            match = re.search(r'href=["\'](https?://download\d+\.mediafire\.com/[^"\']+)["\']', resp.text)
            if match:
                return match.group(1)
            btn = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', resp.text)
            if btn:
                return btn.group(1)
    except requests.RequestException:
        pass
    return url


def resolve_dropbox_download_url(url: str) -> str:
    """Convert Dropbox link to direct download link."""
    return url.replace("www.dropbox.com", "dl.dropboxusercontent.com").replace("dl=0", "dl=1")


def resolve_google_drive_download_url(url: str) -> str:
    """Convert Google Drive view link to direct download link."""
    m = re.search(r'/d/([a-zA-Z0-9_-]+)', url) or re.search(r'id=([a-zA-Z0-9_-]+)', url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url


def resolve_gate_download_url(url: str, session: requests.Session, timeout: float = 10.0) -> Optional[str]:
    """Inspect and resolve direct download URL from supported gate providers and cloud storage."""
    if not url or not url.startswith("http"):
        return None

    if "hypeddit.com" in url or "hypd.it" in url:
        return resolve_hypeddit_download_url(url, session, timeout=timeout)
    if "droploud.com" in url:
        return resolve_droploud_download_url(url, session, timeout=timeout)
    if "toneden.io" in url:
        return resolve_toneden_download_url(url, session, timeout=timeout)
    if "mediafire.com" in url:
        return resolve_mediafire_download_url(url, session, timeout=timeout)
    if "dropbox.com" in url or "dropboxusercontent.com" in url:
        return resolve_dropbox_download_url(url)
    if "drive.google.com" in url or "docs.google.com" in url:
        return resolve_google_drive_download_url(url)

    # Direct audio file links (S3, R2, CDN, raw audio files)
    lower_url = url.lower()
    if any(lower_url.endswith(ext) or f"{ext}?" in lower_url for ext in (".mp3", ".wav", ".flac", ".zip", ".aiff")):
        return url

    return None


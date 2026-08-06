"""Authentication module for SoundCloud accounts.

Handles saving/loading OAuth tokens, scanning browser cookies (supporting Linux,
macOS, and WSL/Windows paths), and verifying credentials with SoundCloud's /me API.
"""

from __future__ import annotations

import base64
import glob
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from platformdirs import user_config_dir

CONFIG_DIR = Path(user_config_dir("dj-digger"))
AUTH_FILE = CONFIG_DIR / "auth.json"
OAUTH_TOKEN_RE = re.compile(r'^[0-9]+-[0-9]+-[A-Za-z0-9_-]+')

LOGGER = logging.getLogger(__name__)


def auth_file_path() -> Path:
    return AUTH_FILE


def get_stored_token() -> Optional[str]:
    """Retrieve OAuth token from environment variable or saved config file."""
    env_token = os.environ.get("SOUNDCLOUD_OAUTH_TOKEN", "").strip()
    if env_token:
        return env_token

    if not AUTH_FILE.exists():
        return None

    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            token = data.get("oauth_token")
            if isinstance(token, str) and token.strip():
                return token.strip()
    except Exception as exc:
        LOGGER.debug("Failed to read auth.json: %s", exc)

    return None


def get_stored_auth_info() -> Dict[str, Any]:
    """Get full stored auth details (token, username, etc.)."""
    if not AUTH_FILE.exists():
        return {}
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


import tempfile


def save_token(token: str, username: str = "", user_id: Optional[int] = None) -> None:
    """Save an OAuth token and metadata to user config file with strict 0600 permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "oauth_token": token.strip(),
        "username": username,
        "user_id": user_id,
    }
    AUTH_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError as exc:
        LOGGER.debug("Could not set strict 0600 permissions on auth.json: %s", exc)


def clear_token() -> None:
    """Delete saved authentication info."""
    if AUTH_FILE.exists():
        try:
            AUTH_FILE.unlink()
        except OSError as exc:
            LOGGER.debug("Could not remove auth.json: %s", exc)


def verify_token(token: str, client_id: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Test token against SoundCloud's /me endpoint.

    Returns user data dictionary if valid, None if invalid/unauthorized.
    """
    token = token.strip()
    if not token:
        return None

    url = f"https://api-v2.soundcloud.com/me?client_id={client_id}"
    headers = {
        "Authorization": f"OAuth {token}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }

    try:
        res = requests.get(url, headers=headers, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict) and "username" in data:
                return data
    except Exception as exc:
        LOGGER.debug("Token verification request failed: %s", exc)

    return None


def _extract_sqlite_cookies(db_path: str) -> List[str]:
    """Extract oauth_token values from unencrypted SQLite cookies database (e.g. Firefox)."""
    if not os.path.exists(db_path):
        return []

    tokens: List[str] = []
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        os.chmod(tmp_path, 0o600)
        shutil.copyfile(db_path, tmp_path)

        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        # Check moz_cookies (Firefox)
        try:
            cursor.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%soundcloud%' AND name = 'oauth_token'"
            )
            for row in cursor.fetchall():
                if row[1] and isinstance(row[1], str) and row[1].strip():
                    tokens.append(row[1].strip())
        except sqlite3.OperationalError:
            pass

        # Check cookies (Chromium)
        try:
            cursor.execute(
                "SELECT name, value FROM cookies WHERE host_key LIKE '%soundcloud%' AND name = 'oauth_token'"
            )
            for row in cursor.fetchall():
                if row[1] and isinstance(row[1], str) and row[1].strip():
                    tokens.append(row[1].strip())
        except sqlite3.OperationalError:
            pass

        conn.close()
    except Exception:
        pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return tokens


def find_browser_cookie_paths() -> List[str]:
    """Locate browser cookie database files on Linux, macOS, and WSL/Windows paths."""
    candidate_paths: List[str] = []

    # Linux / macOS standard paths
    home = Path.home()
    candidate_paths.extend(glob.glob(str(home / ".mozilla/firefox/*/cookies.sqlite")))
    candidate_paths.extend(glob.glob(str(home / ".config/google-chrome/*/Network/Cookies")))
    candidate_paths.extend(glob.glob(str(home / ".config/chromium/*/Network/Cookies")))

    # WSL Windows paths (/mnt/c/Users/<user>/...)
    if os.path.exists("/mnt/c/Users"):
        for win_user_dir in glob.glob("/mnt/c/Users/*"):
            # Firefox on Windows (unencrypted SQLite)
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite"))
            # Chrome / Edge / Brave on Windows
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Local/Google/Chrome/User Data/*/Network/Cookies"))
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Local/Microsoft/Edge/User Data/*/Network/Cookies"))
            candidate_paths.extend(glob.glob(f"{win_user_dir}/AppData/Local/BraveSoftware/Brave-Browser/User Data/*/Network/Cookies"))

    return candidate_paths


def _decrypt_win_dpapi_aes(enc_key_b64: str, enc_val_b64: str) -> Optional[str]:
    """Decrypt Windows Chromium DPAPI+AES-GCM cookie payload using powershell.exe interop in WSL."""
    if not shutil.which("powershell.exe"):
        return None

    ps_script = (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -AssemblyName System.Security; "
        f"$encKey = [System.Convert]::FromBase64String('{enc_key_b64}'); "
        "$key = [System.Security.Cryptography.ProtectedData]::Unprotect($encKey, $null, [System.Security.Cryptography.DataProtectionScope]::CurrentUser); "
        f"$cipherBytes = [System.Convert]::FromBase64String('{enc_val_b64}'); "
        "if ($cipherBytes.Length -lt 31) { exit 1 }; "
        "$nonce = [byte[]]::new(12); [Array]::Copy($cipherBytes, 3, $nonce, 0, 12); "
        "$cipherLen = $cipherBytes.Length - 15 - 16; $ciphertext = [byte[]]::new($cipherLen); [Array]::Copy($cipherBytes, 15, $ciphertext, 0, $cipherLen); "
        "$tag = [byte[]]::new(16); [Array]::Copy($cipherBytes, $cipherBytes.Length - 16, $tag, 0, 16); "
        "$code = @'\n"
        "using System;\nusing System.Text;\nusing System.Runtime.InteropServices;\n"
        "public class AesGcmDecryptor {\n"
        "    [DllImport(\"bcrypt.dll\")] private static extern int BCryptOpenAlgorithmProvider(out IntPtr hAlg, string id, string imp, uint flags);\n"
        "    [DllImport(\"bcrypt.dll\")] private static extern int BCryptCloseAlgorithmProvider(IntPtr hAlg, uint flags);\n"
        "    [DllImport(\"bcrypt.dll\")] private static extern int BCryptSetProperty(IntPtr hObj, string name, byte[] val, int len, int flags);\n"
        "    [DllImport(\"bcrypt.dll\")] private static extern int BCryptGenerateSymmetricKey(IntPtr hAlg, out IntPtr hKey, IntPtr obj, int objLen, byte[] secret, int secretLen, uint flags);\n"
        "    [DllImport(\"bcrypt.dll\")] private static extern int BCryptDestroyKey(IntPtr hKey);\n"
        "    [DllImport(\"bcrypt.dll\")] private static extern int BCryptDecrypt(IntPtr hKey, byte[] inBytes, int inLen, ref INFO info, byte[] iv, int ivLen, byte[] outBytes, int outLen, out int resLen, uint flags);\n"
        "    [StructLayout(LayoutKind.Sequential)] private struct INFO { public int size; public uint ver; public IntPtr pNonce; public int cbNonce; public IntPtr pAuth; public int cbAuth; public IntPtr pTag; public int cbTag; public IntPtr pMac; public int cbMac; public int cbAAD; public long cbData; public uint flags; }\n"
        "    public static string Decrypt(byte[] cipher, byte[] nonce, byte[] tag, byte[] key) {\n"
        "        IntPtr hAlg, hKey;\n"
        "        if (BCryptOpenAlgorithmProvider(out hAlg, \"AES\", null, 0) != 0) return null;\n"
        "        try {\n"
        "            byte[] mode = Encoding.Unicode.GetBytes(\"ChainingModeGCM\\0\");\n"
        "            BCryptSetProperty(hAlg, \"ChainingMode\", mode, mode.Length, 0);\n"
        "            if (BCryptGenerateSymmetricKey(hAlg, out hKey, IntPtr.Zero, 0, key, key.Length, 0) != 0) return null;\n"
        "            try {\n"
        "                GCHandle hN = GCHandle.Alloc(nonce, GCHandleType.Pinned);\n"
        "                GCHandle hT = GCHandle.Alloc(tag, GCHandleType.Pinned);\n"
        "                try {\n"
        "                    var info = new INFO(); info.size = Marshal.SizeOf(info); info.ver = 1;\n"
        "                    info.pNonce = hN.AddrOfPinnedObject(); info.cbNonce = nonce.Length;\n"
        "                    info.pTag = hT.AddrOfPinnedObject(); info.cbTag = tag.Length;\n"
        "                    byte[] plain = new byte[cipher.Length]; int plainLen;\n"
        "                    if (BCryptDecrypt(hKey, cipher, cipher.Length, ref info, null, 0, plain, plain.Length, out plainLen, 0) == 0) {\n"
        "                        return Encoding.UTF8.GetString(plain, 0, plainLen);\n"
        "                    }\n"
        "                } finally { hN.Free(); hT.Free(); }\n"
        "            } finally { BCryptDestroyKey(hKey); }\n"
        "        } finally { BCryptCloseAlgorithmProvider(hAlg, 0); }\n"
        "        return null;\n"
        "    }\n"
        "}\n"
        "'@; Add-Type -TypeDefinition $code; "
        "$res = [AesGcmDecryptor]::Decrypt($ciphertext, $nonce, $tag, $key); "
        "if ($res) { Write-Output $res }"
    )

    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception as exc:
        LOGGER.debug("Powershell DPAPI decryption failed: %s", exc)
    return None


def _extract_wsl_windows_chromium_cookies() -> List[str]:
    """Scan Windows Chrome/Edge/Brave cookies from WSL using powershell.exe DPAPI interop."""
    if not os.path.exists("/mnt/c/Users") or not shutil.which("powershell.exe"):
        return []

    tokens: List[str] = []
    for win_user_dir in glob.glob("/mnt/c/Users/*"):
        browser_roots = [
            f"{win_user_dir}/AppData/Local/Google/Chrome/User Data",
            f"{win_user_dir}/AppData/Local/Microsoft/Edge/User Data",
            f"{win_user_dir}/AppData/Local/BraveSoftware/Brave-Browser/User Data",
        ]
        for root in browser_roots:
            local_state_path = os.path.join(root, "Local State")
            if not os.path.exists(local_state_path):
                continue
            try:
                data = json.loads(Path(local_state_path).read_text(encoding="utf-8"))
                enc_key_raw = data.get("os_crypt", {}).get("encrypted_key")
                if not enc_key_raw or not isinstance(enc_key_raw, str):
                    continue
                raw_bytes = base64.b64decode(enc_key_raw)
                if not raw_bytes.startswith(b"DPAPI"):
                    continue
                enc_key_b64 = base64.b64encode(raw_bytes[5:]).decode("ascii")

                cookie_dbs = glob.glob(f"{root}/*/Network/Cookies") + glob.glob(f"{root}/*/Cookies")
                for db in cookie_dbs:
                    if not os.path.exists(db):
                        continue
                    tmp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp_file:
                            tmp_path = tmp_file.name
                        os.chmod(tmp_path, 0o600)
                        shutil.copyfile(db, tmp_path)

                        conn = sqlite3.connect(tmp_path)
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT encrypted_value FROM cookies WHERE host_key LIKE '%soundcloud%' AND name = 'oauth_token'"
                        )
                        for row in cursor.fetchall():
                            val = row[0]
                            if val and isinstance(val, bytes) and len(val) > 15:
                                val_b64 = base64.b64encode(val).decode("ascii")
                                token = _decrypt_win_dpapi_aes(enc_key_b64, val_b64)
                                if token:
                                    tokens.append(token)
                        conn.close()
                    except Exception as exc:
                        LOGGER.debug("Failed querying WSL chromium db %s: %s", db, exc)
                    finally:
                        if tmp_path and os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
            except Exception as exc:
                LOGGER.debug("Failed reading Local State %s: %s", local_state_path, exc)

    return tokens


def scan_browser_cookies() -> List[str]:
    """Find any plaintext oauth_token values stored in browser cookie stores."""
    found_tokens: List[str] = []
    for path in find_browser_cookie_paths():
        if "cookies.sqlite" in path:
            found_tokens.extend(_extract_sqlite_cookies(path))

    # Also scan Windows Chrome/Edge/Brave cookies if running inside WSL
    found_tokens.extend(_extract_wsl_windows_chromium_cookies())

    # Deduplicate while preserving order
    seen = set()
    result = []
    for t in found_tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def auto_detect_and_verify(client_id: str) -> Optional[Tuple[str, str, int]]:
    """Scan available browser cookie stores for a valid SoundCloud OAuth token.

    Returns (token, username, user_id) if a working session is found, else None.
    """
    tokens = scan_browser_cookies()
    for token in tokens:
        user_data = verify_token(token, client_id)
        if user_data:
            username = user_data.get("username") or "User"
            user_id = user_data.get("id")
            save_token(token, username, user_id)
            return token, username, user_id
    return None

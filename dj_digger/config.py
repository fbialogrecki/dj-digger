"""Configuration settings for user profile, download gates, keybindings, and local scan dirs.

Stores user name, email, hype comments, custom keybindings, footer actions,
and scan directories (~/.config/dj-digger/config.json).
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import Dict, List, Optional

LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Music Listener"
DEFAULT_EMAIL = "music.listener@yahoo.com"
DEFAULT_COMMENTS = ["Love it!", "Amazing track!", "Dope tune!", "Fire!", "Banger!", "Great tune!"]

DEFAULT_KEYBINDINGS: Dict[str, str] = {
    "mark_got": "g",
    "mark_skip": "s",
    "clear_mark": "u",
    "remove_track": "x",
    "copy_path": "c",
    "context_menu": "m",
}

DEFAULT_FOOTER_KEYS: List[Dict[str, str]] = [
    {"key": "space", "label": "Play/Pause"},
    {"key": "g", "label": "Got"},
    {"key": "s", "label": "Skip"},
    {"key": "m", "label": "Menu"},
    {"key": "?", "label": "Help"},
]


def default_config_path() -> Path:
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "dj-digger"
    return config_dir / "config.json"


class AppConfig:
    """User profile, keybindings, and scan settings with JSON persistence."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_config_path()
        self.user_name: str = DEFAULT_NAME
        self.user_email: str = DEFAULT_EMAIL
        self.custom_comments: List[str] = list(DEFAULT_COMMENTS)
        self.scan_directories: List[str] = [
            str(Path.home() / "Music"),
            str(Path.home() / "Downloads"),
        ]
        self.keybindings: Dict[str, str] = dict(DEFAULT_KEYBINDINGS)
        self.footer_keys: List[Dict[str, str]] = list(DEFAULT_FOOTER_KEYS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.user_name = str(raw.get("user_name") or DEFAULT_NAME).strip()
                self.user_email = str(raw.get("user_email") or DEFAULT_EMAIL).strip()
                comments = raw.get("custom_comments")
                if isinstance(comments, list) and comments:
                    cleaned = [str(c).strip() for c in comments if str(c).strip()]
                    if cleaned:
                        self.custom_comments = cleaned
                scan_dirs = raw.get("scan_directories")
                if isinstance(scan_dirs, list) and scan_dirs:
                    self.scan_directories = [str(d).strip() for d in scan_dirs if str(d).strip()]
                keys = raw.get("keybindings")
                if isinstance(keys, dict):
                    for action, key_str in keys.items():
                        if isinstance(key_str, str) and key_str.strip():
                            self.keybindings[action] = key_str.strip().lower()
                footer = raw.get("footer_keys")
                if isinstance(footer, list) and footer:
                    self.footer_keys = [f for f in footer if isinstance(f, dict) and "key" in f and "label" in f]
        except FileNotFoundError:
            self.save()
        except (OSError, ValueError) as exc:
            LOGGER.warning("Could not load config from %s: %s", self.path, exc)

    def save(self) -> None:
        payload = {
            "user_name": self.user_name,
            "user_email": self.user_email,
            "custom_comments": self.custom_comments,
            "scan_directories": self.scan_directories,
            "keybindings": self.keybindings,
            "footer_keys": self.footer_keys,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            LOGGER.warning("Could not save config to %s: %s", self.path, exc)

    def random_comment(self) -> str:
        pool = self.custom_comments or DEFAULT_COMMENTS
        return random.choice(pool)

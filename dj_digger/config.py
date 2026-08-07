"""Configuration settings for user profile and download gate automation.

Stores user name, email, and randomized hype comments used when completing
download gate steps automatically (Hypeddit, GateRush, ToneDen, Droploud, etc.).
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import List, Optional

from platformdirs import user_config_dir

LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Music Listener"
DEFAULT_EMAIL = "music.listener@yahoo.com"
DEFAULT_COMMENTS = [
    "Love it!",
    "Amazing track!",
    "Dope tune!",
    "Fire!",
    "Banger!",
    "Great tune!",
    "Sick beats!",
    "Massive track!",
    "Huge release!",
    "Pure energy!",
    "Loving this!",
    "Absolute banger!",
    "So good!",
    "Fire tune!",
    "On repeat!",
    "Incredible vibes!",
    "Mad energy!",
    "Tune of the month!",
    "Unreal sound!",
    "Masterpiece!",
    "Insane production!",
    "Heavyweight track!",
    "Quality sound!",
    "Peak time banger!",
    "Top notch!",
    "What a tune!",
    "Straight fire!",
    "Sublime!",
    "Certified banger!",
    "Needed this!",
]


def default_config_path() -> Path:
    return Path(user_config_dir("dj-digger")) / "config.json"


class AppConfig:
    """User profile and gate automation settings with JSON persistence."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_config_path()
        self.user_name: str = DEFAULT_NAME
        self.user_email: str = DEFAULT_EMAIL
        self.custom_comments: List[str] = list(DEFAULT_COMMENTS)
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
        except FileNotFoundError:
            self.save()
        except (OSError, ValueError) as exc:
            LOGGER.warning("Could not load config from %s: %s", self.path, exc)

    def save(self) -> None:
        payload = {
            "user_name": self.user_name,
            "user_email": self.user_email,
            "custom_comments": self.custom_comments,
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

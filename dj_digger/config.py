"""What the app remembers about you, in ~/.config/dj-digger/config.json.

Your name and email, which the gate resolvers submit on your behalf; the
comments they leave; where to look for music you already own; and which browser
opens links.
"""

import json
import logging
import os
import random
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Music Listener"
# ``gates`` submits this address to third-party download gates automatically, so
# the default must not be deliverable to anybody. RFC 2606 reserves .invalid for
# exactly this. The previous default was a real-looking address at a real
# provider, which meant every unconfigured install was signing a stranger up for
# artist mailing lists - so it is retired rather than merely changed.
DEFAULT_EMAIL = "dj-digger@example.invalid"
RETIRED_EMAILS = frozenset({"music.listener@yahoo.com"})
DEFAULT_COMMENTS = ["Love it!", "Amazing track!", "Dope tune!", "Fire!", "Banger!", "Great tune!"]


def default_config_path() -> Path:
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "dj-digger"
    return config_dir / "config.json"


class AppConfig:
    """User profile and preferences, persisted as JSON."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_config_path()
        self.user_name: str = DEFAULT_NAME
        self.user_email: str = DEFAULT_EMAIL
        self.custom_comments: list[str] = list(DEFAULT_COMMENTS)
        self.scan_directories: list[str] = [
            str(Path.home() / "Music"),
            str(Path.home() / "Downloads"),
        ]
        # Empty means the system default. Anything else is checked against what
        # the machine reports before it is used - see browser.resolve_choice.
        self.browser: str = ""
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.user_name = str(raw.get("user_name") or DEFAULT_NAME).strip()
                self.user_email = str(raw.get("user_email") or DEFAULT_EMAIL).strip()
                if self.user_email.lower() in RETIRED_EMAILS:
                    # Saved by an older version that shipped a stranger's address
                    # as the default. Nobody chose it, so it does not survive.
                    self.user_email = DEFAULT_EMAIL
                comments = raw.get("custom_comments")
                if isinstance(comments, list) and comments:
                    cleaned = [str(c).strip() for c in comments if str(c).strip()]
                    if cleaned:
                        self.custom_comments = cleaned
                scan_dirs = raw.get("scan_directories")
                if isinstance(scan_dirs, list) and scan_dirs:
                    self.scan_directories = [str(d).strip() for d in scan_dirs if str(d).strip()]
                self.browser = str(raw.get("browser") or "").strip()
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
            "browser": self.browser,
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

    def has_real_email(self) -> bool:
        """False while the profile still carries the unroutable placeholder."""

        return bool(self.user_email) and not self.user_email.lower().endswith(".invalid")

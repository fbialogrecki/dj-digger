"""Opening links in the user's browser."""

from __future__ import annotations

import logging
import time
import webbrowser
from typing import Callable, Iterable, Optional

BROWSER_CHOICES = ["default", "chrome", "firefox", "edge", "safari", "opera"]
BROWSER_ALIASES = {
    "chrome": "chrome",
    "firefox": "firefox",
    "edge": "edge",
    "safari": "safari",
    "opera": "opera",
}

LOGGER = logging.getLogger(__name__)


def resolve_controller(browser: str = "default") -> webbrowser.BaseBrowser:
    target = BROWSER_ALIASES.get(browser, browser)
    try:
        return webbrowser.get(target if browser != "default" else None)
    except webbrowser.Error as exc:
        LOGGER.warning(
            "Could not resolve browser '%s' (%s). Falling back to the system default.",
            browser,
            exc,
        )
        return webbrowser.get()


def open_url(url: str, browser: str = "default") -> bool:
    try:
        resolve_controller(browser).open_new_tab(url)
        return True
    except Exception as exc:  # webbrowser raises a grab bag of platform errors
        LOGGER.error("Failed to open %s: %s", url, exc)
        return False


def open_urls(
    urls: Iterable[str],
    browser: str = "default",
    *,
    pause: float = 0.1,
    controller: Optional[webbrowser.BaseBrowser] = None,
    on_success: Optional[Callable[[int, str], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
) -> int:
    """Open several links in tabs. Returns how many actually opened."""

    import os
    # Prevent wslview on WSL from running slow curl --head checks on every URL
    os.environ["WSLVIEW_SKIP_VALIDATION_CHECK"] = "1"

    controller = controller or resolve_controller(browser)
    opened = 0
    for index, url in enumerate(urls):
        try:
            res = controller.open_new_tab(url)
            if res is False:
                err_msg = f"Browser rejected opening tab #{index + 1}: {url}"
                LOGGER.error("%s", err_msg)
                if on_error:
                    on_error(err_msg)
            else:
                opened += 1
                if on_success:
                    on_success(index, url)
        except Exception as exc:
            err_msg = f"Failed to open tab #{index + 1} ({url}): {exc}"
            LOGGER.error("%s", err_msg)
            if on_error:
                on_error(err_msg)
            continue
        if pause > 0:
            time.sleep(pause)
    return opened

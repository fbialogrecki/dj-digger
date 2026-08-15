"""Opening links in the user's browser."""

from __future__ import annotations

import logging
import time
import webbrowser
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

BROWSER_CHOICES = ["default", "chrome", "firefox", "edge", "safari", "opera"]

# Every link that reaches this module came from somewhere we do not control: a
# ``purchase_url`` any artist can set, an anchor scraped off a track page, or a
# summary file handed to ``dj-digger open``. Handing the operating system
# anything other than a web address is how that turns into a problem -
# ``file://`` and ``\\host\share`` read local or remote paths (on WSL a UNC path
# is an outbound SMB authentication), ``javascript:`` and ``data:`` execute in
# whatever the platform hands them to. So the scheme is checked, not the host.
SAFE_SCHEMES = frozenset({"http", "https"})

LOGGER = logging.getLogger(__name__)


def is_openable(url: str) -> bool:
    """True when this is a web address we are willing to hand to the OS.

    A host is required as well as the scheme: ``http:///etc/passwd`` parses with
    the right scheme and no host at all.
    """

    try:
        parsed = urlparse((url or "").strip())
    except ValueError:  # malformed IPv6 literals and the like
        return False
    return parsed.scheme.lower() in SAFE_SCHEMES and bool(parsed.netloc)


def resolve_controller(browser: str = "default") -> webbrowser.BaseBrowser:
    try:
        return webbrowser.get(browser if browser != "default" else None)
    except webbrowser.Error as exc:
        LOGGER.warning(
            "Could not resolve browser '%s' (%s). Falling back to the system default.",
            browser,
            exc,
        )
        return webbrowser.get()


def open_url(url: str, browser: str = "default") -> bool:
    if not is_openable(url):
        LOGGER.error("Refusing to open %r - only http and https links are opened.", url)
        return False
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
        if not is_openable(url):
            err_msg = f"Refused tab #{index + 1}: {url!r} is not an http or https link"
            LOGGER.error("%s", err_msg)
            if on_error:
                on_error(err_msg)
            continue
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

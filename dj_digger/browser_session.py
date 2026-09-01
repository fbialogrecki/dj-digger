"""The one Playwright lifecycle layer, shared by the store cart and the gate browser.

Both the Bandcamp cart and the Hypeddit fallback drive the same kind of
persistent Chromium profile, and each used to carry its own copy of the launch
error wording. This is the single place that knows how Chromium is started,
what its failures mean, and how to install it.
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Any

from .paths import data_dir

LOGGER = logging.getLogger(__name__)

# Default Playwright action timeout for pages in a managed context.
ACTION_TIMEOUT_MS = 15_000


class AutomationError(RuntimeError):
    """A technical or structural failure which must never trigger store fallback."""


class ChromiumMissing(AutomationError):
    """The Playwright browser required by store carts has not been downloaded."""


def store_profile_path() -> Path:
    """Create the private, persistent Chromium profile outside the repository."""

    path = data_dir() / "store-browser"
    # mkdir's mode is masked by the umask and ignored when the directory already
    # exists, so the explicit chmod is what actually guarantees 0700.
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def require_display() -> None:
    """A headed Chromium needs somewhere to draw; say so in one sentence."""

    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        raise AutomationError("Store cart needs a desktop window; on WSL, enable WSLg")


def classify_launch_error(exc: Exception, *, subject: str = "the dedicated store browser") -> Exception:
    """Turn a Playwright launch failure into the error the user can act on."""

    message = str(exc).lower()
    if "executable doesn't exist" in message:
        return ChromiumMissing("Chromium is required for store carts")
    if "singleton" in message or "user data directory is already in use" in message:
        return AutomationError(f"{subject} profile is already open in another process")
    detail = f"could not start {subject}"
    if sys.platform.startswith("linux"):
        detail += (
            "; install required system libraries with "
            f"'{sys.executable} -m playwright install --with-deps chromium'"
        )
    return AutomationError(detail)


def _cancelled(cancel: Event) -> None:
    if cancel.is_set():
        raise AutomationError("cart operation was cancelled")


def install_chromium(cancel: Event) -> None:
    """Download Playwright's matching Chromium build in the current environment."""

    _cancelled(cancel)
    popen_options = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            **popen_options,
        )
    except OSError as exc:
        raise AutomationError(
            "could not start Chromium installation; run "
            f"'{sys.executable} -m playwright install chromium'"
        ) from exc
    while process.poll() is None:
        if not cancel.wait(0.1):
            continue
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except OSError:
            pass
        _cancelled(cancel)
    _cancelled(cancel)
    if process.returncode:
        raise AutomationError(
            "Chromium installation failed; run "
            f"'{sys.executable} -m playwright install chromium'"
        )


@contextmanager
def sync_browser_context(profile: Path | None = None, *, accept_downloads: bool = False):
    """A headed persistent context for thread-side callers (gates, SoundCloud login)."""

    require_display()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AutomationError(
            "the required Playwright dependency is missing; reinstall dj-soundcloud-digger"
        ) from exc

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            raise ChromiumMissing("Chromium is required for store carts")
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile or store_profile_path()),
                headless=False,
                locale="en-US",
                accept_downloads=accept_downloads,
                chromium_sandbox=True,
            )
        except Exception as exc:
            raise classify_launch_error(exc) from exc
        context.set_default_timeout(ACTION_TIMEOUT_MS)
        try:
            yield context
        finally:
            try:
                context.close()
            except Exception:
                pass


# Chromium releases its profile lock a moment after the previous context
# closed; a relaunch that hits that moment is retried rather than reported.
PROFILE_LOCK_ATTEMPTS = 5
PROFILE_LOCK_WAIT = 1.0


def _profile_locked(exc: Exception) -> bool:
    message = str(exc).lower()
    return "singleton" in message or "user data directory is already in use" in message


async def launch_persistent_context(
    playwright: Any,
    profile: Path | None = None,
    *,
    headless: bool = True,
    accept_downloads: bool = False,
) -> Any:
    """The async twin, for the cart session on Textual's loop.

    Headless by default: the store work happens out of sight, and a window is
    opened separately (see ``launch_viewer``) only when there is something to
    show the user.
    """

    if not headless:
        require_display()
    if not Path(playwright.chromium.executable_path).is_file():
        raise ChromiumMissing("Chromium is required for store carts")
    for attempt in range(1, PROFILE_LOCK_ATTEMPTS + 1):
        try:
            context = await playwright.chromium.launch_persistent_context(
                str(profile or store_profile_path()),
                headless=headless,
                locale="en-US",
                accept_downloads=accept_downloads,
                chromium_sandbox=True,
            )
            break
        except Exception as exc:
            if _profile_locked(exc) and attempt < PROFILE_LOCK_ATTEMPTS:
                LOGGER.debug("Store profile still locked, retrying (%d)", attempt)
                await asyncio.sleep(PROFILE_LOCK_WAIT)
                continue
            raise classify_launch_error(exc) from exc
    context.set_default_timeout(ACTION_TIMEOUT_MS)
    return context


async def launch_viewer(playwright: Any, cookies: list[dict[str, Any]]) -> tuple[Any, Any]:
    """A visible browser carrying the hidden session's cookies.

    The persistent profile can only be open once, and switching it between
    headless and headed raced its lock; a separate browser with the same
    cookies shows the same cart with none of that. Returns (browser, context).
    """

    require_display()
    if not Path(playwright.chromium.executable_path).is_file():
        raise ChromiumMissing("Chromium is required for store carts")
    try:
        browser = await playwright.chromium.launch(headless=False, chromium_sandbox=True)
        context = await browser.new_context(locale="en-US", accept_downloads=False)
        if cookies:
            await context.add_cookies(cookies)
    except Exception as exc:
        raise classify_launch_error(exc, subject="the store browser window") from exc
    context.set_default_timeout(ACTION_TIMEOUT_MS)
    return browser, context

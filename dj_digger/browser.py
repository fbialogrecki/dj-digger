"""Opening links in the user's browser.

There is no ``--browser`` choice baked in any more. The system default is what
gets used unless you pick something else in Settings, and what Settings offers
is whatever this machine turns out to have - including, under WSL, the browser
running on the Windows side, which is the one you actually look at.
"""

import logging
import os
import shutil
import subprocess
import threading
import time
import webbrowser
from collections.abc import Callable, Iterable
from pathlib import Path

from .http import is_openable

# The stored preference meaning "whatever the OS opens links with".
SYSTEM_DEFAULT = ""
# WSL only: hand the link to Windows rather than to anything inside the distro.
WINDOWS = "windows"

# Asked for one at a time and kept only if webbrowser answers. Reading
# ``webbrowser._tryorder`` would be shorter and is what most code does, but it
# is private, it is populated lazily, and its shape has changed across releases.
BROWSER_CANDIDATES = (
    "firefox",
    "chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "safari",
    "opera",
)

# Tried in order under WSL. wslview is the polite one; explorer.exe is on every
# installation; powershell is the fallback when somebody has hidden explorer.
# The powershell entry stops at -Command on purpose: what follows it is a script,
# not an argument list, and _open_on_windows is the one place allowed to build it.
WINDOWS_OPENERS = (
    ["wslview"],
    ["explorer.exe"],
    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"],
)

# Read by the script above, never re-parsed by it. See _open_on_windows.
URL_ENV_VAR = "DJ_DIGGER_URL"

# wslview otherwise runs a `curl --head` against every URL before opening it,
# which on a list of thirty tabs is a visible stall. Set once, at import, rather
# than from inside the loop that opens them - that reached into the environment
# of the whole process on every call.
os.environ.setdefault("WSLVIEW_SKIP_VALIDATION_CHECK", "1")

LOGGER = logging.getLogger(__name__)


def is_wsl() -> bool:
    """Running inside WSL, where the browser lives on the other side."""

    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _windows_opener() -> list[str] | None:
    """The command that hands a URL to Windows, if one of them is installed."""

    for command in WINDOWS_OPENERS:
        if shutil.which(command[0]):
            return command
    return None


def available_browsers() -> list[tuple[str, str]]:
    """``(value, label)`` for everything this machine can actually open a link with."""

    found = [(SYSTEM_DEFAULT, "System default")]
    if is_wsl():
        opener = _windows_opener()
        if opener is not None:
            found.append((WINDOWS, f"Windows default (via {opener[0]})"))
    for name in BROWSER_CANDIDATES:
        try:
            webbrowser.get(name)
        except webbrowser.Error:
            continue
        found.append((name, name.replace("-", " ").title()))
    return found


def resolve_choice(choice: str) -> str:
    """A stored preference, checked against what is really here.

    Never passed to ``webbrowser.get`` unchecked. That function accepts a whole
    command line as well as a browser name - ``webbrowser.get("/bin/sh -c evil
    %s")`` returns something that will run it - so a config file anyone can edit
    is not allowed to name the program we execute. Only a value this machine
    reported gets through.
    """

    if choice in {SYSTEM_DEFAULT, "default", None}:
        return SYSTEM_DEFAULT
    if choice in {value for value, _label in available_browsers()}:
        return choice
    LOGGER.warning(
        "Browser %r is not available here - using the system default instead.", choice
    )
    return SYSTEM_DEFAULT


def _open_on_windows(url: str) -> bool:
    command = _windows_opener()
    if command is None:
        LOGGER.error("No way to reach a Windows browser from here.")
        return False
    # PowerShell parses everything after -Command as code, so the URL cannot be
    # an argument there - and shell=False does not help, because the interpreter
    # is PowerShell itself rather than a shell we declined to invoke.
    # ``https://ok.example/a;$(...)`` is a valid URL, passes is_openable, and is
    # also a valid script; every URL reaching here came from a purchase_url that
    # a stranger set. An environment variable is read by the script and never
    # parsed as part of it. wslview and explorer.exe take a plain argument.
    if command[0].startswith("powershell"):
        argv = [*command, f"Start-Process -FilePath $env:{URL_ENV_VAR}"]
        env = {**os.environ, URL_ENV_VAR: url}
    else:
        argv = [*command, url]
        env = None
    try:
        finished = subprocess.run(
            argv, env=env, capture_output=True, timeout=20, shell=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.error("Could not hand %s to Windows: %s", url, exc)
        return False
    # explorer.exe answers 1 even when it opened the tab, and has done for
    # twenty years. Reaching the end of the call is the only signal it gives.
    if command[0] == "explorer.exe":
        return True
    return finished.returncode == 0


def resolve_controller(choice: str = SYSTEM_DEFAULT) -> webbrowser.BaseBrowser:
    # resolve_choice only lets through a browser this machine reported, so
    # webbrowser.get cannot fail for a reason its own default would survive.
    return webbrowser.get(resolve_choice(choice) or None)


def open_url(url: str, browser: str = SYSTEM_DEFAULT) -> bool:
    if not is_openable(url):
        LOGGER.error("Refusing to open %r - only http and https links are opened.", url)
        return False
    if resolve_choice(browser) == WINDOWS:
        return _open_on_windows(url)
    try:
        resolve_controller(browser).open_new_tab(url)
        return True
    except Exception as exc:  # webbrowser raises a grab bag of platform errors
        LOGGER.error("Failed to open %s: %s", url, exc)
        return False


def open_urls(
    urls: Iterable[str],
    browser: str = SYSTEM_DEFAULT,
    *,
    # Browsers quietly drop tabs opened back-to-back; a short gap between
    # open_url calls lets each one register.
    pause: float = 0.1,
    controller: webbrowser.BaseBrowser | None = None,
    on_success: Callable[[int, str], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> int:
    """Open several links in tabs. Returns how many actually opened.

    ``cancel`` is checked before each tab; what is already open stays open.
    """

    to_windows = controller is None and resolve_choice(browser) == WINDOWS
    if not to_windows:
        controller = controller or resolve_controller(browser)
    opened = 0
    for index, url in enumerate(urls):
        if cancel is not None and cancel.is_set():
            break
        if not is_openable(url):
            err_msg = f"Refused tab #{index + 1}: {url!r} is not an http or https link"
            LOGGER.error("%s", err_msg)
            if on_error:
                on_error(err_msg)
            continue
        try:
            res = _open_on_windows(url) if to_windows else controller.open_new_tab(url)
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

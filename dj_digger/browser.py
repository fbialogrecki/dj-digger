"""Opening links in the user's browser.

There is no ``--browser`` choice baked in any more. The system default is what
gets used unless you pick something else in Settings, and what Settings offers
is whatever this machine turns out to have - including, under WSL, the browser
running on the Windows side, which is the one you actually look at.
"""

import ipaddress
import logging
import os
import shutil
import subprocess
import time
import webbrowser
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlparse

# Every link that reaches this module came from somewhere we do not control: a
# ``purchase_url`` any artist can set, an anchor scraped off a track page, or a
# summary file handed to ``dj-digger open``. Handing the operating system
# anything other than a web address is how that turns into a problem -
# ``file://`` and ``\\host\share`` read local or remote paths (on WSL a UNC path
# is an outbound SMB authentication), ``javascript:`` and ``data:`` execute in
# whatever the platform hands them to. So the scheme is checked, not the host.
SAFE_SCHEMES = frozenset({"http", "https"})

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


def is_fetchable(url: str) -> bool:
    """True when this is an address we are willing to *request*, not just open.

    Opening a link is the user pressing a key; fetching one happens by itself
    during a dig - a link hub is read to see which shops are behind it, and a
    gate resolver posts to it. Those addresses come out of a ``purchase_url``
    that any stranger can set, so one pointed at ``127.0.0.1``, at a box on the
    LAN, or at a cloud metadata service turns a dig into requests issued from
    inside the user's own network.

    ponytail: literal addresses only. A name that resolves to a private address
    gets through, and so does one that resolves differently the second time
    (DNS rebinding). Closing that means resolving here and pinning the address
    into a custom transport adapter - upgrade there if a dig ever runs somewhere
    it does not own the network.
    """

    if not is_openable(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    # RFC 6761 reserves these for the local machine, so they need no lookup.
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        # A bare integer is a legal way to write an address - http://2130706433/
        # is 127.0.0.1 - and ip_address does not accept that spelling on its own.
        address = ipaddress.ip_address(int(host) if host.isdigit() else host)
    except ValueError:
        return True  # a name, not a literal; see the note above
    return address.is_global


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
    resolved = resolve_choice(choice)
    try:
        return webbrowser.get(resolved or None)
    except webbrowser.Error as exc:
        LOGGER.warning(
            "Could not resolve browser '%s' (%s). Falling back to the system default.",
            choice,
            exc,
        )
        return webbrowser.get()


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
    pause: float = 0.1,
    controller: webbrowser.BaseBrowser | None = None,
    on_success: Callable[[int, str], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> int:
    """Open several links in tabs. Returns how many actually opened."""

    to_windows = controller is None and resolve_choice(browser) == WINDOWS
    if not to_windows:
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

"""System clipboard handoff."""

import logging
import subprocess

LOGGER = logging.getLogger(__name__)

# Tried in order. OSC 52 is deliberately absent even though it is the one that
# works over SSH: it copies by writing an escape sequence to stdout, and while
# the crate browser is running stdout belongs to Textual - the sequence would
# land in the middle of a frame and corrupt the screen.
CLIPBOARD_COMMANDS = (
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
    ["pbcopy"],
    # On WSL the clipboard you paste from is Windows', not the Linux one.
    ["clip.exe"],
)


def copy_to_clipboard(text: str) -> bool:
    """Put text on the system clipboard. False when nothing here could.

    It used to return True unconditionally, which meant the caller could not
    tell a copy from a shrug.
    """

    if not text:
        return False
    for command in CLIPBOARD_COMMANDS:
        # clip.exe reads UTF-16LE; everything else wants UTF-8. Sending the
        # wrong one mangles any path that is not pure ASCII.
        encoding = "utf-16-le" if command[0] == "clip.exe" else "utf-8"
        try:
            finished = subprocess.run(
                command, input=text.encode(encoding), capture_output=True, timeout=2
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if finished.returncode == 0:
            return True
    LOGGER.debug("No clipboard tool answered; tried %s", [c[0] for c in CLIPBOARD_COMMANDS])
    return False



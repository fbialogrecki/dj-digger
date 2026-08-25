"""Every key the crate browser binds, and the constants its display is built on.

One source for the bindings, the footer and the help screen, so the three
cannot drift apart.
"""

from ..state import GOT, NEW, OPENED, SKIP

# A mark is one glyph in a one-cell gutter. Spelling "skipped" out cost seven
# columns on every row to say "new" on nearly all of them; the width belongs to
# the track title instead. HelpScreen carries the words.
STATUS_STYLES = {
    NEW: ("\u00b7", "bright_black", "not looked at yet"),
    OPENED: ("\u25cb", "yellow", "link opened, outcome unknown"),
    SKIP: ("\u2717", "bright_black", "skipped"),
    GOT: ("\u2713", "bold green", "got it"),
}

PLAYING_GLYPH = "\u25b6"
OPEN_ALL_CONFIRM_THRESHOLD = 20
# How long before the end of a track we start getting the next one ready. Long
# enough to cover a signed URL, a waveform and the first megabytes of audio on a
# poor connection; short enough that a filter change rarely wastes the work.
PREFETCH_LEAD = 20.0

# Thirty frames a second, which is what a pulse needs to read as one rather than
# as a stutter. It only costs anything while a track is playing: with nothing
# going out, _tick leaves on its first line. Redrawing a waveform this often is
# only affordable because a frame is now a few style ranges - see paint_waveform.
TICK = 1 / 30
# Turning animation off - TEXTUAL_ANIMATIONS=none, which is what you do over a
# slow link - has to turn this off too, or the one thing that repaints the most
# would carry on regardless. The clock and the auto-advance still need a pulse,
# just not thirty of them a second.
CALM_TICK = 0.25
# The spinner is slower than the frame rate on purpose; braille that turns thirty
# times a second is a smear.
SPINNER = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
SPINNER_EVERY = 4
# Long enough to catch the eye, short enough that holding `s` down still works.
FLASH = 0.25
# Number keys select the nth store that this crate actually contains, so `1` is
# always the first store you have rather than a fixed category.
QUICK_FILTER_KEYS = 9

# The footer wants 161 columns to show every binding below and has never had
# them, so Textual clipped the last one mid-word. These are the actions it gives
# up instead, least useful first; `?` still lists all of them.
FOOTER_OPTIONAL = (
    "batch_download",
    "download_track",
    "search('bandcamp')",
    "open_visible",
    "cycle_store(1)",
    "dig_link",
    "open_settings",
)

# Everything except the title gets a fixed budget; the title takes the rest, so
# a wide terminal shows long titles instead of an empty margin.
MARK_WIDTH = 1
INDEX_WIDTH = 4
STORES_WIDTH = 22
GENRE_WIDTH = 14
TIME_WIDTH = 5
# 16, not 20: an 80-column terminal has 17 columns left for the title once the
# fixed ones, their padding and the vertical scrollbar are paid for, so a higher
# floor pushed the table past the screen and hung a horizontal scrollbar under
# it with the last digit of Time behind the edge. It is a floor for terminals
# this narrow only - at 140 columns the title still takes 49.
MIN_TITLE_WIDTH = 16

# These two say nothing as a word - "shop" and "others" are what is left after
# every recognised store, so the domain is the only thing that identifies them.
DOMAIN_BADGE_CATEGORIES = {"shop", "others"}

# Categories whose link goes to a shop page, which is not something a gate
# resolver can unwrap into a file.
DIRECT_STORE_CATEGORIES = frozenset(
    {"beatport", "bandcamp", "traxsource", "junodownload", "apple", "shop", "streaming"}
)

SELECTED = "Selected track"
WHOLE_LIST = "Whole visible list"
CRATES = "Crates"
PLAYBACK = "Playback"
OTHER = "Other"

# One source for the footer and the help screen, so they cannot drift apart:
# (key, action, footer label, section, show in footer, longer help text).
# Footer labels stay short because it gets one line; help has the room to explain.
KEYMAP = [
    ("o,enter", "open_link", "Open", SELECTED, True, "Open its best link, or the filtered store"),
    ("w", "download_track", "Download", SELECTED, True, "Download an artist-provided SoundCloud file"),
    ("W", "batch_download", "Batch download", WHOLE_LIST, True, "Download all free & gate tracks in view"),
    ("ctrl+x", "stop_browser_batch", "Stop browser batch", WHOLE_LIST, False, "Stop the active Chromium gate batch; unfinished tracks stay new"),
    ("b", "search('bandcamp')", "Bandcamp", SELECTED, True, "Search Bandcamp for highlighted track"),
    ("B", "search('beatport')", "Beatport", SELECTED, False, "Search Beatport for highlighted track"),
    ("c", "cart_track", "Cart", SELECTED, False, "Add the exact track to its store cart"),
    ("y", "copy_path", "Copy path", SELECTED, False, "Copy the path of the local file that matches"),
    ("g", "mark_got", "Got", SELECTED, True, "Mark as got, press again to undo"),
    ("s", "mark_skip", "Skip", SELECTED, True, "Mark as skipped, press again to undo"),
    ("u", "mark_new", "Unmark", SELECTED, False, "Clear the mark either way"),
    ("x", "remove_track", "Remove", SELECTED, False, "Remove from this crate, locally only"),
    ("ctrl+z", "undo_remove", "Undo", SELECTED, False, "Put back the last removed track"),
    ("space", "play_pause", "Play", PLAYBACK, True, "Play or pause the highlighted track"),
    ("left_square_bracket", "seek(-1)", "Back", PLAYBACK, False, "Back 10 seconds"),
    ("right_square_bracket", "seek(1)", "Forward", PLAYBACK, False, "Forward 10 seconds"),
    ("n", "play_step(1)", "Next", PLAYBACK, False, "Play the next track in the list"),
    ("p", "play_step(-1)", "Previous", PLAYBACK, False, "Play the previous track"),
    ("minus", "volume(-1)", "Quieter", PLAYBACK, False, "Turn it down"),
    ("equals_sign", "volume(1)", "Louder", PLAYBACK, False, "Turn it up"),
    ("m", "mute", "Mute", PLAYBACK, False, "Mute or unmute"),
    ("a", "open_visible", "Open all", WHOLE_LIST, True, "Open every link shown, asks above 20"),
    ("C", "cart_visible", "Cart all", WHOLE_LIST, False, "Preflight and add every exact store track shown"),
    ("e", "export", "Export", WHOLE_LIST, False, "Write the rows shown to the export file"),
    ("slash", "start_search", "Search", WHOLE_LIST, True, "Filter by artist or title"),
    ("f", "cycle_store(1)", "Next store", WHOLE_LIST, True, "Step to the next store in this crate"),
    ("F", "cycle_store(-1)", "Previous store", WHOLE_LIST, False, "Step back a store"),
    ("0", "filter_index(0)", "Show all", WHOLE_LIST, False, "Drop the store filter, show everything"),
    ("h", "toggle_handled", "Hide handled", WHOLE_LIST, False, "Hide what is got or skipped"),
    ("escape", "clear_filters", "Clear filters", WHOLE_LIST, False, "Clear store, search and hiding"),
    ("d", "dig_link", "Add crate", CRATES, True, "Dig a link into a new crate"),
    ("r", "refresh_crate", "Refresh", CRATES, False, "Re-dig this crate from SoundCloud"),
    ("X", "delete_crate", "Delete", CRATES, False, "Delete this crate, after confirming"),
    ("U", "reset_crate_statuses", "Reset statuses", CRATES, False, "Reset all track statuses to 'new' for this crate"),
    ("ctrl+b", "toggle_sidebar", "Crates", CRATES, False, "Show or hide the crate sidebar"),
    ("question_mark", "help", "Help", OTHER, True, "This screen"),
    ("S", "open_settings", "Settings", OTHER, True, "Configure profile name, email and gate comments"),
    ("q", "quit", "Quit", OTHER, True, "Leave"),
]

# What each group actually operates on. The old footer never said, so it was
# impossible to tell whether a key hit one row or the whole list.
HELP_SCOPES = {
    SELECTED: "acts on the highlighted row only",
    WHOLE_LIST: "acts on every row shown, after filters",
    CRATES: "loads another playlist",
    PLAYBACK: "click the waveform to seek",
    OTHER: "",
}

# Textual's key identifiers are not what anyone wants to read in a help screen.
KEY_DISPLAY = {
    "slash": "/",
    "question_mark": "?",
    "minus": "-",
    "equals_sign": "=",
    "left_square_bracket": "[",
    "right_square_bracket": "]",
    "o,enter": "o, enter",
    "X": "shift+X",
}

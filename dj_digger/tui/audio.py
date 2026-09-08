"""Audio presentation: waveform, meter and transport widgets."""

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static

from ..player import Loaded, PlaybackUnavailable, Player

LOGGER = logging.getLogger(__name__)


# Rows of bottom-anchored blocks, eight levels each. Two of them gave 16 and
# stopped a loud master rendering as a solid rectangle; four give 32 and are
# what the bar spends the row the title used to have on.
BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
WAVEFORM_ROWS = 4
# Loud tracks sit in the top tenth of the range, so the curve has to expand it.
WAVEFORM_GAMMA = 3.0

PLAYED_STYLE = "cyan"
UNPLAYED_STYLE = "bright_black"


def column_levels(samples: list[int], width: int) -> list[float]:
    """One 0..1 level per column, with the loud end of the range expanded.

    Two deliberate choices. Columns average their samples rather than taking the
    peak: at roughly sixteen samples per column the peak almost always hits the
    ceiling, which is most of why this looked like a brick. And the level is
    measured against the track's own maximum rather than stretched between its
    min and max - stretching made a track with no dynamics at all look the most
    dynamic of the lot, because it amplified its noise to full scale. The power
    curve then spreads the top of the range, which is where mastered music sits.
    """

    if width <= 0 or not samples:
        return []

    peak = max(samples)
    if peak <= 0:
        return [0.0] * width

    per_column = len(samples) / width
    levels = []
    for column in range(width):
        start = int(column * per_column)
        end = max(start + 1, int((column + 1) * per_column))
        window = samples[start:end]
        levels.append((sum(window) / len(window) / peak) ** WAVEFORM_GAMMA)
    return levels


def waveform_rows(
    samples: list[int], width: int, rows: int = WAVEFORM_ROWS
) -> list[str]:
    """The block glyphs for a waveform, one string per row.

    These do not change while a track plays, so they are worth building once and
    keeping - only the colours move from frame to frame.
    """

    if width <= 0:
        return []
    if not samples:
        return ["\u2500" * width] * rows

    levels = column_levels(samples, width)
    steps = len(BLOCKS) - 1
    drawn = []
    for row in range(rows):
        # Row 0 is the top of the bar, so it draws the highest slice of the level.
        slice_index = rows - 1 - row
        drawn.append(
            "".join(
                BLOCKS[max(0, min(steps, int(level * steps * rows - slice_index * steps + 0.5)))]
                for level in levels
            )
        )
    return drawn


def paint_waveform(
    rows: list[str],
    played_fraction: float,
    unplayed: str = UNPLAYED_STYLE,
    played: str = PLAYED_STYLE,
) -> Text:
    """Colour prebuilt rows by playback position, independent of audio amplitude.

    A frame costs a handful of style ranges rather than an append per character,
    which is what makes thirty of them a second cheaper than the four this
    managed when every glyph was styled on its own.
    """

    text = Text("\n".join(rows))
    if not rows:
        return text

    width = len(rows[0])
    played_columns = int(width * max(0.0, min(1.0, played_fraction)))
    for index in range(len(rows)):
        start = index * (width + 1)
        if played_columns:
            text.stylize(played, start, start + played_columns)
        text.stylize(unplayed, start + played_columns, start + width)
    return text


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# The bar is the waveform and nothing else, so this is one and the same number.
PLAYER_HEIGHT = WAVEFORM_ROWS
PLAYER_GROW = 0.2


class PlayerBar(Static):
    """The clickable waveform, and whatever the player has to say for itself.

    The title, the clock and the play state used to head this widget on a line
    of their own. They sit in ``PlayerControls`` now, beside the buttons that
    change them, which is a row this has to draw anyway - so the waveform got
    that row instead.
    """

    DEFAULT_CSS = """
    PlayerBar {
        height: 0;
        padding: 0 1;
        overflow: hidden;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, player: Player, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player = player
        self.message = ""
        self.wanted_height = 0
        # Cache glyphs by track, width and peak data. Local peaks arrive after
        # playback starts, so an initially empty shape must be invalidated.
        self._shape: list[str] = []
        self._shape_for = (None, 0)
        self._shape_samples = None

    def refresh_bar(self) -> None:
        self.update(self._content())
        loaded = self.player.loaded is not None
        # A message with nothing loaded - "Loading X", or a dead audio device -
        # is one line of text and does not need the waveform's four rows.
        self._want(PLAYER_HEIGHT if loaded else (1 if self.message else 0))
        # The controls are a sibling widget rather than part of this one, because
        # buttons cannot live inside a Static that repaints thirty times a second.
        for controls in self.screen.query(PlayerControls):
            controls.display = loaded
            if loaded:
                controls.refresh_controls(self.message)

    def _want(self, height: int) -> None:
        """Grow or fold away, rather than blinking in and out of existence."""

        if height == self.wanted_height:
            return
        self.wanted_height = height
        self.styles.animate("height", value=height, duration=PLAYER_GROW)

    def _content(self) -> Text:
        loaded = self.player.loaded
        if loaded is None:
            return Text(self.message, style=self.app.muted)
        palette = self.app.palette
        return paint_waveform(
            self._rows(loaded),
            self.player.fraction,
            unplayed=palette.muted,
            played=palette.accent,
        )

    def _rows(self, loaded: Loaded) -> list[str]:
        width = self._bar_width()
        wanted = (loaded.track.key, width)
        if self._shape_for != wanted or self._shape_samples is not loaded.waveform:
            self._shape = waveform_rows(loaded.waveform, width)
            self._shape_for = wanted
            self._shape_samples = loaded.waveform
        return self._shape

    def _bar_width(self) -> int:
        return max(1, self.size.width - 2)

    def seconds_at(self, x: int) -> float:
        """Turn a click position into a time, for seeking on the waveform."""

        width = self._bar_width()
        fraction = min(1.0, max(0.0, (x - 1) / width)) if width else 0.0
        return fraction * self.player.duration

    def on_click(self, event) -> None:
        if self.player.loaded is None:
            return
        event.stop()
        try:
            self.player.seek(self.seconds_at(event.x))
        except PlaybackUnavailable as exc:
            self.message = str(exc)
        except Exception as exc:  # a bad backend must not take the app down
            LOGGER.exception("Seeking failed")
            self.message = f"Seek failed ({exc})"
        self.refresh_bar()


# Text presentation throughout - no emoji, which every terminal draws in its
# own colour and at its own size. A glyph cannot be made larger than its cell,
# so the buttons read as controls through their chip background and bold weight
# instead. Doubled arrows for the steps: two cells of glyph in a six-cell chip.
PREVIOUS_GLYPH = "\u25c0\u25c0"
PLAY_GLYPH = "\u25b6"
PAUSE_GLYPH = "\u275a\u275a"
NEXT_GLYPH = "\u25b6\u25b6"
CLOSE_GLYPH = "\u2715"

# Twelve cells to aim at. Under about ten a click lands two steps from where you
# meant it, and the whole row still has to fit beside the transport buttons.
VOLUME_TRACK = 12
# Volume glyph and the space after it: where the draggable track starts.
VOLUME_TRACK_START = 2


class VolumeSlider(Static):
    """The speaker and its track. Click or drag anywhere along it to set the volume."""

    DEFAULT_CSS = """
    VolumeSlider {
        width: 22;
        height: 3;
        content-align: left middle;
    }
    """

    def __init__(self, player: Player, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player = player

    def render(self) -> Text:
        volume = self.player.volume
        filled = round(volume * VOLUME_TRACK)
        palette = self.app.palette
        bar = Text("\u00d8 " if volume <= 0 else "\u266a ", style="bold")
        bar.append("━" * filled, style=palette.primary)
        bar.append("●", style=f"bold {palette.primary}")
        muted = palette.muted
        bar.append("─" * (VOLUME_TRACK - filled), style=muted)
        bar.append(f" {int(volume * 100):>3}%", style=muted)
        return bar

    def set_from_x(self, x: int) -> None:
        fraction = (x - VOLUME_TRACK_START) / VOLUME_TRACK
        # Rounded to the step the track can actually draw, so the number beside
        # it does not read 63% on a knob sitting exactly where 60% was.
        self.player.set_volume(round(min(1.0, max(0.0, fraction)) * VOLUME_TRACK) / VOLUME_TRACK)
        self.refresh()

    def on_mouse_down(self, event) -> None:
        event.stop()
        # Captured so the knob keeps following once the pointer leaves the row,
        # which is what tells a slider apart from a row of buttons.
        self.capture_mouse()
        self.set_from_x(event.x)

    def on_mouse_move(self, event) -> None:
        if self.app.mouse_captured is not self:
            return
        if not event.button:
            # The release went missing - a drag that ended off the terminal, say.
            # Left captured, this widget would swallow every click in the app.
            self.release_mouse()
            return
        self.set_from_x(event.x)

    def on_mouse_up(self, event) -> None:
        self.release_mouse()


class PlayerControls(Horizontal):
    """Transport, volume and the way out, under the waveform.

    Every one of these has a key already; the buttons are for the hand that is
    on the mouse anyway, having just clicked the waveform to seek.
    """

    DEFAULT_CSS = """
    PlayerControls {
        display: none;
        height: 3;
        width: 100%;
        padding: 0 1;
    }
    /* One row, no border: a Textual button is three rows tall by default, which
       spent more of the terminal on three glyphs than on the track list.
       By id rather than `PlayerControls Button`, because Textual keys its own
       button borders on a class - which out-specifies a plain type selector, so
       a `border: none` there loses and every button keeps its border row. */
    #player-prev, #player-play, #player-next, #player-close {
        height: 3;
        /* Six against a two-cell glyph: both even, so the icon lands dead
           centre. An odd width either way leaves it half a cell off. */
        width: 6;
        min-width: 6;
        margin: 0 1 0 0;
        border: none;
        /* $boost, not $panel: a translucent lift of whatever is under it, so
           the chip reads in any theme instead of committing to a colour. */
        background: $boost;
        color: $text;
        text-style: bold;
    }
    #player-prev:hover, #player-play:hover, #player-next:hover, #player-close:hover {
        background: $accent;
    }
    /* Takes the slack, and gives the title up an ellipsis at a time rather than
       pushing the clock and the volume off the row. */
    #player-title {
        width: 1fr;
        height: 3;
        content-align: left middle;
        padding: 0 2;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    #player-time {
        width: 14;
        height: 3;
        content-align: right middle;
        padding: 0 2 0 0;
    }
    """

    def __init__(self, player: Player, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player = player
        # What the buttons were last drawn for. A tick repaints the bar thirty
        # times a second and none of that reaches the DOM unless this changes.
        self._shown: tuple | None = None

    def compose(self) -> ComposeResult:
        yield Button(PREVIOUS_GLYPH, id="player-prev", tooltip="Previous track (p)")
        yield Button(PLAY_GLYPH, id="player-play", tooltip="Play or pause (space)")
        yield Button(NEXT_GLYPH, id="player-next", tooltip="Next track (n)")
        yield Static("", id="player-title")
        yield Static("", id="player-time")
        yield VolumeSlider(self.player, id="player-volume")
        yield Button(CLOSE_GLYPH, id="player-close", tooltip="Stop and close the player (ctrl+w)")

    def refresh_controls(self, message: str = "") -> None:
        loaded = self.player.loaded
        if loaded is None:
            return
        # The clock is the only part of this that moves on its own, and it moves
        # once a second - the other twenty-nine ticks have nothing to write.
        state = (
            self.player.playing,
            self.player.volume,
            int(self.player.position),
            loaded.track.key,
            message,
        )
        if state == self._shown:
            return
        self._shown = state
        self.query_one("#player-play", Button).label = (
            PAUSE_GLYPH if self.player.playing else PLAY_GLYPH
        )
        # Text(), not markup: a title like "Rido - Sexy Thing [Clip]" keeps its
        # brackets, and a message is the one thing worth the room over a title.
        self.query_one("#player-title", Static).update(
            Text(message, style=self.app.palette.warning)
            if message
            else Text(loaded.track.label, no_wrap=True, overflow="ellipsis")
        )
        self.query_one("#player-time", Static).update(
            Text(
                f"{format_time(self.player.position)} / {format_time(self.player.duration)}",
                style=self.app.muted,
            )
        )
        self.query_one(VolumeSlider).refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {
            "player-prev": lambda: self.app.action_play_step(-1),
            "player-play": self.app.action_toggle_loaded,
            "player-next": lambda: self.app.action_play_step(1),
            "player-close": self.app.action_close_player,
        }
        action = actions.get(event.button.id or "")
        if action is not None:
            action()

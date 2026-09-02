"""Colours the interface takes from the theme, and the themes corrected to their sources.

Two things made most themes look wrong here. The interface painted its own
cyan, green and yellow straight from the terminal, over whatever the theme
had chosen; and several of Textual's built-in palettes carry values that are
not in the palette they are named after. The first is fixed by ``Palette``:
every colour the rows, legend, waveform and help use is a role of the active
theme. The second by ``CORRECTED_THEMES``, checked against each palette's own
published values (sources in the comments).

Role mapping, following the palettes' own guides where they have one:
primary for store badges, the active filter and selection marks (the blue
family: Nord frost 8, Catppuccin blue, Rosé Pine foam); success for "got" and
free downloads; secondary for "opened" and what a refresh brought in; accent
(the warm one) for the playing marker and the played waveform; warning for a
download in progress; error for the error banner; the muted tone for skipped
rows and secondary text.
"""

from dataclasses import dataclass

from textual.color import Color
from textual.theme import Theme

FALLBACK_MUTED = "bright_black"
# How far the foreground moves toward the background. 0.45 keeps the dim text
# legible on textual-dark while clearly secondary.
MUTED_BLEND = 0.45


@dataclass(frozen=True)
class Palette:
    """Rich style strings for the interface's colour roles."""

    primary: str = "cyan"
    secondary: str = "yellow"
    accent: str = "green"
    success: str = "green"
    warning: str = "yellow"
    error: str = "red"
    muted: str = FALLBACK_MUTED
    background: str = "black"

    @property
    def glow(self) -> tuple[str, ...]:
        """The played waveform just behind the playhead, from quiet to loud.

        Steps within one hue: a colour that changes on every frame reads as
        flicker rather than as a pulse. The first is the ordinary played colour,
        so a silent or paused track looks exactly as it did before any of this.
        """

        try:
            base = Color.parse(self.accent)
        except Exception:
            return (self.accent, f"bold {self.accent}", f"bold {self.accent}", f"bold {self.accent}")
        return (
            self.accent,
            f"bold {base.lighten(0.08).hex}",
            f"bold {base.lighten(0.18).hex}",
            f"bold {base.lighten(0.18).hex}",
        )

    @property
    def download(self) -> str:
        return f"bold {self.background} on {self.warning}"


FALLBACK_PALETTE = Palette()


def _hex(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return Color.parse(value).hex
    except Exception:
        return None


def muted_for(variables: dict[str, str]) -> str:
    """A Rich style string for secondary text, from the app's CSS variables.

    ``App.get_css_variables()`` rather than the Theme object: a theme may leave
    its background unset and let Textual derive it, and the variables carry
    the derived value.
    """

    foreground = variables.get("foreground")
    background = variables.get("background")
    if not foreground or not background:
        return FALLBACK_MUTED
    try:
        return Color.parse(foreground).blend(Color.parse(background), MUTED_BLEND).hex
    except Exception:
        return FALLBACK_MUTED


def palette_for(variables: dict[str, str], theme: Theme | None = None) -> Palette:
    """The interface's colour roles under the active theme.

    Accent roles come from the theme itself when it is given - the CSS
    variables carry them composited with the text alpha, a shade off the
    published value - and the derived background from the variables.
    """

    roles = {
        name: _hex(variables.get(name))
        for name in ("primary", "secondary", "accent", "success", "warning", "error", "background")
    }
    if theme is not None:
        for name in ("primary", "secondary", "accent", "success", "warning", "error"):
            published = _hex(getattr(theme, name, None))
            if published:
                roles[name] = published
    if roles["primary"] is None:
        return FALLBACK_PALETTE
    return Palette(
        primary=roles["primary"],
        secondary=roles["secondary"] or roles["primary"],
        accent=roles["accent"] or roles["warning"] or roles["primary"],
        success=roles["success"] or roles["primary"],
        warning=roles["warning"] or roles["accent"] or roles["primary"],
        error=roles["error"] or "red",
        muted=muted_for(variables),
        background=roles["background"] or "black",
    )


# Built-in themes whose values differ from the palette they are named after,
# rebuilt from the published sources. Names match Textual's so the choice in
# Settings stays the same; registering them replaces the built-in.
CORRECTED_THEMES = (
    # stephango.com/flexoki - dark themes use the 400 shades; Textual shipped
    # the 600 (light-mode) shades on the dark base, hence the muddy look.
    Theme(
        name="flexoki",
        primary="#4385BE",
        secondary="#3AA99F",
        accent="#DA702C",
        foreground="#FFFCF0",
        background="#100F0F",
        surface="#1C1B1A",
        panel="#282726",
        success="#879A39",
        warning="#D0A215",
        error="#D14D41",
        dark=True,
    ),
    # github.com/catppuccin/palette (v1): base #1e1e2e, current green/yellow/red;
    # the style guide puts links and pills on Blue, so primary is blue here.
    Theme(
        name="catppuccin-mocha",
        primary="#89b4fa",
        secondary="#94e2d5",
        accent="#fab387",
        foreground="#cdd6f4",
        background="#1e1e2e",
        surface="#313244",
        panel="#45475a",
        success="#a6e3a1",
        warning="#f9e2af",
        error="#f38ba8",
        dark=True,
    ),
    Theme(
        name="catppuccin-macchiato",
        primary="#8aadf4",
        secondary="#8bd5ca",
        accent="#f5a97f",
        foreground="#cad3f5",
        background="#24273a",
        surface="#363a4f",
        panel="#494d64",
        success="#a6da95",
        warning="#eed49f",
        error="#ed8796",
        dark=True,
    ),
    Theme(
        name="catppuccin-frappe",
        primary="#8caaee",
        secondary="#81c8be",
        accent="#ef9f76",
        foreground="#c6d0f5",
        background="#303446",
        surface="#414559",
        panel="#51576d",
        success="#a6d189",
        warning="#e5c890",
        error="#e78284",
        dark=True,
    ),
    Theme(
        name="catppuccin-latte",
        primary="#1e66f5",
        secondary="#179299",
        accent="#fe640b",
        foreground="#4c4f69",
        background="#eff1f5",
        surface="#e6e9ef",
        panel="#ccd0da",
        success="#40a02b",
        warning="#df8e1d",
        error="#d20f39",
        dark=False,
    ),
    # github.com/morhetz/gruvbox: bright_blue is #83a598 (Textual had a typo)
    # and #a89984 is fg4; aqua as the secondary accent.
    Theme(
        name="gruvbox",
        primary="#83a598",
        secondary="#8ec07c",
        accent="#fe8019",
        foreground="#ebdbb2",
        background="#282828",
        surface="#3c3836",
        panel="#504945",
        success="#b8bb26",
        warning="#fabd2f",
        error="#fb4934",
        dark=True,
    ),
    # github.com/folke/tokyonight.nvim (night): fg #c0caf5, surfaces from the
    # night variant rather than storm's; blue for UI, orange for attention.
    Theme(
        name="tokyo-night",
        primary="#7aa2f7",
        secondary="#7dcfff",
        accent="#ff9e64",
        foreground="#c0caf5",
        background="#1a1b26",
        surface="#16161e",
        panel="#292e42",
        success="#9ece6a",
        warning="#e0af68",
        error="#f7768e",
        dark=True,
    ),
    # draculatheme.com/contribute: the only elevation is Current Line #44475A;
    # links are Cyan, not the Comment colour.
    Theme(
        name="dracula",
        primary="#bd93f9",
        secondary="#8be9fd",
        accent="#ff79c6",
        foreground="#f8f8f2",
        background="#282a36",
        surface="#282a36",
        panel="#44475a",
        success="#50fa7b",
        warning="#ffb86c",
        error="#ff5555",
        dark=True,
    ),
    # atom/one-dark-syntax colors.less: the real hues instead of invented ones.
    Theme(
        name="atom-one-dark",
        primary="#61afef",
        secondary="#56b6c2",
        accent="#d19a66",
        foreground="#abb2bf",
        background="#282c34",
        surface="#21252b",
        panel="#3e4451",
        success="#98c379",
        warning="#e5c07b",
        error="#e06c75",
        dark=True,
    ),
    Theme(
        name="atom-one-light",
        primary="#4078f2",
        secondary="#0184bc",
        accent="#986801",
        foreground="#383a42",
        background="#fafafa",
        surface="#eaeaeb",
        panel="#e5e5e6",
        success="#50a14f",
        warning="#c18401",
        error="#e45649",
        dark=False,
    ),
    # monokai.pro/history classic: elevation #49483e, the yellow #e6db74.
    Theme(
        name="monokai",
        primary="#66d9ef",
        secondary="#ae81ff",
        accent="#fd971f",
        foreground="#f8f8f2",
        background="#272822",
        surface="#272822",
        panel="#49483e",
        success="#a6e22e",
        warning="#e6db74",
        error="#f92672",
        dark=True,
    ),
    # rosepinetheme.com/palette roles: foam = info/links, pine = green, gold =
    # warnings and attention, love = errors, iris = links/hints.
    Theme(
        name="rose-pine",
        primary="#9ccfd8",
        secondary="#c4a7e7",
        accent="#f6c177",
        foreground="#e0def4",
        background="#191724",
        surface="#1f1d2e",
        panel="#26233a",
        success="#31748f",
        warning="#f6c177",
        error="#eb6f92",
        dark=True,
    ),
    Theme(
        name="rose-pine-moon",
        primary="#9ccfd8",
        secondary="#c4a7e7",
        accent="#f6c177",
        foreground="#e0def4",
        background="#232136",
        surface="#2a273f",
        panel="#393552",
        success="#3e8fb0",
        warning="#f6c177",
        error="#eb6f92",
        dark=True,
    ),
    Theme(
        name="rose-pine-dawn",
        primary="#56949f",
        secondary="#907aa9",
        accent="#ea9d34",
        foreground="#575279",
        background="#faf4ed",
        surface="#fffaf3",
        panel="#f2e9e1",
        success="#286983",
        warning="#ea9d34",
        error="#b4637a",
        dark=False,
    ),
    # nordtheme.com: frost 8 is the primary accent, frost 9 secondary UI,
    # aurora 12 (orange) for attention; text is nord6, not the caret tone.
    Theme(
        name="nord",
        primary="#88c0d0",
        secondary="#81a1c1",
        accent="#d08770",
        foreground="#eceff4",
        background="#2e3440",
        surface="#3b4252",
        panel="#434c5e",
        success="#a3be8c",
        warning="#ebcb8b",
        error="#bf616a",
        dark=True,
    ),
    # ethanschoonover.com/solarized: body text is base0 / base00.
    Theme(
        name="solarized-dark",
        primary="#268bd2",
        secondary="#2aa198",
        accent="#cb4b16",
        foreground="#839496",
        background="#002b36",
        surface="#073642",
        panel="#073642",
        success="#859900",
        warning="#b58900",
        error="#dc322f",
        dark=True,
    ),
    Theme(
        name="solarized-light",
        primary="#268bd2",
        secondary="#2aa198",
        accent="#cb4b16",
        foreground="#657b83",
        background="#fdf6e3",
        surface="#eee8d5",
        panel="#eee8d5",
        success="#859900",
        warning="#b58900",
        error="#dc322f",
        dark=False,
    ),
)

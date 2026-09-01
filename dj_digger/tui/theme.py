"""The one dim colour the interface uses, taken from the theme rather than the terminal.

``bright_black`` was hard-coded in twenty places; on a light theme, or on a
terminal palette with a dark grey close to the background, it vanished. The
theme knows its own foreground and background, so the dim tone is a blend of
the two - readable on both, and it changes when the theme does.
"""

from textual.color import Color

FALLBACK_MUTED = "bright_black"
# How far the foreground moves toward the background. 0.45 keeps the dim text
# legible on textual-dark while clearly secondary.
MUTED_BLEND = 0.45


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

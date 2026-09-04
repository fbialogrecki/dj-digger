"""Colours the interface derives from the active Textual theme."""

from dataclasses import dataclass

from textual.color import Color
from textual.theme import Theme

FALLBACK_MUTED = "bright_black"
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
        """The played waveform just behind the playhead, from quiet to loud."""

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
    """Derive secondary text from the active foreground and background."""

    foreground = variables.get("foreground")
    background = variables.get("background")
    if not foreground or not background:
        return FALLBACK_MUTED
    try:
        return Color.parse(foreground).blend(Color.parse(background), MUTED_BLEND).hex
    except Exception:
        return FALLBACK_MUTED


def palette_for(variables: dict[str, str], theme: Theme | None = None) -> Palette:
    """Resolve interface colour roles from the active Textual theme."""

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

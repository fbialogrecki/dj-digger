"""Gate outcomes and inspection data, without HTTP or browser dependencies."""

from dataclasses import dataclass, field
from pathlib import Path


class GateError(RuntimeError):
    """A recognised gate could not be completed safely."""


class GateProfileRequired(GateError):
    """The gate would submit contact data that the user has not configured."""


class GateSocialActionsDisabled(GateError):
    """The user declined the social steps declared by this gate."""


class GateAuthenticationRequired(GateError):
    """A provider-owned OAuth flow must be completed in a browser."""


class GateCaptchaRequired(GateError):
    """The gate presented an interactive anti-bot challenge."""


class GateManualActionRequired(GateError):
    """The gate declared a step whose semantics are not known."""


class GateProtocolChanged(GateError):
    """The provider answered, but no longer speaks the supported protocol."""


class GateRejected(GateError):
    """The provider understood the request but refused to unlock the file."""


class GateDownloadError(GateError):
    """A resolved gate file could not be transferred or validated."""


class GateUnavailable(GateError):
    """The provider or the requested gate is unavailable."""


# Failures the HTTP flow cannot get past, but a person in the private browser
# can: a provider login, a CAPTCHA, an unknown step, a changed protocol, a
# gate that refused the unlock, or social steps the user has disabled here.
BROWSER_REQUIRED_ERRORS = (
    GateAuthenticationRequired,
    GateCaptchaRequired,
    GateManualActionRequired,
    GateProtocolChanged,
    GateRejected,
    GateSocialActionsDisabled,
)


@dataclass(frozen=True)
class HypedditManifest:
    """The short-lived fields used by one desktop Hypeddit download attempt."""

    csrf: str
    file_id: str
    gvt: str
    gvf: str
    wrndk: str
    external_id: str
    steps: tuple[str, ...]
    fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # ``steps_select``: groups of alternatives the page lets the fan choose
    # between, e.g. ("dw",), ("sc",), ("sp","dz"). Empty when the page has none.
    step_groups: tuple[tuple[str, ...], ...] = ()
    sc_comment_required: bool = False
    yt_comment_required: bool = False
    is_skippable: str = "0"
    is_mobile: str = ""
    hypesource: str = ""
    adcode: str = ""


@dataclass(frozen=True)
class HypedditInspection:
    """What one Hypeddit HTML document represents, without executing it."""

    kind: str
    shops: tuple[tuple[str, str], ...] = ()
    nested_gates: tuple[str, ...] = ()
    manifest: HypedditManifest | None = None


@dataclass(frozen=True)
class LinkPageInspection:
    """Shops and gates found behind one purchase link."""

    shops: tuple[tuple[str, str], ...] = ()
    gate_urls: tuple[str, ...] = ()
    keep_original: bool = False
    recognized: bool = False


@dataclass(frozen=True)
class HypedditBrowserBatchResult:
    """Files and typed failures produced by one shared Chromium context."""

    completed: tuple[tuple[str, Path], ...] = ()
    failures: tuple[tuple[str, GateError], ...] = ()
    cancelled: bool = False



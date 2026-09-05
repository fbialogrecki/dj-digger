"""Typed SoundCloud API and transfer failures."""

class SoundCloudError(RuntimeError):
    """Raised when SoundCloud cannot be reached or gives us nothing usable."""


class SoundCloudLoginRequired(SoundCloudError):
    """An artist download requires a SoundCloud account session."""


class SoundCloudTokenRejected(SoundCloudLoginRequired):
    """The saved SoundCloud token expired or was rejected."""


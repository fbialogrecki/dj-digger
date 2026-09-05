"""Managed-browser failures shared by services and store models."""

class AutomationError(RuntimeError):
    """A technical or structural failure which must never trigger store fallback."""


class ChromiumMissing(AutomationError):
    """The Playwright browser required by store carts has not been downloaded."""



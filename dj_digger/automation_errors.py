"""Browser-automation failures shared by services and store models."""

class AutomationError(RuntimeError):
    """A technical or structural failure which must never trigger store fallback."""

"""Browser handoff and completed opening effects, independent of widgets."""

from .. import browser
from ..gates.providers import can_resolve


class OpeningService:
    def __init__(self, state):
        self.state = state

    def can_resolve(self, url):
        return can_resolve(url)

    def open_one(self, url, key, choice):
        opened = browser.open_url(url, choice)
        if opened and key is not None:
            self.state.mark_opened(key)
        return opened

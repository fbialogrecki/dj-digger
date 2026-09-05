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

    def open_many(self, urls, keys, choice, *, on_success, on_error, cancel):
        def finished(index, url):
            self.state.mark_opened(keys[index])
            on_success(index, url)
        return browser.open_urls(urls, choice, on_success=finished, on_error=on_error, cancel=cancel)

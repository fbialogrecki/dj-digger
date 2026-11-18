"""Browser handoff and completed opening effects, independent of widgets."""

import re
from urllib.parse import quote, quote_plus

from .. import browser
from ..gates.providers import can_resolve

SEARCH_PAGES = {
    'Beatport': 'https://www.beatport.com/search?q=',
    'Bandcamp': 'https://bandcamp.com/search?q=',
    'YouTube': 'https://www.youtube.com/results?search_query=',
    'YouTube Music': 'https://music.youtube.com/search?q=',
    'Spotify': 'https://open.spotify.com/search/',
}

_RESERVED = frozenset('discover feed stream you search upload charts settings messages notifications pages signin logout '
                      'people tags terms-of-use pro mobile jobs imprint'.split())
_TABS = frozenset('sets tracks albums popular-tracks reposts likes followers following comments spotlight'.split())


def soundcloud_track_url(value):
    """Canonical public track route, including when a summary supplied the link."""
    if not isinstance(value, str) or len(value) > 512:
        return None
    match = re.fullmatch(r'https://(?:(?:www|m)\.)?soundcloud\.com/([\w-]+)/([\w-]+)/?(?:[?#][^\s]*)?', value, re.ASCII)
    if not match or match[1].lower() in _RESERVED or match[2].lower() in _TABS:
        return None
    return f'https://soundcloud.com/{match[1]}/{match[2]}'


def track_search_url(provider, artist, title):
    query = ' '.join(part.strip() for part in (artist, title) if part and part.strip())
    if not query or provider not in SEARCH_PAGES:
        raise ValueError('Choose a search provider and a track with a title or artist')
    encoded = quote(query, safe='') if provider == 'Spotify' else quote_plus(query)
    return SEARCH_PAGES[provider] + encoded


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

"""Which store a URL belongs to, and its canonical form.

Only canonical HTTPS store domains without credentials on the default port are
accepted; a plain HTTP link is upgraded after the boundary checks pass.
"""

from urllib.parse import ParseResult, urlparse, urlunparse

STORE_HOSTS = {"bandcamp": "bandcamp.com", "beatport": "beatport.com"}


STORE_HOME = {
    "bandcamp": "https://bandcamp.com/",
    "beatport": "https://www.beatport.com/",
}


STORE_LOGIN = {
    "bandcamp": "https://bandcamp.com/login",
}


def _store_origin(url: str, store: str) -> tuple[ParseResult, str, int | None] | None:
    """The parsed URL, its host and port when it sits on the store's own domain.

    None for another domain, for credentials in the URL, or for a URL that
    does not parse; the scheme and port are the caller's to judge.
    """

    base_host = STORE_HOSTS.get(store)
    if base_host is None:
        return None
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if host != base_host and not host.endswith("." + base_host):
        return None
    return parsed, host, port


def is_store_url(url: str, store: str) -> bool:
    """Whether *url* is an HTTPS page owned by the requested store."""

    origin = _store_origin(url, store)
    if origin is None:
        return False
    parsed, _host, port = origin
    return parsed.scheme.lower() == "https" and port in (None, 443)


def canonical_store_url(url: str, store: str) -> str | None:
    """Return a validated HTTPS store URL, upgrading only a plain HTTP origin."""

    value = (url or "").strip()
    if is_store_url(value, store):
        return value
    origin = _store_origin(value, store)
    if origin is None:
        return None
    parsed, host, port = origin
    if parsed.scheme.lower() != "http" or port not in (None, 80):
        return None
    return urlunparse(("https", host, parsed.path or "/", "", parsed.query, ""))


def _beatport_track_id(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def _direct_beatport_track_url(url: str) -> str | None:
    canonical = canonical_store_url(url, "beatport")
    if canonical is None:
        return None
    parsed = urlparse(canonical)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[0] != "track" or not parts[2].isdigit():
        return None
    return urlunparse(("https", parsed.hostname or "", parsed.path, "", "", ""))

"""Which store a URL belongs to, and its canonical form.

Only canonical HTTPS store domains without credentials on the default port are
accepted; a plain HTTP link is upgraded after the boundary checks pass.
"""

from urllib.parse import urlparse, urlunparse

STORE_HOSTS = {"bandcamp": "bandcamp.com", "beatport": "beatport.com"}


STORE_HOME = {
    "bandcamp": "https://bandcamp.com/",
    "beatport": "https://www.beatport.com/",
}


STORE_LOGIN = {
    "bandcamp": "https://bandcamp.com/login",
}


def is_store_url(url: str, store: str) -> bool:
    """Whether *url* is an HTTPS page owned by the requested store."""

    base_host = STORE_HOSTS.get(store)
    if base_host is None:
        return False
    try:
        parsed = urlparse((url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and (host == base_host or host.endswith("." + base_host))
    )


def canonical_store_url(url: str, store: str) -> str | None:
    """Return a validated HTTPS store URL, upgrading only a plain HTTP origin."""

    value = (url or "").strip()
    if is_store_url(value, store):
        return value
    base_host = STORE_HOSTS.get(store)
    if base_host is None:
        return None
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if not (
        parsed.scheme.lower() == "http"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 80)
        and (host == base_host or host.endswith("." + base_host))
    ):
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

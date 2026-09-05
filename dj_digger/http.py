"""URL and redirect policy independent of browser or clipboard handoff."""

import ipaddress
from typing import Any
from urllib.parse import urljoin, urlparse

# Every link that reaches this module came from somewhere we do not control: a
# ``purchase_url`` any artist can set, an anchor scraped off a track page, or a
# summary file handed to ``dj-digger open``. Handing the operating system
# anything other than a web address is how that turns into a problem -
# ``file://`` and ``\\host\share`` read local or remote paths (on WSL a UNC path
# is an outbound SMB authentication), ``javascript:`` and ``data:`` execute in
# whatever the platform hands them to. So the scheme is checked, not the host.
SAFE_SCHEMES = frozenset({"http", "https"})


def is_openable(url: str) -> bool:
    """True when this is a web address we are willing to hand to the OS.

    A host is required as well as the scheme: ``http:///etc/passwd`` parses with
    the right scheme and no host at all.
    """

    try:
        parsed = urlparse((url or "").strip())
    except ValueError:  # malformed IPv6 literals and the like
        return False
    return parsed.scheme.lower() in SAFE_SCHEMES and bool(parsed.netloc)


def is_fetchable(url: str) -> bool:
    """True when this is an address we are willing to *request*, not just open.

    Opening a link is the user pressing a key; fetching one happens by itself
    during a dig - a link hub is read to see which shops are behind it, and a
    gate resolver posts to it. Those addresses come out of a ``purchase_url``
    that any stranger can set, so one pointed at ``127.0.0.1``, at a box on the
    LAN, or at a cloud metadata service turns a dig into requests issued from
    inside the user's own network.

    ponytail: literal addresses only. A name that resolves to a private address
    gets through, and so does one that resolves differently the second time
    (DNS rebinding). Closing that means resolving here and pinning the address
    into a custom transport adapter - upgrade there if a dig ever runs somewhere
    it does not own the network.
    """

    if not is_openable(url):
        return False
    parsed = urlparse(url)
    if parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or "").lower()
    # RFC 6761 reserves these for the local machine, so they need no lookup.
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        # A bare integer is a legal way to write an address - http://2130706433/
        # is 127.0.0.1 - and ip_address does not accept that spelling on its own.
        address = ipaddress.ip_address(int(host) if host.isdigit() else host)
    except ValueError:
        return True  # a name, not a literal; see the note above
    return address.is_global


# One browser identity for every plain HTTP request this program makes. The
# SoundCloud session, the gate resolvers and the token check each used to carry
# their own copy of it, and they had drifted apart.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5


class UnsafeRedirect(ValueError):
    """A redirect chain led somewhere this program will not request."""


def follow_redirects(
    session: Any,
    url: str,
    *,
    timeout: Any,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    stream: bool = False,
) -> tuple[Any, str]:
    """GET ``url`` by hand, validating every redirect target before requesting it.

    ``requests`` would follow the chain itself, but it would also request
    whatever the chain pointed at - an address inside the user's network
    included - and forward ``params`` to every hop. Here ``params`` go with the
    first request only, so a query credential never reaches a redirect target.
    Returns the final response and the URL it came from.
    """

    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if not is_fetchable(current):
            raise UnsafeRedirect("Redirected to an unsafe address")
        response = session.get(
            current,
            headers=headers,
            params=params,
            timeout=timeout,
            stream=stream,
            allow_redirects=False,
        )
        if response.status_code not in REDIRECT_STATUSES:
            return response, current
        location = str(response.headers.get("Location", ""))
        response.close()
        if not location:
            raise UnsafeRedirect("Redirect had no destination")
        current = urljoin(current, location)
        params = None
    raise UnsafeRedirect("Redirect limit exceeded")


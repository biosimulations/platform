"""SSRF guard for the caller-supplied ``archive_url`` on ``POST /compatibility/check``.

That endpoint makes the server fetch a URL chosen by an unauthenticated caller,
which is a server-side request forgery primitive unless the destination is
constrained: without this, ``archive_url=http://169.254.169.254/...`` or
``http://10.0.0.5:8000/...`` would let anyone use the API pod as a proxy into
the cluster and cloud metadata service.
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Cap on redirect hops. Each hop is re-validated, so a public URL cannot bounce
# the fetch onto an internal address.
MAX_REDIRECTS = 5


class BlockedUrlError(ValueError):
    """The URL is not a fetchable public HTTP(S) address."""


def _normalize(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(address)
    # ::ffff:127.0.0.1 is loopback wearing an IPv6 costume -- unwrap before judging.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local          # 169.254.0.0/16 -- cloud metadata
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


async def assert_fetchable_url(url: str) -> None:
    """Raise BlockedUrlError unless ``url`` is a public http(s) address.

    Every DNS answer for the host must be public: a name that resolves to both a
    public and a private address is rejected, not partially allowed.

    This is a pre-flight check, so a hostile DNS server could still rebind
    between this resolution and the one aiohttp performs. Closing that window
    would mean dialing the validated IP directly with a Host header; the checks
    here stop the straightforward attacks (literal internal IPs, localhost,
    metadata endpoints, redirect chains into the cluster).
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise BlockedUrlError(f"Unsupported URL scheme '{parsed.scheme}': only http and https are allowed")
    host = parsed.hostname
    if not host:
        raise BlockedUrlError("URL has no host")

    try:
        port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    except ValueError:
        raise BlockedUrlError("URL has an invalid port")

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedUrlError(f"Could not resolve host '{host}': {e}")

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise BlockedUrlError(f"Could not resolve host '{host}'")

    for address in addresses:
        try:
            ip = _normalize(str(address))
        except ValueError:
            raise BlockedUrlError(f"Host '{host}' resolved to an unusable address")
        if _is_blocked(ip):
            raise BlockedUrlError(
                f"Host '{host}' resolves to a non-public address ({ip}); "
                "internal and loopback destinations are not fetchable"
            )

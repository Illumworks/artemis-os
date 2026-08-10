"""Shared SSRF egress guard for server-side fetches of untrusted URLs.

Server-side code that fetches attacker-influenceable URLs (scout feeds, Argus
research links, the ``pdf_extractor.extract`` tool) must not be usable to reach
internal services: loopback, RFC-1918 private ranges, link-local (cloud
metadata, 169.254.169.254), or other non-global addresses.

Usage:

- ``validate_url(url)`` — raise :class:`EgressBlockedError` if the URL's scheme
  is not http(s) or its host is / resolves to a blocked address.
- ``httpx_egress_hook()`` — an ``httpx`` async ``request`` event hook that runs
  ``validate_url`` on every outgoing request. Because the hook fires per
  request (including each hop when ``follow_redirects=True``), redirects to
  private addresses are blocked too. Wired into
  :class:`artemis.scouts._http.ScoutHttpClient` by default.

Only stdlib (``ipaddress`` + ``socket``) — no new dependencies.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlparse

import httpx

_ALLOWED_SCHEMES = frozenset({"http", "https"})

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class EgressBlockedError(ValueError):
    """Raised when an outbound URL is not safe to fetch server-side."""


def _is_blocked_ip(ip: _IPAddress) -> bool:
    """True when the address must never be reached from a server-side fetch."""
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) so the v4 checks apply.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private  # RFC-1918 (10/8, 172.16/12, 192.168/16) + ULA etc.
        or ip.is_loopback  # 127.0.0.0/8, ::1
        or ip.is_link_local  # 169.254.0.0/16 (cloud metadata), fe80::/10
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified  # 0.0.0.0, ::
    )


def _check_ip(ip: _IPAddress, url: str) -> None:
    if _is_blocked_ip(ip):
        raise EgressBlockedError(f"blocked egress to non-public address {ip} (url={url!r})")


def validate_url(
    url: str,
    *,
    require_https: bool = False,
    _resolver: Callable[..., list[Any]] = socket.getaddrinfo,
) -> None:
    """Raise :class:`EgressBlockedError` unless ``url`` is safe to fetch.

    Checks: scheme is http/https (https only when ``require_https``), a host is
    present, and the host — literal IP or every DNS resolution result — is a
    global (public) address. ``_resolver`` is injectable for tests.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise EgressBlockedError(f"blocked egress: scheme {scheme!r} not allowed (url={url!r})")
    if require_https and scheme != "https":
        raise EgressBlockedError(f"blocked egress: https required (url={url!r})")

    host = parsed.hostname
    if not host:
        raise EgressBlockedError(f"blocked egress: no host in url {url!r}")

    # Literal IP address — no DNS needed.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        _check_ip(literal, url)
        return

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        infos = _resolver(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EgressBlockedError(f"blocked egress: cannot resolve host {host!r}") from exc
    if not infos:
        raise EgressBlockedError(f"blocked egress: host {host!r} resolved to no addresses")

    # Every resolved address must be public — a single private A/AAAA record
    # (DNS-rebinding style) blocks the whole fetch.
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]  # strip IPv6 zone id
        _check_ip(ipaddress.ip_address(addr), url)


async def async_validate_url(url: str, *, require_https: bool = False) -> None:
    """Async wrapper: run the (blocking) DNS resolution off the event loop."""
    await asyncio.to_thread(validate_url, url, require_https=require_https)


def httpx_egress_hook(
    *, require_https: bool = False
) -> Callable[[httpx.Request], Coroutine[Any, Any, None]]:
    """Build an httpx async ``request`` event hook enforcing the egress guard.

    Fires on every outgoing request, including each redirect hop, so redirects
    to private addresses are blocked even when ``follow_redirects=True``.
    """

    async def _hook(request: httpx.Request) -> None:
        await async_validate_url(str(request.url), require_https=require_https)

    return _hook

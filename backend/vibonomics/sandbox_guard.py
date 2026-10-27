"""Refuse requests that reach the app from the sandbox's network.

When the sandbox sidecar is reached over TCP, the app and the sidecar share a network, so model-written
code could call the app's API, and without a password use it. The app works out which of its own
addresses faces the sidecar and refuses every request that arrives on that address. The check uses the
local address a connection arrived on, which no client controls, so forwarded headers cannot get round it.

The guard turns itself off, with a warning, where it could lock people out: when the sidecar is reached
over loopback, or over the same interface as the default route (a sidecar elsewhere on the LAN), because
everyone else's requests arrive there too. With the compose default, a Unix-socket sidecar with no
network at all, there is nothing to guard.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Any
from urllib.parse import urlsplit

from .config import get_config

log = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Documentation addresses, never contacted: connecting a UDP socket only asks the kernel for a route.
_DEFAULT_ROUTE_PROBES = {socket.AF_INET: "192.0.2.1", socket.AF_INET6: "2001:db8::1"}
_RETRY_SECONDS = 5
_RETRIES = 60

_refused: frozenset[IPAddress] = frozenset()
_last_warning = 0.0


def _ip(value: object) -> IPAddress | None:
    try:
        ip = ipaddress.ip_address(str(value).split("%")[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _local_address(family: int, target: str, port: int) -> IPAddress | None:
    """The local address the kernel would use to reach ``target``. Nothing is sent."""
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as probe:
            probe.connect((target, port))
            return _ip(probe.getsockname()[0])
    except OSError:
        return None


def sandbox_facing_addresses(url: str) -> set[IPAddress]:
    """This host's addresses on the sidecar's network, when that network is the sidecar's own.

    Raises ``OSError`` while the sidecar's name does not resolve yet.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return set()  # a Unix socket (or no sidecar): no shared network
    port = parts.port or (443 if parts.scheme == "https" else 80)
    facing: set[IPAddress] = set()
    for family, _type, _proto, _name, sockaddr in socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM):
        if family not in _DEFAULT_ROUTE_PROBES:
            continue
        local = _local_address(family, sockaddr[0], port)
        if local is None:
            continue
        if local.is_loopback:
            log.warning("Sandbox guard off: the sidecar is reached over loopback, where every request arrives")
            continue
        default = _local_address(family, _DEFAULT_ROUTE_PROBES[family], 9)
        if default is None or default == local:
            log.warning(
                "Sandbox guard off: the sidecar at %s is not on a network of its own (%s also carries other traffic)",
                parts.hostname,
                local,
            )
            continue
        facing.add(local)
    return facing


async def refresh() -> bool:
    """Work out what to refuse. Returns False while the sidecar's name does not resolve yet."""
    global _refused
    cfg = get_config()
    if not cfg.sandbox_guard or not cfg.sandbox_url:
        _refused = frozenset()
        return True
    try:
        facing = await asyncio.to_thread(sandbox_facing_addresses, cfg.sandbox_url)
    except OSError:
        return False
    _refused = frozenset(facing)
    if facing:
        log.info("Sandbox guard: refusing requests that arrive on %s", ", ".join(sorted(map(str, facing))))
    return True


async def keep_trying() -> None:
    """For a sidecar that starts after the app: retry until its name resolves."""
    for _ in range(_RETRIES):
        await asyncio.sleep(_RETRY_SECONDS)
        if await refresh():
            return
    log.warning("Sandbox guard off: the sidecar's name never resolved")


def refuses(scope: dict[str, Any]) -> bool:
    """Whether this connection arrived on an address that faces the sidecar."""
    global _last_warning
    if not _refused:
        return False
    server = scope.get("server")
    if not server or _ip(server[0]) not in _refused:
        return False
    if time.monotonic() - _last_warning > 60:
        _last_warning = time.monotonic()
        log.warning("Refused a request from the sandbox network: %s %s", scope.get("method"), scope.get("path"))
    return True

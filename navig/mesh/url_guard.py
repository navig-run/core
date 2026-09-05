"""LAN-only validation for peer ``gateway_url`` values.

The Flux mesh is **LAN-only by design**, and a peer's ``gateway_url`` is an endpoint the
daemon then makes outbound HTTP requests to — ``GET {url}/health`` on every probe cycle, and
``POST {url}/llm/chat`` **carrying the shared ``gateway.mesh_token`` bearer**. So a
``gateway_url`` that points off-LAN is an SSRF sink with a credential attached.

``gateway/routes/mesh.py`` already guarded its *manual* ``/mesh/ping`` bootstrap path. The
**multicast** path did not: :func:`navig.mesh.discovery.MeshDiscovery._parse_packet` stored
``d["gateway_url"]`` verbatim, so a single unauthenticated UDP datagram could register an
off-LAN peer (unauthenticated because the HMAC is only checked when ``mesh.secret`` is
configured, which is not the default). This module is the shared rule both paths use so they
cannot drift apart again.

**Why the multicast path must not resolve hostnames:** ``_handle_packet`` runs on the gateway
event loop, so a hostile packet naming a slow-resolving host would stall it — a trivial DoS.
:func:`is_lan_gateway_url` therefore accepts **IP literals only**. That is safe because a
legitimate node always announces one: ``NodeRegistry.self_record`` builds
``http://{_local_ip()}:{port}`` and ``_local_ip()`` returns ``socket.getsockname()[0]`` or
falls back to ``127.0.0.1``. The manual path keeps its own hostname resolution (a human may
reasonably type a name there) and reuses :func:`ip_is_lan` for the per-address decision.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

__all__ = ["ip_is_lan", "is_lan_gateway_url"]


def ip_is_lan(ip_str: str) -> bool:
    """``True`` if *ip_str* is a private or loopback address that is not link-local.

    Link-local is excluded explicitly: Python counts ``169.254.0.0/16`` as private, and
    ``169.254.169.254`` is the cloud metadata endpoint — the single most valuable SSRF
    target. A non-address string is never LAN.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback) and not ip.is_link_local


def is_lan_gateway_url(url: str) -> bool:
    """``True`` if *url* is an http(s) URL whose host is a LAN **IP literal**.

    Deliberately does NOT resolve hostnames — see the module docstring (event-loop DoS).
    A hostname, a non-http scheme, a malformed URL, or a non-LAN address all return False.
    """
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:  # noqa: BLE001 — a malformed URL is simply not a LAN URL
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return ip_is_lan(parsed.hostname)

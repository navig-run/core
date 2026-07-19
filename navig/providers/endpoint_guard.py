"""
SSRF endpoint guard — block unsafe custom-endpoint destinations by default.

Custom OpenAI-compatible endpoints are user-supplied URLs the server will then
make requests to. Without validation that is a classic SSRF vector — most
dangerously the cloud metadata service (169.254.169.254) and other link-local /
reserved ranges. This guard rejects those by default.

Policy:
  * Always block link-local (incl. 169.254.169.254 metadata), multicast,
    reserved, and unspecified addresses — for every driver.
  * Block loopback + private ranges too unless ``allow_private`` is set (which
    the LOCAL driver passes, since Ollama/LM Studio legitimately bind loopback).
  * Only http/https schemes are allowed.

DNS is resolved best-effort so a public-looking hostname that resolves to an
internal IP is still caught; if resolution fails we do not hard-fail (the
subsequent connection attempt will surface a normal unreachable error).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from navig.providers.connection_types import ConnectionValidationError


def _ip_blocked(
    ip: ipaddress._BaseAddress, *, allow_private: bool, allow_loopback: bool
) -> bool:
    # Loopback FIRST — IPv6 ::1 is also flagged is_reserved, so it must be judged
    # by the loopback rule (permitted by allow_loopback/allow_private) before the
    # blanket reserved/link-local block below.
    if ip.is_loopback:
        return not (allow_private or allow_loopback)
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_link_local:
        return True
    if ip.is_private:
        # LAN ranges (10/8, 172.16/12, 192.168/16) need the broad allow_private —
        # a LOCAL template (Ollama/LM Studio) only needs loopback, so it must NOT
        # be able to probe arbitrary internal hosts (SSRF).
        return not allow_private
    return False


def _candidate_ips(host: str) -> list[ipaddress._BaseAddress]:
    # Literal IP?
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    # Hostname → resolve best-effort (catches DNS pointing at internal IPs).
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001 — unresolvable; let the real request fail later
        return []
    out: list[ipaddress._BaseAddress] = []
    for info in infos:
        addr = info[4][0]
        try:
            out.append(ipaddress.ip_address(addr.split("%")[0]))  # strip zone id
        except ValueError:
            continue
    return out


def assert_safe_endpoint(
    url: str | None, *, allow_private: bool = False, allow_loopback: bool = False
) -> None:
    """Raise :class:`ConnectionValidationError` if *url* is an unsafe destination.

    ``allow_loopback`` permits 127.0.0.1/::1 ONLY (for LOCAL runtimes that bind
    loopback); ``allow_private`` additionally permits LAN/private ranges.
    """
    if not url or not url.strip():
        return
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ConnectionValidationError(
            f"Endpoint must be http(s): got scheme '{parsed.scheme or '∅'}'."
        )
    host = parsed.hostname
    if not host:
        raise ConnectionValidationError("Endpoint has no host.")

    ips = _candidate_ips(host)
    for ip in ips:
        if _ip_blocked(ip, allow_private=allow_private, allow_loopback=allow_loopback):
            reason = (
                "link-local / metadata / reserved address"
                if (ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
                else "private / loopback address"
            )
            raise ConnectionValidationError(
                f"Endpoint '{host}' resolves to a blocked {reason} ({ip}). "
                "Use a public endpoint, or a local-runtime template for loopback."
            )

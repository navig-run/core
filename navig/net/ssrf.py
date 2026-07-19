"""
navig/net/ssrf.py
─────────────────
SSRF (Server-Side Request Forgery) guard for all outbound HTTP in navig.

All outbound HTTP calls — web search, webhook delivery, user-configured URL
callbacks — must go through :func:`safe_fetch` so that a mis-configured or
malicious URL cannot reach internal infrastructure (Docker socket, cloud
metadata endpoints, loopback services).

Usage::

    from navig.net.ssrf import SsrfPolicy, safe_fetch

    policy = SsrfPolicy()               # private networks blocked by default
    response = await safe_fetch("https://api.example.com/data", policy)

Design
------
- :class:`SsrfPolicy` is a frozen dataclass resolved **once** at config load
  time (or constructed inline for call-site flexibility).
- :func:`resolve_host` resolves the URL's hostname to its canonical IPv4/IPv6
  addresses via ``socket.getaddrinfo`` and checks each against the blocked
  ranges.  Validation runs before any request, and :func:`safe_fetch`
  re-validates every redirect hop.  NOTE: the httpx client re-resolves DNS at
  connect time, so this does **not** by itself defend against *active* DNS
  rebinding (a hostile resolver handing a public IP to the validator and a
  private IP to the client a moment later); pinning the connection to the
  validated IP would be required for that, and is a known follow-up.
- :func:`safe_fetch` wraps ``httpx.AsyncClient`` (imported lazily to keep
  startup cost zero when HTTP is not needed) and follows redirects itself so
  each hop must pass the SSRF policy.

Blocked ranges when ``allow_private_network=False`` (the default)
------------------------------------------------------------------
- ``127.0.0.0/8``       — loopback (IPv4)
- ``::1/128``           — loopback (IPv6)
- ``10.0.0.0/8``        — private
- ``172.16.0.0/12``     — private
- ``192.168.0.0/16``    — private
- ``169.254.0.0/16``    — link-local / cloud metadata (AWS/Azure/GCP)
- ``fc00::/7``          — unique local (IPv6 private)
- ``fe80::/10``         — link-local (IPv6)
- ``0.0.0.0/8``         — "this" network
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Union

# ──────────────────────────────────────────────────────────────────────────────
# Blocked networks (single source of truth for all SSRF checks)
# ──────────────────────────────────────────────────────────────────────────────

_BLOCKED_NETS: tuple[Union[IPv4Network, IPv6Network], ...] = (
    # IPv4
    IPv4Network("127.0.0.0/8"),       # loopback
    IPv4Network("0.0.0.0/8"),         # "this" network
    IPv4Network("10.0.0.0/8"),        # private class A
    IPv4Network("172.16.0.0/12"),     # private class B
    IPv4Network("192.168.0.0/16"),    # private class C
    IPv4Network("169.254.0.0/16"),    # link-local / cloud metadata
    IPv4Network("100.64.0.0/10"),     # shared address space (RFC 6598)
    IPv4Network("192.0.2.0/24"),      # TEST-NET-1 (documentation)
    IPv4Network("198.51.100.0/24"),   # TEST-NET-2 (documentation)
    IPv4Network("203.0.113.0/24"),    # TEST-NET-3 (documentation)
    IPv4Network("240.0.0.0/4"),       # reserved
    # IPv6
    IPv6Network("::1/128"),           # loopback
    IPv6Network("fc00::/7"),          # unique local (private)
    IPv6Network("fe80::/10"),         # link-local
    IPv6Network("::ffff:0:0/96"),     # IPv4-mapped addresses
    IPv6Network("::/128"),            # unspecified
)


# ──────────────────────────────────────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────────────────────────────────────


class SsrfBlockedError(OSError):
    """Raised when an outbound request is blocked by the SSRF policy.

    Attributes
    ----------
    url:
        The original URL that was blocked.
    resolved_ip:
        The IP address that triggered the block (if available).
    """

    def __init__(self, url: str, resolved_ip: str = "") -> None:
        self.url = url
        self.resolved_ip = resolved_ip
        detail = f" (resolved to {resolved_ip})" if resolved_ip else ""
        super().__init__(
            f"SSRF policy blocked outbound request to {url!r}{detail}. "
            "If this is an intentional private-network call, set "
            "allow_private_network=True in SsrfPolicy."
        )


@dataclass(frozen=True)
class SsrfPolicy:
    """Immutable SSRF enforcement policy.

    Parameters
    ----------
    allow_private_network:
        When ``True``, requests to private/loopback IP ranges are permitted.
        Default: ``False``.
    allowed_domains:
        Optional allowlist of exact hostnames (no wildcards) that are always
        permitted regardless of resolved IP.  Useful for tightly-controlled
        internal services.  Default: empty tuple (no bypasses).
    """

    allow_private_network: bool = False
    allowed_domains: tuple[str, ...] = ()


# ──────────────────────────────────────────────────────────────────────────────
# Core validation
# ──────────────────────────────────────────────────────────────────────────────


def is_safe_url(url: str, policy: SsrfPolicy | None = None) -> bool:
    """Return ``True`` if the URL passes the SSRF policy, ``False`` otherwise.

    This is a non-raising alternative to :func:`check_url`; useful when you
    want to filter a list of URLs rather than fail fast.
    """
    try:
        check_url(url, policy)
        return True
    except (SsrfBlockedError, ValueError):
        return False


def check_url(url: str, policy: SsrfPolicy | None = None) -> None:
    """Validate *url* against *policy*.  Raises on any violation.

    Parameters
    ----------
    url:
        Absolute URL (must have ``http`` or ``https`` scheme).
    policy:
        SSRF policy to apply.  Defaults to ``SsrfPolicy()`` (private
        networks blocked, no domain allowlist).

    Raises
    ------
    ValueError:
        If the URL is malformed, missing a host, or uses a non-HTTP scheme.
    SsrfBlockedError:
        If the resolved host falls within a blocked IP range (and the domain
        is not in the policy's allowlist, and ``allow_private_network`` is
        ``False``).
    """
    if policy is None:
        policy = SsrfPolicy()

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"SSRF guard only permits http/https URLs; got scheme {parsed.scheme!r}"
        )
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL has no host: {url!r}")

    # Domain allowlist bypass
    if hostname in policy.allowed_domains:
        return

    # Private-network bypass
    if policy.allow_private_network:
        return

    # Resolve and check every returned address
    addresses = resolve_host(hostname)
    for addr_str in addresses:
        ip = _parse_ip(addr_str)
        if ip is not None and _is_blocked(ip):
            raise SsrfBlockedError(url, addr_str)


def resolve_host(hostname: str) -> list[str]:
    """Resolve *hostname* to a list of IP address strings.

    Uses :func:`socket.getaddrinfo` with ``SOCK_STREAM`` to mirror what an
    HTTP library would do.  Returns only the address strings (no port info).

    Raises :class:`socket.gaierror` on DNS failure.
    """
    results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in results]


# ──────────────────────────────────────────────────────────────────────────────
# Optional async fetch wrapper (requires httpx; imported lazily)
# ──────────────────────────────────────────────────────────────────────────────


async def safe_fetch(
    url: str,
    policy: SsrfPolicy | None = None,
    *,
    max_redirects: int = 5,
    **httpx_kwargs,
):
    """Validate *url* and perform an async GET request via ``httpx``.

    Every URL — the initial one AND each redirect hop — is re-validated with
    :func:`check_url` before it is fetched, so an ``http://ok.example`` that
    ``302``s to ``http://169.254.169.254/`` (cloud metadata) is blocked, not
    followed.  Redirects are therefore handled here, not by httpx: a
    ``follow_redirects`` kwarg is ignored, because letting httpx follow would
    jump to the redirect target's IP without re-checking the SSRF policy.

    Parameters
    ----------
    url:
        Absolute URL to fetch.
    policy:
        SSRF policy.  Defaults to ``SsrfPolicy()`` (private blocked).
    max_redirects:
        Maximum number of redirect hops to follow (each re-validated).  A chain
        longer than this raises ``ValueError`` rather than looping forever.
    **httpx_kwargs:
        Forwarded to ``httpx.AsyncClient.get()`` — except ``follow_redirects``,
        which is managed here.

    Returns
    -------
    httpx.Response — the first non-redirect response.

    Raises
    ------
    SsrfBlockedError:
        If the initial URL, or any redirect target, resolves into a blocked
        range (before that hop's network I/O).
    ValueError:
        If a URL is malformed, or the redirect chain exceeds *max_redirects*.
    ImportError:
        If ``httpx`` is not installed.
    """
    check_url(url, policy)
    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "navig.net.ssrf.safe_fetch requires 'httpx'. "
            "Install it with: pip install httpx"
        ) from exc

    # Follow redirects ourselves so every hop is re-checked against the SSRF
    # policy; httpx's own follow_redirects would jump to a blocked IP unchecked.
    httpx_kwargs.pop("follow_redirects", None)
    current = url
    async with httpx.AsyncClient(follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            response = await client.get(current, **httpx_kwargs)
            location = response.headers.get("location")
            if response.is_redirect and location:
                current = str(urllib.parse.urljoin(current, location))
                check_url(current, policy)  # re-validate the redirect target
                continue
            return response
    raise ValueError(
        f"safe_fetch: redirect chain exceeded {max_redirects} hops starting at {url!r}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Config-driven policy
# ──────────────────────────────────────────────────────────────────────────────


def policy_from_config() -> SsrfPolicy:
    """Build the SSRF policy from global config — best-effort, secure by default.

    Reads (all optional):
      - ``net.ssrf.allow_private_network`` — when true, private/loopback targets
        are permitted (e.g. an agent legitimately fetching a local dev server).
        Default ``false`` so a prompt-injected agent cannot reach internal
        services (cloud metadata, the local daemon, private hosts).
      - ``net.ssrf.allowed_domains`` — exact hostnames always permitted.

    The boolean is coerced with :func:`navig.core.coerce.coerce_bool`, so the
    string ``"false"`` that ``navig config set`` stores cannot silently flip the
    guard on (the classic ``bool("false") is True`` trap). Any config-read
    failure falls back to the secure default ``SsrfPolicy()`` — a broken config
    must never *disable* the guard.
    """
    try:
        from navig.config import get_config_manager  # noqa: PLC0415
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        cfg = get_config_manager().get_global_config()
        net = cfg.get("net", {}) if isinstance(cfg, dict) else {}
        ssrf_cfg = net.get("ssrf", {}) if isinstance(net, dict) else {}

        allow_private = coerce_bool(ssrf_cfg.get("allow_private_network"), default=False)
        raw_domains = ssrf_cfg.get("allowed_domains") or []
        domains = (
            tuple(str(d).strip() for d in raw_domains if str(d).strip())
            if isinstance(raw_domains, (list, tuple))
            else ()
        )
        return SsrfPolicy(allow_private_network=allow_private, allowed_domains=domains)
    except Exception:  # noqa: BLE001 — a broken config must never disable the guard
        return SsrfPolicy()


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────


def _parse_ip(addr: str) -> Union[IPv4Address, IPv6Address, None]:
    """Parse *addr* as an IP address; return ``None`` on failure."""
    try:
        return ipaddress.ip_address(addr)
    except ValueError:
        return None


def _is_blocked(ip: Union[IPv4Address, IPv6Address]) -> bool:
    """Return ``True`` if *ip* falls within any of the blocked networks."""
    for net in _BLOCKED_NETS:
        if isinstance(net, IPv4Network) and isinstance(ip, IPv4Address) or isinstance(net, IPv6Network) and isinstance(ip, IPv6Address):
            if ip in net:
                return True
    return False

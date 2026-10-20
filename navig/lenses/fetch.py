"""Fetch a Lens or an ``@require`` file — https only, public hosts only, size-capped.

The deck API is reachable remotely (Lighthouse), so "fetch this URL" from a client is a
server-side request: refuse anything that resolves to a loopback / private / link-local
/ reserved address, cap the body, and time out. Redirects are re-checked hop by hop.

**The address that was checked is the address that is connected to.** Resolving once to
check and letting the HTTP client resolve again to connect is a DNS-rebinding hole: a
hostile resolver answers a public IP to the check and ``127.0.0.1`` to the connect, and
the "public only" rule is bypassed. So each hop resolves exactly once, every answer must
be public, and the socket is opened to that IP while TLS still verifies the certificate
for the hostname (SNI + ``Host`` stay the name). Environment proxies are therefore not
used for these fetches — a proxy would resolve the name itself, out of our sight.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse

MAX_BYTES = 1_000_000
TIMEOUT_S = 15.0
_MAX_REDIRECTS = 5
_REDIRECTS = (301, 302, 303, 307, 308)


class LensFetchError(RuntimeError):
    """The URL could not be fetched safely — the message is user-facing."""


def _resolve_public(host: str, port: int) -> str:
    """Resolve ``host`` once; every answer must be public. Returns the IP to connect to."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise LensFetchError(f"could not resolve {host}: {exc}") from exc
    if not infos:
        raise LensFetchError(f"could not resolve {host}")
    for info in infos:
        addr = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not addr.is_global:
            raise LensFetchError(f"refusing to fetch {host}: it resolves to a non-public address")
    return str(infos[0][4][0])


def _check_url(url: str) -> tuple[urllib.parse.SplitResult, str]:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme.lower() != "https":
        raise LensFetchError(f"only https:// URLs can be fetched (got {url!r})")
    host = parts.hostname
    if not host:
        raise LensFetchError(f"no host in {url!r}")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise LensFetchError(f"bad port in {url!r}") from exc
    return parts, _resolve_public(host, port)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection to a pre-checked IP that still verifies the hostname's cert."""

    def __init__(self, host: str, port: int, ip: str, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _get(parts: urllib.parse.SplitResult, ip: str, *, timeout: float, max_bytes: int):
    target = parts.path or "/"
    if parts.query:
        target += f"?{parts.query}"
    conn = _PinnedHTTPSConnection(parts.hostname or "", parts.port or 443, ip, timeout=timeout)
    try:
        conn.request("GET", target, headers={"User-Agent": "navig-lens"})
        resp = conn.getresponse()
        location = resp.getheader("Location")
        if resp.status in _REDIRECTS and location:
            return resp.status, location, b"", "utf-8"
        body = resp.read(max_bytes + 1)
        return resp.status, None, body, resp.headers.get_content_charset() or "utf-8"
    finally:
        conn.close()


def fetch_text(url: str, *, max_bytes: int = MAX_BYTES, timeout: float = TIMEOUT_S) -> str:
    """GET ``url`` and return its body as text. Raises :class:`LensFetchError`."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        parts, ip = _check_url(current)
        try:
            status, location, body, charset = _get(parts, ip, timeout=timeout, max_bytes=max_bytes)
        except (OSError, http.client.HTTPException) as exc:
            raise LensFetchError(f"could not fetch {current}: {exc}") from exc
        if location is not None:
            current = urllib.parse.urljoin(current, location)
            continue
        if status != 200:
            raise LensFetchError(f"{current} answered HTTP {status}")
        if len(body) > max_bytes:
            raise LensFetchError(f"{current} is larger than {max_bytes // 1000} KB")
        try:
            return body.decode(charset, errors="replace")
        except LookupError:  # an unknown charset label from the server
            return body.decode("utf-8", errors="replace")
    raise LensFetchError(f"too many redirects fetching {url}")

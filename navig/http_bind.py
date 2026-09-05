"""Bind a local HTTP server without tripping over OS-reserved port ranges.

Windows (Hyper-V/WSL/Docker) reserves whole blocks of TCP ports at boot. Binding one
fails with ``WinError 10013`` — "an attempt was made to access a socket in a way
forbidden by its access permissions" — which reads like a firewall or privilege problem
but simply means *that number is spoken for*. The reserved blocks move between reboots
and differ per machine (``netsh int ipv4 show excludedportrange protocol=tcp``), so a
hard-coded default port is a coin flip: several navig defaults landed inside a reserved
block on a real machine and their commands could never start.

So: try the familiar default, and if the OS refuses it, let the OS pick a free port
instead of failing. A port the *user* asked for explicitly is never silently swapped —
that would send them to the wrong place — it raises :class:`PortBindError` instead.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer

__all__ = ["PortBindError", "bind_http_server"]


class PortBindError(RuntimeError):
    """An explicitly requested port could not be bound (in use, reserved, or denied)."""


def bind_http_server(
    handler,
    port: int | None = None,
    *,
    preferred: int = 8099,
    host: str = "127.0.0.1",
    server_cls: type | None = None,
):
    """Return ``(server, bound_port)``.

    ``port=None`` tries *preferred* and falls back to an OS-assigned free port. An
    explicit ``port`` is used as given, and raises :class:`PortBindError` if it fails.

    ``server_cls`` is resolved at call time (not as a default argument) so tests can
    substitute the server class without opening a real socket.
    """
    server_cls = server_cls or ThreadingHTTPServer
    candidates = [port] if port is not None else [preferred, 0]
    last: OSError | None = None
    for candidate in candidates:
        try:
            srv = server_cls((host, candidate), handler)
        except OSError as exc:  # busy, or inside a reserved range (Windows 10013)
            last = exc
            continue
        return srv, srv.server_address[1]

    reason = getattr(last, "strerror", None) or last
    raise PortBindError(
        f"cannot bind {host}:{port} ({reason}). The port is in use or reserved by the "
        f"OS — try another, e.g. --port {(port or 0) + 1}, or omit --port to auto-pick."
    ) from last

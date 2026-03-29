"""
navig-commands/commands/ping.py

TCP/DNS connectivity check — stdlib only, no extra deps.
"""

from __future__ import annotations

import socket
from typing import Any


def handle(args: dict, ctx: Any = None) -> dict:
    """
    Check connectivity to a host.

    args:
      host (str): Hostname or IP to ping.
      port (int, optional): TCP port (default 80). Use 0 for DNS-only.
      timeout (float, optional): Timeout seconds (default 3.0).
    """
    host = args.get("host", "").strip()
    if not host:
        return {"status": "error", "message": "Missing 'host' argument"}
    port = int(args.get("port", 80))
    timeout = float(args.get("timeout", 3.0))

    # DNS resolution
    try:
        resolved = socket.getaddrinfo(
            host, port or 80, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
        ip = resolved[0][4][0]
    except socket.gaierror as exc:
        return {"status": "error", "message": f"DNS failed: {exc}", "host": host}

    if port == 0:
        return {"status": "ok", "data": {"host": host, "resolved": ip, "method": "dns"}}

    # TCP connect
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {
            "status": "ok",
            "data": {
                "host": host,
                "port": port,
                "resolved": ip,
                "reachable": True,
                "method": "tcp",
            },
        }
    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        return {
            "status": "error",
            "message": str(exc),
            "data": {"host": host, "port": port, "resolved": ip, "reachable": False},
        }

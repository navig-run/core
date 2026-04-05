"""Shared client helpers for calling the local NAVIG gateway.

This module lives outside the ``navig.gateway`` *package* intentionally.
Importing from ``navig.gateway`` (the package) triggers its heavy
``__init__.py`` which eagerly imports the full server stack, including
``navig.gateway.server`` → ``navig.agent.proactive.engine`` and many async
subsystems.  CLI commands that only need to issue a single HTTP request to
a running gateway (``gateway session list``, ``browser``, …) must avoid that
import cascade.

Place lightweight gateway HTTP helpers here; keep this file free of any
``navig.gateway.*`` imports.
"""

from __future__ import annotations


def gateway_cli_defaults() -> tuple[int, str]:
    """Return gateway port/host from config with stable CLI fallbacks."""
    try:
        from navig.config import get_config_manager

        raw = get_config_manager()._load_global_config()
    except Exception:
        raw = {}

    gw = raw.get("gateway") or {}
    port = int(gw.get("port") or 8789)
    host = str(gw.get("host") or "127.0.0.1")
    return port, host


def gateway_base_url() -> str:
    """Return the gateway base URL.

    Uses the configured host (default ``127.0.0.1``) rather than the
    ``localhost`` hostname.  On Windows 11, ``localhost`` resolves to
    ``::1`` (IPv6) first, which causes a multi-second delay before
    falling back to ``127.0.0.1`` when the listener only binds IPv4.
    Explicitly using the numeric address avoids this dual-stack delay.
    """
    port, host = gateway_cli_defaults()
    return f"http://{host}:{port}"


def gateway_request_headers() -> dict[str, str]:
    """Return auth headers for gateway admin requests when configured."""
    try:
        from navig.config import get_config_manager

        raw = get_config_manager()._load_global_config()
    except Exception:
        raw = {}

    gw = raw.get("gateway") or {}
    auth = gw.get("auth") or {}
    token = auth.get("token") or gw.get("auth_token") or gw.get("token")

    headers = {"X-Actor": "navig-cli"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def gateway_request(method: str, path: str, **kwargs):
    """Send an authenticated request to the local gateway."""
    import requests

    headers = dict(gateway_request_headers())
    extra_headers = kwargs.pop("headers", None) or {}
    headers.update(extra_headers)
    return requests.request(method, f"{gateway_base_url()}{path}", headers=headers, **kwargs)

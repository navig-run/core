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

# Zero-dependency leaf import — keeps this module free of the heavy
# ``navig.gateway.*`` import cascade (see module docstring).
from navig._daemon_defaults import _GATEWAY_PORT


def gateway_cli_defaults() -> tuple[int, str]:
    """Return gateway port/host from config with stable CLI fallbacks."""
    try:
        from navig.config import get_config_manager

        raw = get_config_manager()._load_global_config()
    except Exception:
        raw = {}

    gw = raw.get("gateway") or {}
    try:
        port = int(gw.get("port") or _GATEWAY_PORT)
    except (ValueError, TypeError):
        port = _GATEWAY_PORT
    host = str(gw.get("host") or "127.0.0.1")
    return port, host


def read_gateway_discovery() -> tuple[int, str] | None:
    """Read ``~/.navig/gateway.json`` — written by the live gateway on its
    SELF-HEALING BIND (``gateway/server.py``) recording the port it actually
    bound. Returns ``(port, host)``, or ``None`` when the file is missing or
    invalid (daemon never started, or an older core)."""
    try:
        import json

        from navig.platform.paths import config_dir

        raw = json.loads((config_dir() / "gateway.json").read_text(encoding="utf-8"))
        port = int(raw.get("port") or 0)
        host = str(raw.get("host") or "127.0.0.1")
        if 0 < port < 65536:
            return port, host
    except Exception:
        pass
    return None


def gateway_live_defaults(probe_timeout: float = 0.25) -> tuple[int, str]:
    """Where is the gateway ACTUALLY listening? (client-side resolver)

    The self-healing bind can land the gateway off the configured port —
    on Windows, WinNAT/Hyper-V reserves whole dynamic ranges that swallow
    8789 and its neighbours — in which case the live endpoint is recorded to
    ``~/.navig/gateway.json``. Prefer that discovery when its endpoint
    currently accepts connections; otherwise fall back to config defaults.

    Never use this to choose a BIND port — config stays canonical for the
    server side (``commands/gateway.py``), or a restarting daemon would chase
    its own discovery file.
    """
    disc = read_gateway_discovery()
    if disc is not None:
        import socket

        port, host = disc
        try:
            with socket.create_connection((host, port), timeout=probe_timeout):
                return port, host
        except OSError:
            pass  # stale discovery (daemon down or moved) — use config
    return gateway_cli_defaults()


def gateway_base_url() -> str:
    """Return the base URL of the LIVE gateway (client-side).

    Resolves through :func:`gateway_live_defaults`, so callers follow the
    gateway wherever its self-healing bind actually landed it — not a
    configured port nothing listens on.

    Uses the configured host (default ``127.0.0.1``) rather than the
    ``localhost`` hostname.  On Windows 11, ``localhost`` resolves to
    ``::1`` (IPv6) first, which causes a multi-second delay before
    falling back to ``127.0.0.1`` when the listener only binds IPv4.
    Explicitly using the numeric address avoids this dual-stack delay.
    """
    port, host = gateway_live_defaults()
    # A bind-any host is not a connectable client target — reach it on loopback.
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def gateway_request_headers() -> dict[str, str]:
    """Return auth headers for gateway admin requests when configured."""
    try:
        from navig.config import get_config_manager

        # MUST NOT be `_load_global_config()`. That returns the PYDANTIC-VALIDATED
        # view (`validate_global_config(...).model_dump()`), which keeps only fields
        # the schema declares -- and the schema does not declare `gateway.auth`.
        # Measured on the operator's machine: config.yaml held
        # `gateway: {auth: {token: ...}, mesh_token: ...}` while
        # `_load_global_config()` reported
        # `gateway: {allowed_origins, enabled, host, port, require_auth}` -- every
        # real key replaced by schema defaults. So this helper could never find a
        # token and every CLI -> gateway admin request went out unauthenticated;
        # all seven `navig cron` commands answered 401. The gateway's own 401 body
        # says "(The NAVIG CLI does this for you.)" -- it did not.
        #
        # `get_global_config()` preserves undeclared keys, which is what a
        # credential read requires. `gateway_cli_defaults` above may keep using the
        # validated view: port/host ARE schema-declared.
        raw = get_config_manager().get_global_config() or {}
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


def unwrap_envelope(body: object) -> object:
    """The payload inside a gateway ``json_ok`` response.

    Gateway routes answer with an envelope — ``{"ok": …, "data": <payload>, "error": …}``
    (``routes/common.envelope_ok``) — so a CLI that reads a field straight off the decoded
    body always misses: the field lives one level down. That mistake silently emptied the
    whole ``navig flux`` surface (#713) and the whole ``navig cron`` surface, because the
    miss looks exactly like "the daemon has nothing to report".

    Recognised ONLY by the full three-key signature, so a payload that merely happens to
    carry an ``ok`` key is never unwrapped by accident. Anything that isn't an envelope —
    a bare list, an already-unwrapped payload, a route that returns a raw ``web.Response``
    — is returned unchanged, so this is safe to apply at any call site. A ``data`` of
    ``None`` becomes ``{}`` so display code can keep chaining ``.get()``.
    """
    if (
        isinstance(body, dict)
        and "ok" in body
        and "data" in body
        and "error" in body
    ):
        payload = body["data"]
        return {} if payload is None else payload
    return body

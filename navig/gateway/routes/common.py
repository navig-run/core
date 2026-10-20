"""Shared helpers for gateway route modules."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web


def _get_web():
    """Return the aiohttp.web module, raising RuntimeError when unavailable."""
    try:
        from aiohttp import web as _web  # noqa: PLC0415

        return _web
    except ImportError as exc:
        raise RuntimeError("aiohttp is required for gateway routes (pip install aiohttp)") from exc


def envelope_ok(data: Any | None = None) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def envelope_error(
    message: str,
    *,
    code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "data": None,
        "error": message,
        "error_code": code,
    }
    if details:
        payload["details"] = details
    return payload


def json_ok(data: Any | None = None, *, status: int = 200) -> web.Response:
    return _get_web().json_response(envelope_ok(data), status=status)


def json_error_response(
    message: str,
    *,
    status: int,
    code: str,
    details: dict[str, Any] | None = None,
) -> web.Response:
    return _get_web().json_response(
        envelope_error(message, code=code, details=details), status=status
    )


def gateway_mesh_token(gateway: Any) -> str | None:
    """The fleet-shared peer secret this install accepts from LAN mesh peers.

    Read at request time from the gateway's config manager: `_ensure_mesh_token`
    mints AFTER `GatewayConfig` is constructed and swaps the `gateway:` dict, so
    `config.raw_gateway_cfg` can be stale for a first-boot mint. The raw section
    is the fallback for gateway objects that carry only that (stubs/tests).
    Non-string / empty values are treated as "absent" — never guessed at.
    """
    cm = getattr(gateway, "config_manager", None)
    raw = getattr(cm, "global_config", None)
    if isinstance(raw, dict):
        section = raw.get("gateway", {})
        if isinstance(section, dict):
            tok = section.get("mesh_token")
            if isinstance(tok, str) and tok:
                return tok
    raw_cfg = getattr(getattr(gateway, "config", None), "raw_gateway_cfg", None)
    if isinstance(raw_cfg, dict):
        tok = raw_cfg.get("mesh_token")
        if isinstance(tok, str) and tok:
            return tok
    return None


def require_bearer_auth(
    request: web.Request,
    gateway: Any,
    allow_anonymous: bool = False,
    extra_tokens: tuple[str | None, ...] = (),
) -> web.Response | None:
    """Return 401/503 response when no accepted credential is presented.

    Args:
        allow_anonymous: If True, permit unauthenticated requests when no token
            is configured (used for local-only endpoints like the Rust bridge).
        extra_tokens: Additional accepted bearer secrets, each compared in
            constant time alongside the admin token. Exactly TWO routes pass
            the fleet-shared ``gateway.mesh_token`` here: the peer-forward
            target (`POST /llm/chat`) and `POST /mesh/ping` (the join flow's
            authenticated ping-back). Every other guarded route leaves this
            empty — the mesh secret must never unlock an admin-only endpoint. ``None``/empty entries are ignored;
            if NO credential is configured at all (no admin token and no
            extras) the request still fails closed with 503, exactly as
            before (Gate 0).
    """
    token = getattr(getattr(gateway, "config", None), "auth_token", None)
    accepted: list[str] = []
    if token:
        accepted.append(str(token))
    for extra in extra_tokens:
        if isinstance(extra, str) and extra:
            accepted.append(extra)

    if not accepted:
        if allow_anonymous:
            return None  # local-only endpoints (e.g. the Rust bridge) opt in explicitly
        # The gateway mints and persists a token at startup (`server._ensure_auth_token`),
        # so a missing token is a FAILED MINT, not a first-boot choice. Fail closed with
        # an actionable error: previously this returned None, opening every guarded route
        # to anonymous callers whenever the mint broke.
        return json_error_response(
            "The gateway has no auth token — refusing to serve authenticated routes. "
            "Restart the gateway (`navig gateway restart`) and check `navig doctor` "
            "for the failed token mint.",
            status=503,
            code="auth_not_configured",
        )

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        if allow_anonymous:
            return None  # Allow through without token if endpoint permits it
        return json_error_response(
            "Missing bearer token. The gateway's token is in your config — read it with "
            "`navig config get gateway.auth.token` and send it as "
            "`Authorization: Bearer <token>`. (The NAVIG CLI does this for you.)",
            status=401,
            code="unauthorized",
        )

    provided = header[len("Bearer ") :].strip()
    if not provided:
        return json_error_response("Invalid bearer token", status=401, code="unauthorized")
    for candidate in accepted:
        if hmac.compare_digest(provided, candidate):
            return None
    return json_error_response("Invalid bearer token", status=401, code="unauthorized")

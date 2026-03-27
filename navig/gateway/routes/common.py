"""Shared helpers for gateway route modules."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from aiohttp import web


def _get_web():
    """Return the aiohttp.web module, raising RuntimeError when unavailable."""
    try:
        from aiohttp import web as _web  # noqa: PLC0415

        return _web
    except ImportError as exc:
        raise RuntimeError(
            "aiohttp is required for gateway routes (pip install aiohttp)"
        ) from exc


def envelope_ok(data: Optional[Any] = None) -> Dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def envelope_error(
    message: str,
    *,
    code: str,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ok": False,
        "data": None,
        "error": message,
        "error_code": code,
    }
    if details:
        payload["details"] = details
    return payload


def json_ok(data: Optional[Any] = None, *, status: int = 200) -> "web.Response":
    return _get_web().json_response(envelope_ok(data), status=status)


def json_error_response(
    message: str,
    *,
    status: int,
    code: str,
    details: Optional[Dict[str, Any]] = None,
) -> "web.Response":
    return _get_web().json_response(
        envelope_error(message, code=code, details=details), status=status
    )


def require_bearer_auth(
    request: "web.Request",
    gateway: Any,
    allow_anonymous: bool = False,
) -> "Optional[web.Response]":
    """Return 401 response when gateway auth token is configured but not provided/valid.

    Args:
        allow_anonymous: If True, permit unauthenticated requests when no token
            is configured (used for local-only endpoints like the Rust bridge).
    """
    token = getattr(getattr(gateway, "config", None), "auth_token", None)
    if not token:
        return None  # No token configured → open access

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        if allow_anonymous:
            return None  # Allow through without token if endpoint permits it
        return json_error_response(
            "Missing bearer token", status=401, code="unauthorized"
        )

    provided = header[len("Bearer ") :].strip()
    if not provided or not hmac.compare_digest(provided, str(token)):
        return json_error_response(
            "Invalid bearer token", status=401, code="unauthorized"
        )

    return None

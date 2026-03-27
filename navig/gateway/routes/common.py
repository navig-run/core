"""Shared helpers for gateway route modules."""

from __future__ import annotations

import hmac
from typing import Any, Dict, Optional

from aiohttp import web


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


def json_ok(data: Optional[Any] = None, *, status: int = 200) -> web.Response:
    return web.json_response(envelope_ok(data), status=status)


def json_error_response(
    message: str,
    *,
    status: int,
    code: str,
    details: Optional[Dict[str, Any]] = None,
) -> web.Response:
    return web.json_response(
        envelope_error(message, code=code, details=details), status=status
    )


def require_bearer_auth(request: web.Request, gateway: Any) -> Optional[web.Response]:
    """Return 401 response when gateway auth token is configured but not provided/valid."""
    token = getattr(getattr(gateway, "config", None), "auth_token", None)
    if not token:
        return None

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return json_error_response(
            "Missing bearer token", status=401, code="unauthorized"
        )

    provided = header[len("Bearer ") :].strip()
    if not provided or not hmac.compare_digest(provided, str(token)):
        return json_error_response(
            "Invalid bearer token", status=401, code="unauthorized"
        )

    return None

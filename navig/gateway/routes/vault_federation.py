"""Vault authority HTTP surface: POST /api/vault/v1/{enroll,resolve,list,status,sync}.

Off unless ``vault.authority.enabled`` is true — then this install answers
enrolled devices (see ``navig_vault.federation.authority``). Disabled, every
path is a plain 404, indistinguishable from a route that does not exist.

Self-authenticating, like ``/api/ingest``: only ``/api/deck/*`` sits behind the
deck middleware, and a device must never hold a deck/operator credential. Each
request is HMAC-signed by its device key; secret values come back sealed to it.

A request that arrived through Lighthouse (stamped ``X-Navig-Via`` by the
uplink) or any Cloudflare hop is marked RELAYED: it may not enroll and may not
ask for an unsealed value.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.gateway.server import NavigGateway  # noqa: F401

try:
    from aiohttp import web
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("aiohttp is required for gateway routes") from exc

logger = logging.getLogger(__name__)

PATHS = ("enroll", "resolve", "list", "status", "sync")
_MAX_BODY = 64 * 1024


def register(app: "web.Application", gateway: "NavigGateway") -> None:
    for name in PATHS:
        app.router.add_post(f"/api/vault/v1/{name}", _handler)


def _enabled() -> bool:
    try:
        from navig.config import ConfigManager  # noqa: PLC0415 — fresh read, never the singleton
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        return coerce_bool(ConfigManager().get("vault.authority.enabled"), default=False)
    except Exception:  # noqa: BLE001 — unreadable config fails CLOSED: no authority
        logger.warning("vault authority: config unreadable; serving nothing", exc_info=True)
        return False


def _relayed(request: "web.Request") -> bool:
    from navig.gateway.deck.auth import RELAY_VIA_HEADER  # noqa: PLC0415

    return bool(request.headers.get(RELAY_VIA_HEADER) or request.headers.get("CF-Ray"))


_AUTHORITY = None


def _authority():
    global _AUTHORITY
    if _AUTHORITY is None:
        from navig_vault.federation.authority import Authority  # noqa: PLC0415

        from navig.vault.core import get_vault  # noqa: PLC0415

        _AUTHORITY = Authority(get_vault())
    return _AUTHORITY


async def _handler(request: "web.Request") -> "web.Response":
    if not _enabled():
        raise web.HTTPNotFound()
    if request.content_length and request.content_length > _MAX_BODY:
        return web.json_response({"error": "payload_too_large"}, status=413)
    body = await request.read()
    if len(body) > _MAX_BODY:
        return web.json_response({"error": "payload_too_large"}, status=413)
    headers = dict(request.headers)
    relayed = _relayed(request)
    # Rate-limit key only: behind Lighthouse every request comes from 127.0.0.1, so the
    # forwarded client address (when present) is the meaningful one.
    from navig.gateway.deck.auth import _forwarded_client_ip  # noqa: PLC0415

    source = _forwarded_client_ip(request) or (request.remote or "-")
    try:
        status, payload = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _authority().handle(
                "POST", request.path, headers, body, relayed=relayed, source=source
            ),
        )
    except Exception:  # noqa: BLE001 — never leak a traceback (or a value) to a device
        logger.exception("vault authority request failed (%s)", request.path)
        return web.json_response({"error": "authority_error"}, status=500)
    return web.json_response(payload, status=status)

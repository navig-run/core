"""Deck API: who owns this Brain, and the three things an operator may do about it.

Small on purpose — the NAVIG OS Brain card and any script need the same four facts
(owner, role, lease, delivery mode) and two actions (claim, release/takeover), and
nothing else belongs here.

Envelope matches the rest of the deck API: ``{"ok": true, "data": …}`` /
``{"ok": false, "error": …}``. Ownership refusals come back as HTTP 409 with the
same sentence the CLI prints, so the UI never has to invent its own wording.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover — aiohttp is a hard dep for the gateway
    web = None  # type: ignore[assignment]

from navig.brain import authority as _authority
from navig.brain import lease as _lease
from navig.brain import roles as _roles
from navig.brain import telegram_mode as _tg
from navig.brain.identity import hostname
from navig.brain.identity import owner_id as _owner_id

logger = logging.getLogger(__name__)

__all__ = [
    "brain_payload",
    "handle_deck_brain_claim",
    "handle_deck_brain_release",
    "handle_deck_brain_status",
    "handle_deck_brain_takeover",
    "register_brain_routes",
]


def _ok(data: object) -> "web.Response":
    return web.json_response({"ok": True, "data": data})


def _err(message: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": message}, status=status)


def brain_payload() -> dict[str, Any]:
    """The one status document — the CLI's ``--json`` body, reused verbatim."""
    status = _authority.lease_store().status()
    telegram = _tg.TelegramModeStore().status()
    role = str((status.get("you") or {}).get("role") or _lease.ROLE_ALL_IN_ONE)
    return {
        "owner_id": _owner_id(),
        "hostname": hostname(),
        "state": status.get("state"),
        "epoch": status.get("epoch"),
        "owner": status.get("owner"),
        "you": status.get("you"),
        "role": role,
        "lease": status.get("lease"),
        "can_claim": status.get("can_claim"),
        "can_takeover": status.get("can_takeover"),
        "summary": status.get("summary"),
        # Where the record came from: the shared authority, or this machine's local
        # record (single-machine install). A UI that cannot show this cannot tell the
        # operator which guarantee they actually have.
        "authority": status.get("authority"),
        "telegram": {
            "mode": telegram.get("mode"),
            "owner_id": telegram.get("owner_id"),
            "epoch": telegram.get("epoch"),
            "updated_at": telegram.get("updated_at"),
        },
        # The warning the UI shows instead of inventing one: a Brain on a workstation
        # stops answering when that workstation is switched off.
        "host_dependency": _roles.host_dependency_warning(role),
        "always_on_host": _roles.detect_install_context().get("always_on_host"),
    }


async def handle_deck_brain_status(request: "web.Request") -> "web.Response":
    """``GET /api/deck/brain`` — owner, role, lease and delivery state."""
    try:
        return _ok(brain_payload())
    except Exception as exc:  # noqa: BLE001 — a status read must not 500 the UI
        logger.warning("deck brain status failed: %s", exc)
        return _err(f"could not read Brain ownership: {exc}", status=503)


async def _read_json_body(request: "web.Request") -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — an empty body is a valid "use the defaults" post
        return {}
    return body if isinstance(body, dict) else {}


async def handle_deck_brain_claim(request: "web.Request") -> "web.Response":
    """``POST /api/deck/brain/claim`` — make the machine running the gateway the Brain."""
    body = await _read_json_body(request)
    try:
        lease = _authority.lease_store().acquire(
            role=_lease.normalize_role(body["role"]) if body.get("role") else None,
            reason=str(body.get("reason") or "claimed from NAVIG OS"),
        )
    except _lease.BrainOwnershipError as exc:
        # 409: the request was well-formed, the WORKSPACE says no. Same wording as the CLI.
        return _err(str(exc), status=409)
    return _ok({"lease": lease.to_dict()})


async def handle_deck_brain_release(request: "web.Request") -> "web.Response":
    """``POST /api/deck/brain/release`` — give the Brain up so another machine may claim it."""
    body = await _read_json_body(request)
    try:
        lease = _authority.lease_store().release(
            reason=str(body.get("reason") or "released from NAVIG OS")
        )
    except _lease.BrainOwnershipError as exc:
        return _err(str(exc), status=409)
    return _ok({"lease": lease.to_dict()})


async def handle_deck_brain_takeover(request: "web.Request") -> "web.Response":
    """``POST /api/deck/brain/takeover`` — explicit, audited displacement.

    Requires ``reason``; displacing a lease that is still heartbeating additionally
    requires ``force`` and ``from_owner`` so a stale page cannot move a live Brain by
    accident.
    """
    body = await _read_json_body(request)
    try:
        lease = _authority.lease_store().takeover(
            role=_lease.normalize_role(body["role"]) if body.get("role") else None,
            reason=str(body.get("reason") or ""),
            from_owner=str(body.get("from_owner") or "") or None,
            force=bool(body.get("force")),
        )
    except _lease.BrainOwnershipError as exc:
        return _err(str(exc), status=409)
    return _ok({"lease": lease.to_dict()})


def register_brain_routes(app: "web.Application") -> None:
    """Mount the Brain ownership endpoints (called from ``register_deck_routes``)."""
    app.router.add_get("/api/deck/brain", handle_deck_brain_status)
    app.router.add_post("/api/deck/brain/claim", handle_deck_brain_claim)
    app.router.add_post("/api/deck/brain/release", handle_deck_brain_release)
    app.router.add_post("/api/deck/brain/takeover", handle_deck_brain_takeover)

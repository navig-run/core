"""NAVIG Dock bridge — the Dock's side of browser control for agents.

Routes (local-only; the deck auth middleware has already applied the desktop bypass,
which for extension origins now means "the paired Dock" — see ``navig.dock.pairing``):

    POST /api/deck/dock/pair     {name, version}  → {token, extension_id}   (trust on first use)
    GET  /api/deck/dock/bridge   WebSocket: hello{token} → welcome → call/result/cancel

The agent side (``/dock/status``, ``/dock/call``) lives in ``gateway/routes/dock.py`` and
needs the gateway's own bearer token, never an extension origin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

try:
    from aiohttp import WSMsgType, web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_PING_EVERY_S = 15.0


def _local_extension(request: "web.Request") -> str | None:
    """The calling extension's id when this is a genuinely-local extension request."""
    from navig.dock.pairing import extension_id_from_origin  # noqa: PLC0415

    if not request.get("deck_auth_local"):
        return None
    return extension_id_from_origin(request.headers.get("Origin"))


async def handle_deck_dock_pair(request: "web.Request") -> "web.Response":
    from navig.dock import pairing  # noqa: PLC0415

    ext = _local_extension(request)
    if ext is None:
        return web.json_response(
            {"ok": False, "error": "Only the NAVIG Dock extension on this computer can pair."}, status=403)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    name = str((body or {}).get("name") or "NAVIG Dock")[:80]
    try:
        token = pairing.pair(ext, name)
    except pairing.PairingConflict as exc:
        return web.json_response({
            "ok": False, "error": "another_extension_paired",
            "detail": f"Another browser extension ({exc}) is paired with NAVIG. If that is not yours, "
                      "run `navig dock unpair`, then turn browser control on again.",
        }, status=409)
    except pairing.PairingUnreadable:
        return web.json_response({
            "ok": False, "error": "pairing_unreadable",
            "detail": "NAVIG could not read its Dock pairing file. Run `navig dock unpair` to reset it.",
        }, status=503)
    return web.json_response({"ok": True, "token": token, "extension_id": ext})


async def handle_deck_dock_bridge(request: "web.Request") -> "web.StreamResponse":
    from navig.dock import pairing  # noqa: PLC0415
    from navig.dock.hub import get_bridge  # noqa: PLC0415
    from navig.dock.protocol import (  # noqa: PLC0415
        CLOSE_BAD_HELLO,
        CLOSE_UNAUTHORIZED,
        HELLO_TIMEOUT_S,
        PROTOCOL_VERSION,
    )
    from navig.gateway.routes.core import WS_HEARTBEAT_SECONDS  # noqa: PLC0415

    ext = _local_extension(request)
    if ext is None:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT_SECONDS, max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)

    # 1. hello — the token proves this is the paired Dock (the Origin alone is not proof).
    try:
        msg = await ws.receive(timeout=HELLO_TIMEOUT_S)
        hello = json.loads(msg.data) if msg.type == WSMsgType.TEXT else None
    except (asyncio.TimeoutError, ValueError, TypeError):
        hello = None
    if not isinstance(hello, dict) or hello.get("t") != "hello":
        await ws.close(code=CLOSE_BAD_HELLO, message=b"expected hello")
        return ws
    if hello.get("proto") != PROTOCOL_VERSION:
        await ws.close(code=CLOSE_BAD_HELLO, message=f"protocol {PROTOCOL_VERSION} required".encode())
        return ws
    if not pairing.verify(ext, str(hello.get("token") or "")):
        logger.warning("dock bridge: refused an unpaired or wrong-token extension %s…", ext[:6])
        await ws.close(code=CLOSE_UNAUTHORIZED, message=b"not paired")
        return ws

    bridge = get_bridge()
    session = await bridge.attach(ws, {
        "version": str(hello.get("version") or "")[:40],
        "browser": str(hello.get("browser") or "")[:80],
        "caps": [str(c)[:40] for c in (hello.get("caps") or [])][:64],
        "paused": bool(hello.get("paused")),
        "extension_id": ext,
    })
    await ws.send_str(json.dumps({"t": "welcome", "proto": PROTOCOL_VERSION, "session": session}))

    async def _pinger() -> None:
        while not ws.closed:
            await asyncio.sleep(_PING_EVERY_S)
            try:
                await ws.send_str(json.dumps({"t": "ping", "ts": time.time() * 1000}))
            except Exception:  # noqa: BLE001
                return

    pinger = asyncio.create_task(_pinger())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                frame = json.loads(msg.data)
            except ValueError:
                continue
            reply = bridge.on_frame(frame)
            if reply is not None:
                await ws.send_str(json.dumps(reply))
    finally:
        pinger.cancel()
        bridge.detach(ws)
    return ws

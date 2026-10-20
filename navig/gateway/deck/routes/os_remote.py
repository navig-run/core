"""Remote NAVIG OS — pairing, devices and the kill switch.

    GET    /api/deck/os/remote          status: relay, NAVIG OS server, devices, usage
    PATCH  /api/deck/os/remote          {"enabled": bool}                    LOCAL ONLY
    POST   /api/deck/os/pair            mint a one-time pairing code + link  LOCAL ONLY
    POST   /api/deck/os/pair/cancel     {"code"} — the QR dialog closed      LOCAL ONLY
    POST   /api/deck/os/link            mint a one-time BRAIN LINK for another
                                        NAVIG OS (see below)                  LOCAL ONLY
    GET    /api/deck/os/link/events     live events for a linked NAVIG OS, by cursor
    GET    /api/deck/os/devices         paired devices
    DELETE /api/deck/os/devices/{id}    revoke (ends its live sessions)
    POST   /api/deck/os/retire          tell the edge this tenant is moving   LOCAL ONLY
    POST   /api/os/pair/redeem          PUBLIC — the phone trades the code for a device token;
                                        also {"op":"request"} / {"op":"poll"} (sign-in, below)
                                        and {"op":"link"} (brain link, below)
    GET    /api/deck/os/account         the navig.run account this computer is linked to
    POST   /api/deck/os/account         {"email"} — link (emails a confirmation)   LOCAL ONLY
    DELETE /api/deck/os/account         unlink                                      LOCAL ONLY
    GET    /api/deck/os/requests        devices waiting for approval
    POST   /api/deck/os/requests/{id}/approve                                       LOCAL ONLY
    POST   /api/deck/os/requests/{id}/deny

SIGN-IN FROM A NEW DEVICE (no QR). The computer is linked once to a navig.run account.
Anyone signed in to that account at os.navig.run gets a short-lived ticket signed by
navig.run and sends it here with ``{"op":"request"}``; the ticket must name THIS tenant
and THE linked email (``navig.cloud.remote_ticket``). That buys only a request: the owner
is asked in NAVIG OS and in Telegram, and nothing is minted until they approve. The
waiting browser polls with ``{"op":"poll"}`` and a secret only it holds; the first poll
after approval receives the device token, once. Approval is local-only for the same
reason pairing is — a deck key or Telegram chat user must not be able to grant OS access;
the Telegram prompt is answerable by the OWNER only (``navig.telegram.os_access_actions``).

BRAIN LINK — another NAVIG OS uses this computer as its brain. The owner mints a link
here (``navig os link`` or the desktop), pastes it into the other machine's NAVIG OS, and
that server redeems it with ``{"op":"link"}`` for its own ``brain``-kind device token.
From then on it calls this deck API as ``<edge>/os/<tenant>/api/deck/*`` with that token
(the uplink rewrites the path; deck auth accepts the token). It is the deck key's power,
minus the local-only routes, revocable per machine from the same device list, and cut
by the same remote-access switch. It never carries the deck key itself.

Live events reach a linked NAVIG OS by POLLING ``/api/deck/os/link/events`` (every ~15 s):
the relay carries requests, not a held-open stream, and a long-poll would keep the
owner's Durable Object awake around the clock. The brain keeps the last few hundred
events in memory, numbered, from the first poll on.

Why "local only" is checked here and not left to deck auth: deck auth also admits the
deck key (Bearer) and allowed Telegram users. If either could mint a pairing code, the
deck key would grant OS access after all — the exact coupling per-device keys exist to
break. Revoking and listing stay open to deck auth on purpose: a lost phone must be
removable from Telegram or the Deck, and removing access can never grant any.

The redeem route sits outside ``/api/deck`` so deck auth does not apply: the one-time
code in its body IS the credential (128-bit, single use, two minutes). It is reached from
the phone through the edge (``/os/<tenant>/pair`` → rewritten by the uplink).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import socket
import time
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

from navig.gateway.deck.auth import is_local_request

logger = logging.getLogger(__name__)

# Redeem attempts per minute, for the whole install. Guessing a 128-bit code is hopeless
# anyway; this keeps a flood from turning into database writes. Deliberately NOT a
# lockout: a lockout would let anyone who knows the tenant id stop the owner pairing.
_REDEEM_PER_MIN = 10
_redeem_times: "collections.deque[float]" = collections.deque()
# Polls are cheap reads by a browser that already holds a secret, every ~2 s while it
# waits; several may wait at once. Counted apart so waiting never locks out redeeming.
_POLL_PER_MIN = 120
_poll_times: "collections.deque[float]" = collections.deque()
_API_TIMEOUT_S = 10.0

_EDGE_VERSION_TTL_S = 600.0
_edge_version_cache: dict[str, tuple[float, str]] = {}


# ── Plumbing ─────────────────────────────────────────────────────────────────


def _json(data: Any, status: int = 200) -> "web.Response":
    return web.json_response(data, status=status)


def _error(status: int, code: str, message: str) -> "web.Response":
    return _json({"error": code, "message": message}, status=status)


def _local_only(request: "web.Request") -> "web.Response | None":
    if is_local_request(request):
        return None
    return _error(
        403,
        "local_only",
        "Pairing and remote-access settings can only be changed on the computer itself.",
    )


def _uplink(request: "web.Request") -> Any:
    gw = request.app.get("gateway") or request.app.get("navig_gateway")
    cm = getattr(gw, "cloud_manager", None) if gw is not None else None
    return getattr(cm, "_uplink", None) if cm is not None else None


def _store() -> Any:
    from navig.store.os_devices import get_os_device_store

    return get_os_device_store()


async def _edge_version(url: str) -> str:
    """The deployed edge's version (``GET /``), cached ten minutes. "" when unreachable."""
    now = time.monotonic()
    cached = _edge_version_cache.get(url)
    if cached and now - cached[0] < _EDGE_VERSION_TTL_S:
        return cached[1]
    from navig.commands.lighthouse import _deployed_worker_version

    version = await asyncio.to_thread(_deployed_worker_version, url)
    if version:
        _edge_version_cache[url] = (now, version)
    return version


async def _relay_status(request: "web.Request") -> dict[str, Any]:
    from navig.cloud.os_relay import edge_supports_link, edge_supports_os

    uplink = _uplink(request)
    if uplink is None:
        return {
            "state": "missing", "url": "", "online": False, "edge_version": "",
            "supports_os": False, "supports_link": False,
        }
    version = await _edge_version(uplink.lighthouse_url)
    online = uplink.status == "online"
    supports = edge_supports_os(version)
    supports_link = edge_supports_link(version)
    if not online:
        state = "offline"
    elif not version:
        state = "unknown_version"
    elif not supports:
        state = "outdated"
    else:
        state = "ready"
    return {
        "state": state,
        "url": uplink.lighthouse_url,
        "online": online,
        "edge_version": version,
        "supports_os": supports,
        "supports_link": supports_link,
    }


def _usage(relay: Any) -> dict[str, Any]:
    usage = _store().usage_today()
    msgs = usage["msgs_in"] + usage["msgs_out"]
    budget = getattr(relay, "daily_msg_budget", 0) if relay is not None else 0
    return {
        **usage,
        "messages": msgs,
        # Cloudflare bills 20 WebSocket messages into a Durable Object as one request.
        "do_requests": msgs // 20,
        "budget_messages": budget,
    }


# ── Handlers ─────────────────────────────────────────────────────────────────


async def handle_os_remote_status(request: "web.Request") -> "web.Response":
    from navig.cloud.manager import _os_remote_enabled
    from navig.cloud.os_discovery import read_descriptor

    uplink = _uplink(request)
    relay = getattr(uplink, "os_relay", None)
    descriptor = await asyncio.to_thread(read_descriptor)
    devices = await asyncio.to_thread(_store().list_devices)
    snap = relay.snapshot() if relay is not None else {"sessions": 0, "devices_connected": []}
    return _json(
        {
            "enabled": relay.enabled if relay is not None else _os_remote_enabled(),
            "relay": await _relay_status(request),
            "os_server": {"running": descriptor is not None, "kind": (descriptor or {}).get("kind", "")},
            "devices": len(devices),
            "sessions": snap.get("sessions", 0),
            "usage_today": await asyncio.to_thread(_usage, relay),
            "hostname": socket.gethostname(),
        }
    )


async def handle_os_remote_patch(request: "web.Request") -> "web.Response":
    refused = _local_only(request)
    if refused:
        return refused
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _error(400, "bad_json", "Send a JSON body like {\"enabled\": false}.")
    from navig.core.coerce import coerce_bool

    if not isinstance(body, dict) or "enabled" not in body:
        return _error(400, "missing_enabled", "Send {\"enabled\": true} or {\"enabled\": false}.")
    enabled = coerce_bool(body.get("enabled"), default=True)
    from navig.config import get_config_manager

    get_config_manager().set_global("os.remote.enabled", enabled)
    relay = getattr(_uplink(request), "os_relay", None)
    ended = relay.set_enabled(enabled) if relay is not None else 0
    return _json({"enabled": enabled, "sessions_ended": ended})


async def handle_os_pair_start(request: "web.Request") -> "web.Response":
    refused = _local_only(request)
    if refused:
        return refused
    from navig.cloud.os_relay import PAIR_CODE_TTL_S_HINT, pair_link

    uplink = _uplink(request)
    relay_status = await _relay_status(request)
    if uplink is None:
        return _error(
            409, "relay_missing",
            "Remote access needs your Lighthouse relay. Set it up with: navig lighthouse login",
        )
    if not relay_status["online"]:
        return _error(409, "relay_offline", "Your Lighthouse relay is not connected right now. Try again in a moment.")
    if not relay_status["supports_os"]:
        return _error(
            409, "edge_outdated",
            "Your Lighthouse relay predates remote NAVIG OS. Update it with: navig lighthouse redeploy",
        )
    relay = getattr(uplink, "os_relay", None)
    if relay is not None and not relay.enabled:
        return _error(409, "disabled", "Remote access is turned off. Turn it on first.")

    code, expires = await asyncio.to_thread(_store().mint_pair_code)
    return _json(
        {
            "link": pair_link(uplink.lighthouse_url, uplink.tenant, code),
            "code": code,
            "expires_at": expires,
            "ttl_s": PAIR_CODE_TTL_S_HINT,
            "hostname": socket.gethostname(),
        }
    )


async def handle_os_link_start(request: "web.Request") -> "web.Response":
    """A one-time link that makes ANOTHER NAVIG OS use this computer as its brain."""
    refused = _local_only(request)
    if refused:
        return refused
    from navig.cloud.os_relay import LINK_CODE_TTL_S_HINT, brain_link
    from navig.store.os_devices import LINK_CODE_TTL_S

    uplink = _uplink(request)
    relay_status = await _relay_status(request)
    if uplink is None:
        return _error(
            409, "relay_missing",
            "Linking another NAVIG OS needs your Lighthouse relay. Set it up with: navig lighthouse login",
        )
    if not relay_status["online"]:
        return _error(409, "relay_offline", "Your Lighthouse relay is not connected right now. Try again in a moment.")
    if not relay_status["supports_link"]:
        return _error(
            409, "edge_outdated",
            "Your Lighthouse relay predates linking another NAVIG OS. Update it with: navig lighthouse redeploy",
        )
    relay = getattr(uplink, "os_relay", None)
    if relay is not None and not relay.enabled:
        return _error(409, "disabled", "Remote access is turned off. Turn it on first.")

    code, expires = await asyncio.to_thread(_store().mint_pair_code, ttl_s=LINK_CODE_TTL_S, kind="brain")
    return _json(
        {
            "link": brain_link(uplink.lighthouse_url, uplink.tenant, code),
            "code": code,
            "expires_at": expires,
            "ttl_s": LINK_CODE_TTL_S_HINT,
            "hostname": socket.gethostname(),
        }
    )


async def handle_os_pair_cancel(request: "web.Request") -> "web.Response":
    refused = _local_only(request)
    if refused:
        return refused
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    code = str((body or {}).get("code") or "")
    if code:
        await asyncio.to_thread(_store().cancel_pair_code, code)
    return _json({"ok": True})


async def handle_os_devices(request: "web.Request") -> "web.Response":
    relay = getattr(_uplink(request), "os_relay", None)
    connected = set((relay.snapshot() if relay is not None else {}).get("devices_connected", []))
    devices = await asyncio.to_thread(_store().list_devices)
    for d in devices:
        d["connected"] = d["id"] in connected
    return _json({"devices": devices})


async def handle_os_device_revoke(request: "web.Request") -> "web.Response":
    device_id = request.match_info.get("device_id", "")
    store = _store()
    device = await asyncio.to_thread(store.get, device_id)
    if device is None:
        return _error(404, "not_found", "No such device.")
    revoked = await asyncio.to_thread(store.revoke, device_id)
    relay = getattr(_uplink(request), "os_relay", None)
    ended = relay.kill_device(device_id) if relay is not None else 0
    return _json({"revoked": revoked or bool(device.get("revoked_at")), "sessions_ended": ended})


async def handle_os_retire(request: "web.Request") -> "web.Response":
    refused = _local_only(request)
    if refused:
        return refused
    uplink = _uplink(request)
    sent = await uplink.send_retire() if uplink is not None else False
    return _json({"retired": sent})


def _limited(times: "collections.deque[float]", per_min: int) -> bool:
    now = time.monotonic()
    while times and now - times[0] > 60:
        times.popleft()
    if len(times) >= per_min:
        return True
    times.append(now)
    return False


async def handle_os_pair_redeem(request: "web.Request") -> "web.Response":
    # JSON only: a cross-site form post cannot send it without a preflight, which the
    # edge answers for its own origin list, so this cannot be triggered from a page the
    # victim merely visits.
    if (request.content_type or "") != "application/json":
        return _error(415, "json_required", "Send the pairing code as JSON.")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    op = str(body.get("op") or "") if isinstance(body, dict) else ""
    if op == "poll":
        if _limited(_poll_times, _POLL_PER_MIN):
            return _error(429, "rate_limited", "Checking too often. Wait a moment.")
    elif _limited(_redeem_times, _REDEEM_PER_MIN):
        return _error(429, "rate_limited", "Too many pairing attempts. Wait a minute and scan the code again.")
    if not isinstance(body, dict):
        return _error(400, "bad_json", "The pairing request was not valid JSON.")
    code = str(body.get("code") or "")

    from navig.cloud.manager import _os_remote_enabled
    from navig.cloud.os_relay import device_name_from_ua

    uplink = _uplink(request)
    relay = getattr(uplink, "os_relay", None)
    enabled = relay.enabled if relay is not None else _os_remote_enabled()
    if not enabled:
        return _error(403, "disabled", "Remote access is turned off on that computer.")
    if op == "request":
        return await _access_request(request, body, uplink)
    if op == "poll":
        return await _access_poll(request, body)
    if op == "link":
        return await _link_redeem(request, body)
    if op:
        return _error(400, "unknown_op", "Update NAVIG OS in this browser and try again.")

    store = _store()
    if not code or not await asyncio.to_thread(store.take_pair_code, code):
        return _error(
            400, "invalid_code",
            "That pairing code has expired or was already used. Show a new code on your computer.",
        )
    ua = request.headers.get("User-Agent", "")
    name = str((body or {}).get("name") or "").strip() or device_name_from_ua(ua)
    device_id, token = await asyncio.to_thread(store.add_device, name=name, ua=ua)

    gw = request.app.get("gateway") or request.app.get("navig_gateway")
    events = getattr(gw, "system_events", None)
    if events is not None:
        try:
            await events.emit("os.device.paired", {"id": device_id, "name": name})
        except Exception:  # noqa: BLE001 — the pairing already happened; the toast is extra
            logger.debug("os.device.paired event not emitted", exc_info=True)
    return _json({"deviceId": device_id, "deviceToken": token, "name": name, "hostname": socket.gethostname()})


async def _link_redeem(request: "web.Request", body: dict[str, Any]) -> "web.Response":
    """Another NAVIG OS trades a brain-link code for its own ``brain`` device token."""
    code = str(body.get("code") or "")
    store = _store()
    if not code or not await asyncio.to_thread(store.take_pair_code, code, kind="brain"):
        return _error(
            400, "invalid_code",
            "That link has expired or was already used. Make a new one on your computer: navig os link",
        )
    ua = request.headers.get("User-Agent", "")
    name = str(body.get("name") or "").strip() or "NAVIG OS"
    device_id, token = await asyncio.to_thread(store.add_device, name=name, ua=ua, kind="brain")
    await _emit(request, "os.device.paired", {"id": device_id, "name": name, "kind": "brain"})
    return _json({"deviceId": device_id, "deviceToken": token, "name": name, "hostname": socket.gethostname()})


# ── Live events for a linked NAVIG OS ───────────────────────────────────────

_EVENT_BACKLOG_MAX = 300
_EVENTS_PER_REPLY = 100
# A single event bigger than this is delivered as its type only: the poll answers through
# the relay, and one huge payload must not crowd out the rest of the page.
_EVENT_MAX_BYTES = 32 * 1024


class EventBacklog:
    """The last ``_EVENT_BACKLOG_MAX`` system events, numbered, for pollers.

    ``epoch`` changes whenever the brain restarts, so a poller holding a cursor from the
    previous process learns it missed events instead of reading the wrong ones.
    """

    def __init__(self, maxlen: int = _EVENT_BACKLOG_MAX) -> None:
        import uuid

        self.epoch = uuid.uuid4().hex[:12]
        self.seq = 0
        self._events: "collections.deque[dict[str, Any]]" = collections.deque(maxlen=maxlen)

    def add(self, event_type: str, payload: Any) -> None:
        import json
        from datetime import datetime, timezone

        data: Any = payload if payload is not None else {}
        try:
            size = len(json.dumps(data, default=str))
        except (TypeError, ValueError):
            size = _EVENT_MAX_BYTES + 1
        if size > _EVENT_MAX_BYTES:
            data = {"_truncated": True}
        self.seq += 1
        self._events.append(
            {
                "seq": self.seq,
                "type": str(event_type or "status_update"),
                "data": data,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )

    def since(self, epoch: str, cursor: int | None, limit: int = _EVENTS_PER_REPLY) -> dict[str, Any]:
        """Events after *cursor*. No cursor, or another epoch's, starts at "now".

        ``gap`` is true when events were missed: the brain restarted, or the poller fell
        further behind than the backlog reaches.
        """
        if cursor is None or epoch != self.epoch or cursor > self.seq:
            # A cursor from the future can only be another process's: resync to now.
            return {"epoch": self.epoch, "cursor": self.seq, "events": [], "gap": cursor is not None}
        oldest = self._events[0]["seq"] if self._events else self.seq + 1
        events = [e for e in self._events if e["seq"] > cursor][:limit]
        return {
            "epoch": self.epoch,
            "cursor": events[-1]["seq"] if events else self.seq,
            "events": events,
            "gap": cursor < oldest - 1,
        }


_backlogs: dict[int, tuple[Any, EventBacklog]] = {}


def _backlog_for(request: "web.Request") -> EventBacklog | None:
    """The backlog subscribed to this gateway's event queue, created on the first poll."""
    queue = getattr(_gateway(request), "system_events", None)
    if queue is None or not hasattr(queue, "subscribe"):
        return None
    held = _backlogs.get(id(queue))
    if held is not None and held[0] is queue:
        return held[1]
    backlog = EventBacklog()

    def _on_event(evt: Any) -> None:
        try:
            backlog.add(getattr(evt, "event_type", "status_update"), getattr(evt, "payload", {}))
        except Exception:  # noqa: BLE001 — a dropped event must never break the queue
            logger.debug("link event not recorded", exc_info=True)

    queue.subscribe("*", _on_event)
    _backlogs[id(queue)] = (queue, backlog)
    return backlog


async def handle_os_link_events(request: "web.Request") -> "web.Response":
    """``?epoch=&cursor=`` → ``{epoch, cursor, events, gap}``. Deck auth applies (a linked
    NAVIG OS's brain-link token, the desktop, the deck key)."""
    backlog = _backlog_for(request)
    if backlog is None:
        return _error(503, "no_events", "This NAVIG has no event queue running.")
    raw = request.query.get("cursor", "")
    try:
        cursor: int | None = int(raw) if raw != "" else None
    except ValueError:
        return _error(400, "bad_cursor", "cursor must be a whole number.")
    if cursor is not None and cursor < 0:
        return _error(400, "bad_cursor", "cursor must be a whole number.")
    import json

    return web.json_response(
        backlog.since(request.query.get("epoch", ""), cursor),
        dumps=lambda obj: json.dumps(obj, default=str),
    )


# ── Sign-in from a new device ────────────────────────────────────────────────


def _account_email() -> str:
    from navig.config import get_config_manager

    return str(get_config_manager().get("os.remote.account_email", "") or "").strip().lower()


def _gateway(request: "web.Request") -> Any:
    return request.app.get("gateway") or request.app.get("navig_gateway")


async def _emit(request: "web.Request", event: str, payload: dict[str, Any]) -> None:
    events = getattr(_gateway(request), "system_events", None)
    if events is None:
        return
    try:
        await events.emit(event, payload)
    except Exception:  # noqa: BLE001 — the state change already happened; the event is extra
        logger.debug("%s event not emitted", event, exc_info=True)


async def _access_request(request: "web.Request", body: dict[str, Any], uplink: Any) -> "web.Response":
    from navig.cloud.os_relay import device_name_from_ua
    from navig.cloud.remote_ticket import TicketError, verify_ticket
    from navig.store.os_devices import ACCESS_REQUEST_TTL_S

    if uplink is None:
        return _error(409, "relay_missing", "This computer's relay is not connected.")
    try:
        ticket = verify_ticket(str(body.get("ticket") or ""), tenant=uplink.tenant, email=_account_email())
    except TicketError as exc:
        return _error(403, exc.code, str(exc))
    ua = request.headers.get("User-Agent", "")
    name = str(body.get("name") or "").strip() or device_name_from_ua(ua)
    store = _store()
    made = await asyncio.to_thread(
        store.add_access_request, email=ticket.email, name=name, ua=ua, ticket_nonce=ticket.nonce
    )
    if made is None:
        return _error(409, "ticket_used", "That sign-in was already used. Sign in again.")
    request_id, secret = made
    req = await asyncio.to_thread(store.get_access_request, request_id) or {
        "id": request_id, "name": name, "email": ticket.email,
    }
    await _emit(request, "os.device.request", {"id": request_id, "name": req.get("name"), "email": ticket.email})
    telegram = False
    try:
        from navig.telegram import os_access_actions

        telegram = await os_access_actions.send_prompt(_gateway(request), req)
    except Exception:  # noqa: BLE001 — the desktop prompt still works
        logger.debug("telegram device prompt failed", exc_info=True)
    return _json(
        {
            "requestId": request_id,
            "pollSecret": secret,
            "expiresIn": ACCESS_REQUEST_TTL_S,
            "hostname": socket.gethostname(),
            "telegram": telegram,
        }
    )


async def _access_poll(request: "web.Request", body: dict[str, Any]) -> "web.Response":
    result = await asyncio.to_thread(
        _store().poll_access_request, str(body.get("id") or ""), str(body.get("secret") or "")
    )
    if result.get("status") == "approved":
        await _emit(request, "os.device.paired", {"id": result["deviceId"], "name": result.get("name", "")})
        result["hostname"] = socket.gethostname()
    return _json(result)


async def handle_os_requests(request: "web.Request") -> "web.Response":
    return _json({"requests": await asyncio.to_thread(_store().list_pending_requests)})


async def _decide(request: "web.Request", approve: bool) -> "web.Response":
    from navig.telegram import os_access_actions

    took, req = await os_access_actions.decide(
        _gateway(request), request.match_info.get("request_id", ""), approve=approve, where="NAVIG OS"
    )
    if req is None:
        return _error(404, "not_found", "No such request.")
    if not took:
        return _error(409, "not_pending", f"That request is no longer waiting ({req.get('status')}).")
    return _json({"ok": True, "approved": approve, "name": req.get("name")})


async def handle_os_request_approve(request: "web.Request") -> "web.Response":
    refused = _local_only(request)
    if refused:
        return refused
    return await _decide(request, True)


async def handle_os_request_deny(request: "web.Request") -> "web.Response":
    # Denying can never grant anything, so deck auth (Telegram, the Deck) may do it too.
    return await _decide(request, False)


# ── Linking this computer to a navig.run account ─────────────────────────────


def _api_base() -> str:
    from navig.config import get_config_manager

    base = get_config_manager().get("cloud.broker_url", "https://api.navig.run") or "https://api.navig.run"
    return str(base).rstrip("/")


async def _api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Call api.navig.run as this computer (Bearer = deck key; its sha256 is the tenant)."""
    import aiohttp

    from navig.config import get_config_manager

    key = str(get_config_manager().get("deck.api_key", "") or "")
    if not key:
        return 0, {"error": "no_key", "message": "This computer has no deck key yet."}
    try:
        timeout = aiohttp.ClientTimeout(total=_API_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, f"{_api_base()}{path}", json=body, headers={"Authorization": f"Bearer {key}"}
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = {}
                return resp.status, data if isinstance(data, dict) else {}
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return 0, {"error": "unreachable", "message": str(exc) or type(exc).__name__}


async def handle_os_account(request: "web.Request") -> "web.Response":
    email = _account_email()
    status, data = await _api("GET", "/api/remote/link")
    if status != 200:
        # Unknown is not "not linked": say we could not check.
        return _json({"state": "waiting" if email else "none", "email": email, "reachable": False})
    linked_email = str(data.get("email") or "").lower() if data.get("linked") else ""
    if email and linked_email == email:
        state = "linked"
    elif email:
        state = "waiting"  # the confirmation email has not been clicked (yet)
    else:
        state = "none"
    return _json({"state": state, "email": email, "reachable": True})


async def handle_os_account_link(request: "web.Request") -> "web.Response":
    refused = _local_only(request)
    if refused:
        return refused
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    email = str((body or {}).get("email") or "").strip().lower() if isinstance(body, dict) else ""
    local, _, domain = email.partition("@")
    if not local or "." not in domain or " " in email:
        return _error(400, "invalid_email", "Enter the email address you use for navig.run.")
    uplink = _uplink(request)
    if uplink is None:
        return _error(409, "relay_missing", "Set up your Lighthouse relay first: navig lighthouse login")
    status, data = await _api(
        "POST",
        "/api/remote/link/start",
        {"email": email, "name": socket.gethostname(), "edge_url": uplink.lighthouse_url},
    )
    if status != 200:
        message = str(data.get("message") or data.get("error") or f"HTTP {status}")
        return _error(502, "link_failed", f"Could not reach navig.run: {message}")
    from navig.config import get_config_manager

    # Tickets are accepted only for this email — set it now. Nothing reaches this computer
    # through the account until the owner has ALSO clicked the confirmation link.
    get_config_manager().set_global("os.remote.account_email", email)
    out: dict[str, Any] = {"sent": True, "email": email}
    if data.get("devConfirmUrl"):
        out["devConfirmUrl"] = data["devConfirmUrl"]
    return _json(out)


async def handle_os_account_unlink(request: "web.Request") -> "web.Response":
    refused = _local_only(request)
    if refused:
        return refused
    from navig.config import get_config_manager

    # Local first: once the email is cleared no ticket is accepted, whatever navig.run says.
    get_config_manager().set_global("os.remote.account_email", "")
    status, _ = await _api("DELETE", "/api/remote/link")
    return _json({"unlinked": True, "navig_run_updated": status == 200})


def register(app: "web.Application") -> None:
    app.router.add_get("/api/deck/os/remote", handle_os_remote_status)
    app.router.add_patch("/api/deck/os/remote", handle_os_remote_patch)
    app.router.add_post("/api/deck/os/pair", handle_os_pair_start)
    # POST, not DELETE-with-body: the desktop reaches these through a generic brain
    # proxy whose DELETE carries no body.
    app.router.add_post("/api/deck/os/pair/cancel", handle_os_pair_cancel)
    app.router.add_post("/api/deck/os/link", handle_os_link_start)
    app.router.add_get("/api/deck/os/link/events", handle_os_link_events)
    app.router.add_get("/api/deck/os/devices", handle_os_devices)
    app.router.add_delete("/api/deck/os/devices/{device_id}", handle_os_device_revoke)
    app.router.add_post("/api/deck/os/retire", handle_os_retire)
    app.router.add_post("/api/os/pair/redeem", handle_os_pair_redeem)
    app.router.add_get("/api/deck/os/account", handle_os_account)
    app.router.add_post("/api/deck/os/account", handle_os_account_link)
    app.router.add_delete("/api/deck/os/account", handle_os_account_unlink)
    app.router.add_get("/api/deck/os/requests", handle_os_requests)
    app.router.add_post("/api/deck/os/requests/{request_id}/approve", handle_os_request_approve)
    app.router.add_post("/api/deck/os/requests/{request_id}/deny", handle_os_request_deny)

"""NAVIG OS over Lighthouse — the brain's half.

A phone or browser at os.navig.run opens a socket on the user's Lighthouse edge; the edge
multiplexes it down this brain's single outbound uplink as a stream id (``sid``). This
module turns each stream into a local WebSocket to the NAVIG OS server on this PC:

    edge ──os_open{sid}──▶ OsRelay ──(first message = NAVIG OS handshake)──▶ check device
         ◀──os_ok{sid}────        ──ws://127.0.0.1:<port>, token swapped──▶ NAVIG OS server
         ◀─@sid:batch────  ◀────────────── pipe both ways ─────────────────▶

Credentials, and why they are swapped here:
  * The phone presents a DEVICE token (``navig.store.os_devices``) in the handshake's
    ``token`` field. It is never the deck key, and it never reaches the NAVIG OS server.
  * The NAVIG OS server's own token (from ``os_discovery``) never leaves this PC.
  * The upgrade request carries ``X-Navig-Via: relay`` + ``X-Navig-Remote-Device`` so the
    OS server can tell a remote client from the desktop window; only a local process can
    set them.

Relay framing (between this module and the hosted page; the edge never looks inside):
  * one or more NAVIG OS envelopes joined by ``\\x1e`` — a batch;
  * ``\\x1f<mid>|<i>|<n>|<chunk>`` — a fragment of one envelope too big to send whole.
  Both separators are control characters that valid JSON text cannot contain unescaped,
  so they can never collide with an envelope.

Batching is the cost control: Cloudflare bills messages INTO a Durable Object, not bytes,
so a streamed reply of 200 small events becomes a handful of uplink messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

CAPS = ["os1"]

BATCH_SEP = "\x1e"
FRAGMENT_MARK = "\x1f"
# Coalesce downstream envelopes for up to this long / this many bytes per flush.
BATCH_WINDOW_S = 0.02
BATCH_BYTES = 128 * 1024
# Anything bigger than this is sent as fragments (keeps each uplink message small, so a
# Telegram reply never waits behind more than one of them).
FRAGMENT_BYTES = 256 * 1024
# A reassembled message may not exceed this (a large session import, a file upload).
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MAX_PARTIAL_MESSAGES = 4
MAX_FRAGMENTS = 4096
OS_CONNECT_TIMEOUT_S = 10.0
HANDSHAKE_TIMEOUT_S = 5.0
MAX_HANDSHAKE_BYTES = 8 * 1024
INBOX_MAX = 256
USAGE_FLUSH_S = 30.0
# Messages per UTC day before new sessions are refused (Cloudflare bills 20 WebSocket
# messages into a Durable Object as one request; the free plan has 100k requests a day,
# shared with Telegram and everything else the relay carries). Measured 2026-09-30 on a
# real phone session through the live relay to the installed desktop app: opening the app
# ≈ 700 messages, idle ≈ 12.5/min, one short chat turn ≈ 60–75. A heavy day — the app open
# eight hours, ten opens, a hundred chat turns — is ≈ 20k. 300k (15k requests, 15% of the
# free plan) leaves 15× that for long streamed answers and file transfers, while a runaway
# client is stopped long before it can starve the Telegram bot of its share.
DEFAULT_DAILY_MSG_BUDGET = 300_000

# Close codes (mirrors services/lighthouse/src/os_relay.ts CLOSE; the hosted app maps them).
CLOSE_DEVICE = 4401
CLOSE_DISABLED = 4403
CLOSE_OS_NOT_RUNNING = 4410
CLOSE_LIMIT = 4429
CLOSE_TOO_BIG = 1009
CLOSE_GOING_AWAY = 1001
CLOSE_UPLINK_RESTART = 1012

_WS_END = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
    aiohttp.WSMsgType.ERROR,
)

SendControl = Callable[[dict[str, Any]], Awaitable[None]]
SendData = Callable[[str, str], Awaitable[None]]


class RelayFramingError(ValueError):
    """A fragment that cannot be reassembled safely (too big, too many, malformed)."""


class Reassembler:
    """Undo the relay framing on one stream: yields whole NAVIG OS envelopes."""

    def __init__(self, max_bytes: int = MAX_MESSAGE_BYTES, max_partial: int = MAX_PARTIAL_MESSAGES):
        self._max_bytes = max_bytes
        self._max_partial = max_partial
        self._parts: dict[str, dict[str, Any]] = {}

    def feed(self, payload: str) -> list[str]:
        if not payload.startswith(FRAGMENT_MARK):
            return [p for p in payload.split(BATCH_SEP) if p]
        try:
            mid, idx_s, total_s, chunk = payload[1:].split("|", 3)
            idx, total = int(idx_s), int(total_s)
        except ValueError as exc:
            raise RelayFramingError("malformed fragment") from exc
        if not mid or len(mid) > 16 or total < 1 or total > MAX_FRAGMENTS or not 0 <= idx < total:
            raise RelayFramingError("bad fragment index")
        entry = self._parts.get(mid)
        if entry is None:
            if len(self._parts) >= self._max_partial:
                raise RelayFramingError("too many partial messages")
            entry = {"total": total, "chunks": {}, "size": 0}
            self._parts[mid] = entry
        if entry["total"] != total or idx in entry["chunks"]:
            raise RelayFramingError("inconsistent fragment")
        entry["chunks"][idx] = chunk
        entry["size"] += len(chunk)
        if entry["size"] > self._max_bytes:
            raise RelayFramingError("message too big")
        if len(entry["chunks"]) < total:
            return []
        del self._parts[mid]
        return ["".join(entry["chunks"][i] for i in range(total))]


def fragment(message: str, size: int = FRAGMENT_BYTES) -> list[str]:
    """Split one envelope into relay fragments (inverse of :class:`Reassembler`)."""
    mid = secrets.token_hex(4)
    chunks = [message[i : i + size] for i in range(0, len(message), size)] or [""]
    n = len(chunks)
    return [f"{FRAGMENT_MARK}{mid}|{i}|{n}|{c}" for i, c in enumerate(chunks)]


def _map_upstream_close(code: Optional[int]) -> tuple[int, str]:
    """What the browser should see when the NAVIG OS server closed the local socket."""
    if code == 4005:
        # We presented the token from the descriptor and the server refused it: the desktop
        # restarted with a new token after we read the file. Transient — the page reconnects
        # and the next handshake reads the fresh descriptor.
        return CLOSE_OS_NOT_RUNNING, "NAVIG OS restarted: reconnecting"
    if code in (4001, 4002, 4003, 4004, 4006, 4008):
        return code, "closed by NAVIG OS"
    if code in (1000, 1001, None):
        return CLOSE_GOING_AWAY, "NAVIG OS closed the session"
    return 1011, "NAVIG OS connection lost"


@dataclass
class _Session:
    sid: str
    ua: str = ""
    ip: str = ""
    inbox: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=INBOX_MAX))
    task: Optional[asyncio.Task] = None
    device_id: str = ""
    upstream: Optional[aiohttp.ClientWebSocketResponse] = None
    browser_closed: bool = False
    cleaned: bool = False
    close_code: int = 1000
    close_reason: str = ""
    # Downstream batching
    out: list[str] = field(default_factory=list)
    out_size: int = 0
    flush_handle: Optional[asyncio.TimerHandle] = None
    flush_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Metering, flushed to the store periodically
    msgs_in: int = 0
    msgs_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0


class OsRelay:
    """Owns every remote NAVIG OS stream riding one uplink."""

    def __init__(
        self,
        *,
        send_control: SendControl,
        send_data: SendData,
        store_fn: Optional[Callable[[], Any]] = None,
        descriptor_fn: Optional[Callable[[], Optional[dict[str, Any]]]] = None,
        enabled: bool = True,
        daily_msg_budget: int = DEFAULT_DAILY_MSG_BUDGET,
        on_budget_exceeded: Optional[Callable[[dict[str, int]], None]] = None,
    ):
        self._send_control = send_control
        self._send_data = send_data
        self._store_fn = store_fn or _default_store
        self._descriptor_fn = descriptor_fn or _default_descriptor
        self.enabled = enabled
        self.daily_msg_budget = max(0, int(daily_msg_budget))
        self._on_budget_exceeded = on_budget_exceeded
        self._sessions: dict[str, _Session] = {}
        self._http: Optional[aiohttp.ClientSession] = None
        self._usage_task: Optional[asyncio.Task] = None
        self._budget_noted_day = ""

    @staticmethod
    def caps() -> list[str]:
        return list(CAPS)

    # ── Frames from the edge ────────────────────────────────────────────────

    def on_open(self, frame: dict[str, Any]) -> None:
        sid = str(frame.get("sid") or "")
        if not sid or sid in self._sessions:
            return
        session = _Session(sid=sid, ua=str(frame.get("ua") or "")[:200], ip=str(frame.get("ip") or ""))
        self._sessions[sid] = session
        session.task = asyncio.create_task(self._run(session))
        session.task.add_done_callback(lambda t, s=session: self._on_task_done(s, t))
        self._ensure_usage_task()

    def on_data(self, sid: str, payload: str) -> None:
        session = self._sessions.get(sid)
        if session is None:
            # The edge thinks this stream is open; we do not (e.g. we restarted). End it.
            self._spawn(self._send_control({"t": "os_close", "sid": sid, "code": 1011, "reason": "unknown stream"}))
            return
        session.msgs_in += 1
        session.bytes_in += len(payload)
        try:
            session.inbox.put_nowait(payload)
        except asyncio.QueueFull:
            self._end(session, CLOSE_LIMIT, "too slow: messages piling up")

    def on_close(self, sid: str, code: Any = 1000, reason: Any = "") -> None:
        session = self._sessions.get(sid)
        if session is None:
            return
        session.browser_closed = True
        self._end(session, int(code) if isinstance(code, int) else 1000, str(reason or ""))

    # ── Commands from the gateway ──────────────────────────────────────────

    def kill_device(self, device_id: str) -> int:
        """A device was revoked: end its live sessions now (4401)."""
        n = 0
        for session in list(self._sessions.values()):
            if session.device_id == device_id:
                self._end(session, CLOSE_DEVICE, "this device was removed")
                n += 1
        return n

    def set_enabled(self, enabled: bool) -> int:
        """The kill switch. Turning it off ends every live remote session immediately."""
        self.enabled = bool(enabled)
        if self.enabled:
            return 0
        sessions = list(self._sessions.values())
        for session in sessions:
            self._end(session, CLOSE_DISABLED, "remote access was turned off on this computer")
        return len(sessions)

    async def close_all(self, code: int = CLOSE_UPLINK_RESTART, reason: str = "computer reconnecting") -> None:
        """The uplink dropped: every stream on it is gone (the edge closes the browsers)."""
        sessions = list(self._sessions.values())
        for session in sessions:
            session.browser_closed = True  # nothing to tell: the edge already knows
            self._end(session, code, reason)
        for session in sessions:
            if session.task is not None:
                try:
                    await session.task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        if self._usage_task is not None:
            self._usage_task.cancel()
            self._usage_task = None
        if self._http is not None and not self._http.closed:
            await self._http.close()
        self._http = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "sessions": len(self._sessions),
            "devices_connected": sorted({s.device_id for s in self._sessions.values() if s.device_id}),
            "daily_msg_budget": self.daily_msg_budget,
        }

    # ── One stream ─────────────────────────────────────────────────────────

    async def _run(self, s: _Session) -> None:
        try:
            await self._serve(s)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — one bad stream must never touch the uplink
            logger.debug("os relay stream %s failed: %r", s.sid, exc)
            s.close_code, s.close_reason = 1011, "relay error"
        finally:
            await self._cleanup(s)

    def _on_task_done(self, s: _Session, _task: asyncio.Task) -> None:
        # A task cancelled BEFORE its first step never enters `_run`, so its `finally`
        # never runs: the session would leak and the browser would never be told. This
        # callback runs however the task ended.
        if not s.cleaned:
            self._spawn(self._cleanup(s))

    async def _cleanup(self, s: _Session) -> None:
        if s.cleaned:
            return
        s.cleaned = True
        if s.flush_handle is not None:
            s.flush_handle.cancel()
        if s.upstream is not None and not s.upstream.closed:
            try:
                await s.upstream.close()
            except Exception:  # noqa: BLE001
                pass
        await self._flush_usage(s)
        if self._sessions.get(s.sid) is s:
            del self._sessions[s.sid]
        if not s.browser_closed:
            await self._send_control(
                {"t": "os_close", "sid": s.sid, "code": s.close_code, "reason": s.close_reason[:120]}
            )

    async def _serve(self, s: _Session) -> None:
        if not self.enabled:
            s.close_code, s.close_reason = CLOSE_DISABLED, "remote access is turned off on this computer"
            return
        if self._over_budget():
            s.close_code, s.close_reason = CLOSE_LIMIT, "today's remote access budget is used up"
            return

        try:
            first = await asyncio.wait_for(s.inbox.get(), HANDSHAKE_TIMEOUT_S)
        except asyncio.TimeoutError:
            s.close_code, s.close_reason = CLOSE_DEVICE, "no handshake"
            return
        handshake = _parse_handshake(first)
        if handshake is None:
            s.close_code, s.close_reason = CLOSE_DEVICE, "expected a NAVIG OS handshake"
            return

        store = self._store_fn()
        device = await asyncio.to_thread(store.device_for_token, str(handshake.get("token") or ""))
        if device is None:
            s.close_code, s.close_reason = CLOSE_DEVICE, "device not recognised: pair it again"
            return
        s.device_id = device["id"]

        target = await asyncio.to_thread(self._descriptor_fn)
        if target is None:
            s.close_code, s.close_reason = CLOSE_OS_NOT_RUNNING, "NAVIG OS is not running on this computer"
            return

        handshake["token"] = target["token"]
        try:
            http = await self._get_http()
            s.upstream = await asyncio.wait_for(
                http.ws_connect(
                    target["ws_url"],
                    headers={"X-Navig-Via": "relay", "X-Navig-Remote-Device": s.device_id},
                    max_msg_size=MAX_MESSAGE_BYTES,
                    timeout=aiohttp.ClientWSTimeout(ws_close=5.0),
                    autoping=True,
                ),
                OS_CONNECT_TIMEOUT_S,
            )
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
            logger.debug("os relay: cannot reach NAVIG OS at %s: %r", target["ws_url"], exc)
            s.close_code, s.close_reason = CLOSE_OS_NOT_RUNNING, "NAVIG OS is not reachable on this computer"
            return

        await self._send_control({"t": "os_ok", "sid": s.sid, "deviceId": s.device_id})
        await s.upstream.send_str(json.dumps(handshake, separators=(",", ":")))
        await asyncio.to_thread(store.touch, s.device_id)

        up = asyncio.create_task(self._browser_to_os(s))
        down = asyncio.create_task(self._os_to_browser(s))
        done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for task in done:
            exc = task.exception()
            if isinstance(exc, RelayFramingError):
                s.close_code, s.close_reason = CLOSE_TOO_BIG, str(exc)
            elif exc is not None:
                raise exc

    async def _browser_to_os(self, s: _Session) -> None:
        reasm = Reassembler()
        upstream = s.upstream
        if upstream is None:
            return
        while True:
            payload = await s.inbox.get()
            for message in reasm.feed(payload):
                await upstream.send_str(message)

    async def _os_to_browser(self, s: _Session) -> None:
        upstream = s.upstream
        if upstream is None:
            return
        async for msg in upstream:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._queue_down(s, msg.data)
            elif msg.type in _WS_END:
                break
        await self._flush(s)
        if not s.browser_closed and s.close_code == 1000:
            s.close_code, s.close_reason = _map_upstream_close(upstream.close_code)

    # ── Downstream batching ────────────────────────────────────────────────

    async def _queue_down(self, s: _Session, message: str) -> None:
        if len(message) > FRAGMENT_BYTES or BATCH_SEP in message or message.startswith(FRAGMENT_MARK):
            await self._flush(s)
            for part in fragment(message):
                await self._emit(s, part)
            return
        if s.out and s.out_size + len(message) + 1 > BATCH_BYTES:
            await self._flush(s)
        s.out.append(message)
        s.out_size += len(message) + 1
        if s.out_size >= BATCH_BYTES:
            await self._flush(s)
        elif s.flush_handle is None:
            loop = asyncio.get_running_loop()
            s.flush_handle = loop.call_later(BATCH_WINDOW_S, lambda: self._spawn(self._flush(s)))

    async def _flush(self, s: _Session) -> None:
        async with s.flush_lock:
            if s.flush_handle is not None:
                s.flush_handle.cancel()
                s.flush_handle = None
            if not s.out:
                return
            batch, s.out, s.out_size = BATCH_SEP.join(s.out), [], 0
            await self._emit(s, batch)

    async def _emit(self, s: _Session, payload: str) -> None:
        s.msgs_out += 1
        s.bytes_out += len(payload)
        await self._send_data(s.sid, payload)

    # ── Ending, metering, budget ───────────────────────────────────────────

    def _end(self, s: _Session, code: int, reason: str) -> None:
        if s.close_code == 1000:
            s.close_code, s.close_reason = code, reason
        if s.task is not None and not s.task.done():
            s.task.cancel()

    async def _flush_usage(self, s: _Session) -> None:
        if not s.device_id:
            return
        counts = {"msgs_in": s.msgs_in, "msgs_out": s.msgs_out, "bytes_in": s.bytes_in, "bytes_out": s.bytes_out}
        s.msgs_in = s.msgs_out = s.bytes_in = s.bytes_out = 0
        if not any(counts.values()):
            return
        try:
            await asyncio.to_thread(self._store_fn().add_usage, s.device_id, **counts)
        except Exception as exc:  # noqa: BLE001 — metering must never break a session
            logger.debug("os relay usage write failed: %r", exc)

    def _ensure_usage_task(self) -> None:
        if self._usage_task is None or self._usage_task.done():
            self._usage_task = asyncio.create_task(self._usage_loop())

    async def _usage_loop(self) -> None:
        try:
            while self._sessions:
                await asyncio.sleep(USAGE_FLUSH_S)
                for s in list(self._sessions.values()):
                    await self._flush_usage(s)
                if self._over_budget():
                    for s in list(self._sessions.values()):
                        self._end(s, CLOSE_LIMIT, "today's remote access budget is used up")
        except asyncio.CancelledError:
            return

    def _over_budget(self) -> bool:
        if self.daily_msg_budget <= 0:
            return False
        try:
            usage = self._store_fn().usage_today()
        except Exception:  # noqa: BLE001 — cannot measure: do not lock the owner out
            return False
        used = usage["msgs_in"] + usage["msgs_out"]
        if used < self.daily_msg_budget:
            return False
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if self._budget_noted_day != day:
            self._budget_noted_day = day
            if self._on_budget_exceeded is not None:
                try:
                    self._on_budget_exceeded(usage)
                except Exception:  # noqa: BLE001
                    pass
        return True

    # ── Plumbing ───────────────────────────────────────────────────────────

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    def _spawn(self, coro: Awaitable[Any]) -> None:
        from navig.core.background import spawn

        spawn(coro)


def _parse_handshake(payload: str) -> Optional[dict[str, Any]]:
    if len(payload) > MAX_HANDSHAKE_BYTES or payload.startswith(FRAGMENT_MARK):
        return None
    first = payload.split(BATCH_SEP, 1)[0]
    try:
        env = json.loads(first)
    except ValueError:
        return None
    if not isinstance(env, dict) or env.get("type") != "handshake" or not isinstance(env.get("token"), str):
        return None
    return env


def _default_store() -> Any:
    from navig.store.os_devices import get_os_device_store

    return get_os_device_store()


def _default_descriptor() -> Optional[dict[str, Any]]:
    from navig.cloud.os_discovery import read_descriptor

    return read_descriptor()


# ── Pairing helpers (used by the gateway routes and `navig os pair`) ──────────

HOSTED_APP_URL = "https://os.navig.run/"
# Shown to the desktop so its countdown matches the store (navig.store.os_devices).
PAIR_CODE_TTL_S_HINT = 120
MIN_EDGE_VERSION = (1, 3, 0)


def pair_link(edge_url: str, tenant: str, code: str) -> str:
    """The link the desktop shows as a QR. Everything rides in the FRAGMENT, which a
    browser never sends in a request, so os.navig.run never sees the relay or the code;
    the page strips it from the address bar before doing anything else. ``v=1`` lets a
    later page refuse a format it does not understand instead of guessing."""
    from urllib.parse import urlencode

    return f"{HOSTED_APP_URL}#" + urlencode(
        {"pair": "1", "v": "1", "e": edge_url.rstrip("/"), "t": tenant, "c": code}
    )


# The edge route that carries a linked NAVIG OS's deck calls (/os/<tenant>/api/deck/*).
MIN_EDGE_VERSION_LINK = (1, 4, 0)
LINK_CODE_TTL_S_HINT = 600


def brain_link(edge_url: str, tenant: str, code: str) -> str:
    """What the owner pastes into ANOTHER NAVIG OS to make this computer its brain.

    The part before ``#`` is the address the other machine will call (the relay, routed
    to this brain by the tenant); the code rides in the fragment so that opening the link
    in a browser by mistake never sends it anywhere. Single use, ten minutes.
    """
    return f"{edge_url.rstrip('/')}/os/{tenant}#link={code}&v=1"


def _edge_at_least(version: str, minimum: tuple[int, int, int]) -> bool:
    try:
        parts = tuple(int(p) for p in (version or "").strip().split(".")[:3])
    except ValueError:
        return False
    return len(parts) == 3 and parts >= minimum


def edge_supports_os(version: str) -> bool:
    """Is the deployed Lighthouse edge new enough to carry NAVIG OS (1.3.0+)?"""
    return _edge_at_least(version, MIN_EDGE_VERSION)


def edge_supports_link(version: str) -> bool:
    """Can the deployed edge carry a linked NAVIG OS's deck calls (1.4.0+)?"""
    return _edge_at_least(version, MIN_EDGE_VERSION_LINK)


def device_name_from_ua(ua: str) -> str:
    """"Safari on iPhone" from a User-Agent — a label for the device list, nothing more."""
    ua = ua or ""
    if "iPhone" in ua:
        where = "iPhone"
    elif "iPad" in ua:
        where = "iPad"
    elif "Android" in ua:
        where = "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        where = "Mac"
    elif "Windows" in ua:
        where = "Windows"
    elif "Linux" in ua or "CrOS" in ua:
        where = "Linux"
    else:
        where = ""
    if "Edg/" in ua:
        browser = "Edge"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{browser} on {where}" if where else browser

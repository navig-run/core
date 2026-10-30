"""``UplinkClient`` -- the brain's OUTBOUND WebSocket connection to Lighthouse.

This is the zero-tunnel transport. Instead of running an inbound tunnel
(cloudflared) so the world can reach the laptop, the brain dials *out* to its
self-hosted Lighthouse edge (a Cloudflare Worker on the user's own account) and
keeps one persistent WebSocket open. Lighthouse pushes inbound work down that
pipe and reads the replies back:

  edge ──"req" frame──► brain dispatches in-process ──"res" frame──► edge

Three inbound ``kind``s are dispatched:

  * ``deck``     -- forwarded as a loopback HTTP call to the local gateway, so
                    the real route stack (auth, middleware) runs unchanged.
  * ``telegram`` -- handed to ``TelegramChannel.handle_webhook_update`` (which
                    validates the secret token); replies stay direct from the
                    brain, so the bot token never transits the edge.
  * ``sms``      -- replayed as a loopback POST to ``/sms/webhook``.

The brain also pushes ``event`` frames (mirroring ``/api/events`` SSE) and
periodic ``snapshot`` frames up the pipe so the edge can serve a cached status
view + a banner while the brain is asleep.

Modelled on the aiohttp ``ws_connect`` pattern in ``agent/llm_providers.py`` and
the reconnect/watchdog discipline of ``cloud/manager.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Dispatch hooks injected by the gateway (kept explicit for testability).
TelegramHandler = Callable[[dict, str], Awaitable[bool]]
SnapshotProvider = Callable[[], Awaitable[Optional[dict[str, Any]]]]

_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
# A connection must have stayed up at least this long to count as "stable" and reset
# the backoff. A shorter one (a half-open edge that accepts the handshake then
# immediately CLOSEs) keeps the backoff growing, so it can't cause a ~1/sec storm.
_BACKOFF_STABLE_S = 10.0
_CONNECT_TIMEOUT_S = 15.0
# Keep the loopback dispatch comfortably under the edge's 30s reply timeout.
_DISPATCH_TIMEOUT_S = 25.0

# Stamped on every request the uplink replays into the gateway. Kept in sync with
# navig/gateway/deck/auth.py RELAY_VIA_HEADER (pinned by a test) rather than imported,
# so the uplink does not pull the gateway's auth module in at import time.
RELAY_VIA_HEADER = "X-Navig-Via"
# Remote NAVIG OS data waiting to go up the uplink (each item <= 256 KiB, see os_relay).
_BULK_QUEUE_MAX = 64
# Hop-by-hop / length headers we must not echo back through the edge.
_DROP_RESP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


def _note_os_budget_exceeded(usage: dict[str, int]) -> None:
    """Remote NAVIG OS hit its daily message budget: say so where the operator looks."""
    try:
        from navig.core import incidents

        incidents.record(incidents.OS_REMOTE_BUDGET, **usage)
    except Exception:  # noqa: BLE001 — a health note must never break the relay
        logger.debug("os remote budget incident not recorded", exc_info=True)


def api_key_hash(api_key: str) -> str:
    """The tenant id Lighthouse routes on: ``sha256(api_key)`` as lowercase hex."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def _rewrite_ingest_path(path: str) -> str:
    """``/ingest/<tenant>/<source>[?…]`` → loopback ``/api/ingest/<source>[?…]``.

    Lighthouse forwards the opaque public ingest path as ``kind=deck``. The tenant
    segment only routed it to this brain; we strip it so the request lands on the
    gateway's canonical route, where the per-source HMAC (not the tenant) authorises.
    """
    rest = path[len("/ingest/") :]
    parts = rest.split("/", 1)
    if len(parts) != 2 or not parts[1]:
        return path  # malformed — let the gateway 404 it
    return "/api/ingest/" + parts[1]


_OS_PAIR_PATH = re.compile(r"^/os/[0-9a-f]{64}/pair(?:\?.*)?$")


def _rewrite_os_pair_path(path: str) -> str:
    """``/os/<tenant>/pair`` → loopback ``/api/os/pair/redeem`` (the public redeem route).

    Like ingest: the tenant only routed the request here; the one-time pairing code in
    the body is the credential, checked by the gateway route.
    """
    return "/api/os/pair/redeem" if _OS_PAIR_PATH.match(path) else path


_OS_LINK_PATH = re.compile(r"^/os/[0-9a-f]{64}(/api/deck/[^?#]*(?:\?.*)?)$")


def _rewrite_os_path(path: str, headers: dict[str, Any]) -> str:
    """Public ``/os/<tenant>/…`` paths → their loopback route.

    * ``/os/<tenant>/pair`` → ``/api/os/pair/redeem`` (see :func:`_rewrite_os_pair_path`).
    * ``/os/<tenant>/api/deck/<rest>`` → ``/api/deck/<rest>`` — a NAVIG OS on another
      machine using this brain (brain link). Deck auth then checks its Bearer device
      token. ONLY the deck API, and only with a Bearer: the tenant path is keyless, so
      it must never reach the routes that skip deck auth (``/api/events`` holds a stream
      open; ``/api/ingest`` and the redeem route have their own credentials). Anything
      else is left as-is and the gateway answers 404.
    """
    rewritten = _rewrite_os_pair_path(path)
    if rewritten != path:
        return rewritten
    m = _OS_LINK_PATH.match(path)
    if not m:
        return path
    auth = next((str(v) for k, v in headers.items() if str(k).lower() == "authorization"), "")
    if not auth.startswith("Bearer "):
        return path
    target = m.group(1)
    # The loopback client normalises dot segments, so "/api/deck/../events" would land
    # on /api/events after all. Refuse dot segments and encoded separators outright.
    route = target.split("?", 1)[0]
    lowered = route.lower()
    if "\\" in route or any(enc in lowered for enc in ("%2e", "%2f", "%5c")):
        return path
    if any(seg in (".", "..") for seg in route.split("/")):
        return path
    return target


def _now() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UplinkState:
    status: str = "off"  # off | connecting | online | error | stopping
    connected_at: float | None = None
    last_error: str | None = None
    reconnects: int = 0
    requests_served: int = 0


class UplinkClient:
    """Owns one outbound WebSocket to Lighthouse + its dispatch + reconnect loop."""

    def __init__(
        self,
        *,
        lighthouse_url: str,
        api_key: str,
        gateway_port: int,
        gateway_host: str = "127.0.0.1",
        telegram_handler: Optional[TelegramHandler] = None,
        system_events: Any = None,
        snapshot_provider: Optional[SnapshotProvider] = None,
        snapshot_interval_s: float = 30.0,
        version: str = "",
        connectivity_listener: Optional[Callable[[str], None]] = None,
        os_remote_enabled: bool = True,
        os_remote_budget: int | None = None,
    ):
        self.lighthouse_url = lighthouse_url.rstrip("/")
        self.api_key = api_key
        self.gateway_host = gateway_host
        self.gateway_port = int(gateway_port)
        self.telegram_handler = telegram_handler
        self.system_events = system_events
        self.snapshot_provider = snapshot_provider
        self.snapshot_interval_s = max(5.0, float(snapshot_interval_s))
        self.version = version
        self._connectivity_listener = connectivity_listener

        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._run_task: asyncio.Task | None = None
        self._snapshot_task: asyncio.Task | None = None
        self._event_drain_task: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()
        self._event_q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        self._unsub: Callable[[], None] | None = None
        self._stop = False
        self.state = UplinkState()
        # Remote NAVIG OS streams (os.navig.run → edge → here → the local NAVIG OS server).
        # Their data goes through a bounded queue and ONE writer that yields between
        # messages, so a busy stream is capped at one fragment ahead of a Telegram reply,
        # and a slow edge pushes back on the NAVIG OS socket instead of growing memory.
        from navig.cloud.os_relay import DEFAULT_DAILY_MSG_BUDGET, OsRelay

        self._bulk_q: asyncio.Queue[str] = asyncio.Queue(maxsize=_BULK_QUEUE_MAX)
        self._bulk_task: asyncio.Task | None = None
        self.os_relay = OsRelay(
            send_control=self._send,
            send_data=self._send_os_data,
            enabled=os_remote_enabled,
            daily_msg_budget=DEFAULT_DAILY_MSG_BUDGET if os_remote_budget is None else os_remote_budget,
            on_budget_exceeded=_note_os_budget_exceeded,
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        return self.state.status

    @property
    def tenant(self) -> str:
        return api_key_hash(self.api_key)

    def telegram_webhook_url(self) -> str:
        """Public path the brain registers via ``setWebhook`` (opaque per-brain)."""
        return f"{self.lighthouse_url}/tg/{self.tenant}"

    def sms_webhook_url(self) -> str:
        return f"{self.lighthouse_url}/sms/{self.tenant}"

    def ingest_url(self, source: str) -> str:
        """Public, copy-paste URL a website POSTs signed events to for *source*."""
        return f"{self.lighthouse_url}/ingest/{self.tenant}/{source}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.state.status,
            "mode": "lighthouse",
            "lighthouse_url": self.lighthouse_url,
            "tenant": self.tenant,
            "connected_at": self.state.connected_at,
            "last_error": self.state.last_error,
            "reconnects": self.state.reconnects,
            "requests_served": self.state.requests_served,
        }

    async def start(self) -> None:
        if self._run_task is not None:
            return
        if not self.api_key:
            self._mark_error("no_api_key")
            raise RuntimeError("lighthouse uplink needs deck.api_key")
        if not self.lighthouse_url:
            self._mark_error("no_lighthouse_url")
            raise RuntimeError("lighthouse uplink needs cloud.lighthouse_url")
        self._stop = False
        self.state = UplinkState(status="connecting")
        self._run_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        self.state.status = "stopping"
        for task in (self._run_task, self._snapshot_task, self._event_drain_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._run_task, self._snapshot_task, self._event_drain_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._run_task = self._snapshot_task = self._event_drain_task = None
        for task in list(self._inflight):
            task.cancel()
        self._inflight.clear()
        self._unsubscribe_events()
        await self._close_ws()
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self.state = UplinkState(status="off")

    # ── Reconnect loop ───────────────────────────────────────────────────────────

    def _ws_url(self) -> str:
        base = self.lighthouse_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("wss://"):
            pass
        elif base.startswith(("http://", "ws://")):
            # Plaintext. The uplink carries the deck api_key (Authorization header)
            # plus all deck/Telegram/SMS traffic, so plaintext over the internet
            # would leak it. Lighthouse always runs on Cloudflare (TLS), so a
            # non-loopback http URL is almost certainly a typo — force wss and
            # warn. Loopback (local dev / tests) is allowed to stay ws.
            rest = base.split("://", 1)[1]
            host = rest.split("/", 1)[0].split(":", 1)[0].lower()
            is_loopback = host in ("127.0.0.1", "::1", "localhost") or host.endswith(".localhost")
            if is_loopback:
                base = "ws://" + rest if base.startswith("http://") else base
            else:
                logger.warning(
                    "lighthouse_url is plaintext (%s) for a non-loopback host — "
                    "forcing wss:// so the uplink (which carries the api_key) is "
                    "never sent in the clear.",
                    base,
                )
                base = "wss://" + rest
        else:
            # No scheme → assume TLS (Cloudflare). Safer default than plaintext.
            base = "wss://" + base
        return f"{base}/uplink"

    async def _run(self) -> None:
        backoff = _BACKOFF_INITIAL_S
        while not self._stop:
            try:
                await self._connect_and_serve()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._mark_error(repr(exc))
                logger.warning("lighthouse uplink dropped: %r", exc)
            # Reset the backoff ONLY if the connection was actually stable. A
            # half-open edge that accepts the handshake then immediately CLOSEs
            # returns "normally" too — resetting on that produced a ~1/sec
            # reconnect storm. connected_at is set once the WS is up, so its age
            # is how long we were online. Checked on BOTH exits: a long healthy
            # session that ends in an error must reconnect promptly as well.
            connected_at = self.state.connected_at
            if connected_at is not None and (_now() - connected_at) >= _BACKOFF_STABLE_S:
                backoff = _BACKOFF_INITIAL_S
            if self._stop:
                break
            # The connection is down (clean close or error) and we're about to back
            # off and retry. Reflect that honestly: `_connect_and_serve` leaves
            # status pinned "online" and connected_at set after a CLEAN close (the
            # `async for` just ends on a CLOSE frame), so snapshot() and — via #649 —
            # CloudManager.status would report a live uplink over a dead socket for
            # the whole backoff window (which #660 can grow to 30s). Downgrade a
            # still-"online" status to "connecting" (the except path already set
            # "error") and clear connected_at now that the #660 stability check above
            # has consumed it, so "connected since" isn't stale while offline.
            if self.state.status == "online":
                self.state.status = "connecting"
            self.state.connected_at = None
            # Dropped (clean close or error) and about to retry — report the
            # transition; the reporter debounces so brief blips don't notify.
            self._fire_connectivity("offline")
            self.state.reconnects += 1
            # Jitter the wait so many brains don't reconnect in lockstep (thundering herd).
            await asyncio.sleep(backoff * random.uniform(0.5, 1.5))
            backoff = min(backoff * 2, _BACKOFF_MAX_S)

    async def _connect_and_serve(self) -> None:
        session = await self._get_session()
        self.state.status = "connecting"
        self._ws = await session.ws_connect(
            self._ws_url(),
            headers={"Authorization": f"Bearer {self.api_key}"},
            heartbeat=30,
            timeout=aiohttp.ClientTimeout(total=_CONNECT_TIMEOUT_S),
            max_msg_size=16 * 1024 * 1024,
        )
        self.state.status = "online"
        self.state.connected_at = _now()
        self.state.last_error = None
        self._fire_connectivity("online")
        logger.info("lighthouse uplink online: %s", self.lighthouse_url)

        await self._send_hello()
        self._subscribe_events()
        self._event_drain_task = asyncio.create_task(self._event_drain_loop())
        self._snapshot_task = asyncio.create_task(self._snapshot_loop())
        self._bulk_task = asyncio.create_task(self._bulk_writer_loop())

        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._on_frame(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    raise RuntimeError(f"ws error: {self._ws.exception()!r}")
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
        finally:
            for task in (self._snapshot_task, self._event_drain_task, self._bulk_task):
                if task is not None and not task.done():
                    task.cancel()
            self._snapshot_task = self._event_drain_task = self._bulk_task = None
            # Every remote NAVIG OS stream rode this socket; the edge has already closed
            # the browsers (1012), so end ours and drop what was queued for them.
            await self.os_relay.close_all()
            while not self._bulk_q.empty():
                self._bulk_q.get_nowait()
            self._unsubscribe_events()
            await self._close_ws()

    # ── Inbound frame handling ─────────────────────────────────────────────────

    def _on_frame(self, text: str) -> None:
        # Remote NAVIG OS data: `@<sid>:<payload>`, never parsed here (it is the page's
        # business, and parsing a streamed reply per message would be pure overhead).
        if text.startswith("@"):
            if len(text) > 38 and text[37] == ":":
                self.os_relay.on_data(text[1:37], text[38:])
            return
        try:
            frame = json.loads(text)
        except (ValueError, TypeError):
            return
        t = frame.get("t")
        if t == "os_open":
            self.os_relay.on_open(frame)
            return
        if t == "os_close":
            self.os_relay.on_close(str(frame.get("sid") or ""), frame.get("code", 1000), frame.get("reason", ""))
            return
        if t == "req":
            task = asyncio.create_task(self._dispatch(frame))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        elif t == "ping":
            # GC-safe like the req branch: a dropped pong is a missed keepalive,
            # which the broker can read as a dead uplink and tear down.
            task = asyncio.create_task(self._send({"t": "pong"}))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        rid = frame.get("id", "")
        kind = frame.get("kind", "deck")
        try:
            if kind == "telegram":
                status, headers, body = await self._dispatch_telegram(frame)
            elif kind == "sms":
                status, headers, body = await self._dispatch_loopback(frame, "/sms/webhook")
            else:
                path = frame.get("path", "/")
                if path.startswith("/ingest/"):
                    path = _rewrite_ingest_path(path)
                elif path.startswith("/os/"):
                    path = _rewrite_os_path(path, frame.get("headers") or {})
                status, headers, body = await self._dispatch_loopback(frame, path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("uplink dispatch failed (%s): %r", kind, exc)
            status, headers, body = 502, {"content-type": "application/json"}, json.dumps(
                {"error": "dispatch_failed"}
            )
        self.state.requests_served += 1
        await self._send(
            {
                "t": "res",
                "id": rid,
                "status": int(status),
                "headers": {str(k): str(v) for k, v in headers.items()},
                "body": body if isinstance(body, str) else body.decode("utf-8", "replace"),
            }
        )

    async def _dispatch_loopback(
        self, frame: dict[str, Any], path: str
    ) -> tuple[int, dict[str, str], str]:
        session = await self._get_session()
        method = str(frame.get("method", "GET")).upper()
        url = f"http://{self.gateway_host}:{self.gateway_port}{path}"
        headers = {
            k: v
            for k, v in (frame.get("headers") or {}).items()
            if k.lower() not in ("host", "content-length", RELAY_VIA_HEADER.lower())
        }
        # Every relayed request reaches the gateway from 127.0.0.1, so it must be marked
        # as relayed by an explicit rule rather than by whatever Cloudflare headers the
        # edge happened to pass along. Set LAST, over any inbound value, so a caller
        # cannot spoof or strip it. deck/auth.py `_request_is_local` refuses it.
        headers[RELAY_VIA_HEADER] = "lighthouse"
        raw_body = frame.get("body") or ""
        data = raw_body.encode("utf-8") if (raw_body and method not in ("GET", "HEAD")) else None
        async with session.request(
            method,
            url,
            headers=headers,
            data=data,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=_DISPATCH_TIMEOUT_S),
        ) as resp:
            body = await resp.text()
            out_headers = {
                k: v for k, v in resp.headers.items() if k.lower() not in _DROP_RESP_HEADERS
            }
            return resp.status, out_headers, body

    async def _dispatch_telegram(
        self, frame: dict[str, Any]
    ) -> tuple[int, dict[str, str], str]:
        if self.telegram_handler is None:
            return 503, {"content-type": "application/json"}, json.dumps({"error": "telegram_off"})
        try:
            update = json.loads(frame.get("body") or "{}")
        except (ValueError, TypeError):
            return 400, {"content-type": "application/json"}, json.dumps({"error": "bad_update"})
        secret = ""
        for k, v in (frame.get("headers") or {}).items():
            if k.lower() == "x-telegram-bot-api-secret-token":
                secret = v
                break
        ok = await self.telegram_handler(update, secret)
        # Always 200 so Telegram does not retry-storm; the ok flag is advisory.
        return 200, {"content-type": "application/json"}, json.dumps({"ok": bool(ok)})

    # ── Outbound (events + snapshots) ──────────────────────────────────────────

    async def _send(self, frame: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_str(json.dumps(frame))
        except Exception as exc:  # noqa: BLE001
            logger.debug("uplink send failed: %r", exc)

    async def _send_hello(self) -> None:
        snap = await self._collect_snapshot()
        frame: dict[str, Any] = {"t": "hello", "version": self.version, "caps": self.os_relay.caps()}
        if snap is not None:
            frame["snapshot"] = json.dumps(snap)
        await self._send(frame)

    async def _send_os_data(self, sid: str, payload: str) -> None:
        # Awaiting put() is the backpressure: a full queue stalls this stream's reader,
        # which stops reading the NAVIG OS socket, which pushes back on the server.
        await self._bulk_q.put(f"@{sid}:{payload}")

    async def _bulk_writer_loop(self) -> None:
        try:
            while True:
                item = await self._bulk_q.get()
                ws = self._ws
                if ws is None or ws.closed:
                    continue
                try:
                    await ws.send_str(item)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("uplink os data send failed: %r", exc)
                # Let any waiting res/pong/event frame go before the next chunk of data.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            return

    async def send_retire(self) -> bool:
        """Tell the edge this tenant is retired (the deck key is about to rotate).

        Paired devices still point at this tenant; after rotation nothing ever connects
        here again, so without this they would see "computer offline" forever. Returns
        whether the frame was handed to a live socket.
        """
        ws = self._ws
        if ws is None or ws.closed:
            return False
        await self._send({"t": "retire"})
        return True

    def _subscribe_events(self) -> None:
        ev = self.system_events
        if ev is None or not hasattr(ev, "subscribe"):
            return

        def _on_event(evt: Any) -> None:
            try:
                payload = getattr(evt, "payload", {}) or {}
                frame = {
                    "type": getattr(evt, "event_type", "status_update"),
                    "data": payload,
                    "ts": _now_iso(),
                }
                self._event_q.put_nowait(json.dumps(frame))
            except asyncio.QueueFull:
                pass
            except Exception:  # noqa: BLE001
                pass

        try:
            ev.subscribe("*", _on_event)
        except Exception:  # noqa: BLE001
            return

        def _unsub() -> None:
            try:
                subs = getattr(ev, "_wildcard_subscribers", None)
                if subs is not None and _on_event in subs:
                    subs.remove(_on_event)
            except Exception:  # noqa: BLE001
                pass

        self._unsub = _unsub

    def _unsubscribe_events(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _event_drain_loop(self) -> None:
        try:
            while True:
                data = await self._event_q.get()
                # event="message" → deck EventSource onmessage fires, matching
                # the daemon's own /api/events frames.
                await self._send({"t": "event", "event": "message", "data": data})
        except asyncio.CancelledError:
            return

    async def _snapshot_loop(self) -> None:
        if self.snapshot_provider is None:
            return
        try:
            while True:
                await asyncio.sleep(self.snapshot_interval_s)
                snap = await self._collect_snapshot()
                if snap is not None:
                    await self._send({"t": "snapshot", "data": json.dumps(snap)})
        except asyncio.CancelledError:
            return

    async def _collect_snapshot(self) -> Optional[dict[str, Any]]:
        if self.snapshot_provider is None:
            return None
        try:
            return await self.snapshot_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("snapshot provider failed: %r", exc)
            return None

    # ── Lifecycle helpers ──────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _close_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _mark_error(self, reason: str) -> None:
        self.state.status = "error"
        self.state.last_error = reason

    def _fire_connectivity(self, status: str) -> None:
        cb = self._connectivity_listener
        if cb is None:
            return
        try:
            cb(status)  # runs in the loop thread → safe to create tasks
        except Exception:  # noqa: BLE001
            logger.debug("connectivity listener failed", exc_info=True)

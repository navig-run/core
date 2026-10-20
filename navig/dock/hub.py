"""The daemon side of the Dock bridge: one live Dock socket, correlated calls.

Lives in the gateway process. ``call()`` is what ``POST /dock/call`` (and so every MCP
``dock_*`` tool) awaits: it validates the request, sends one ``call`` frame, and waits
for the matching ``result`` under a deadline. Nothing here polls — the socket carries
results back the moment the Dock finishes.

Guarantees:

* **Bounded** — at most ``MAX_IN_FLIGHT`` calls wait at once; the next one is ``busy``.
* **No duplicates** — ids are random and never re-sent. A call still waiting when the
  socket drops (or a newer Dock supersedes this one) fails as ``disconnected`` with
  ``effects="uncertain"`` for mutations, so the caller re-reads the page before acting.
* **Cancel** — a deadline, an explicit cancel, or the HTTP caller going away sends a
  ``cancel`` frame so the Dock aborts the work in progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from navig.dock.protocol import (
    CLOSE_SUPERSEDED,
    MAX_IN_FLIGHT,
    PROTOCOL_VERSION,
    TOOLS,
    DockCallError,
    deadline_ms,
    validate_call,
)
from navig.dock.protocol import (
    call_id as caller_call_id,
)

logger = logging.getLogger(__name__)


@dataclass
class _Pending:
    future: asyncio.Future
    tool: str
    mutating: bool
    started: float = field(default_factory=time.perf_counter)


class DockBridge:
    def __init__(self) -> None:
        self._ws: Any = None
        self._session = ""
        self._info: dict[str, Any] = {}
        self._pending: dict[str, _Pending] = {}
        self._paused = False
        self._connected_at = 0.0
        self._last_seen = 0.0
        self._rtt_ms: deque[float] = deque(maxlen=200)

    # ── connection ───────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._ws is not None and not getattr(self._ws, "closed", False)

    async def attach(self, ws: Any, info: dict[str, Any]) -> str:
        """Make *ws* the live Dock. A previous socket is closed as superseded."""
        old = self._ws
        self._ws = ws
        self._session = secrets.token_hex(8)
        self._info = dict(info)
        self._paused = bool(info.get("paused"))
        self._connected_at = self._last_seen = time.time()
        if old is not None and old is not ws:
            self._fail_pending("disconnected", "a newer Dock connection replaced this one")
            try:
                await old.close(code=CLOSE_SUPERSEDED, message=b"superseded")
            except Exception:  # noqa: BLE001 — the old socket may already be gone
                pass
        logger.info("dock bridge: connected (Dock %s)", self._info.get("version", "?"))
        return self._session

    def detach(self, ws: Any) -> None:
        if ws is not self._ws:
            return
        self._ws = None
        self._fail_pending("disconnected", "the Dock disconnected")
        logger.info("dock bridge: disconnected")

    def _fail_pending(self, category: str, message: str) -> None:
        for pid, p in list(self._pending.items()):
            if not p.future.done():
                effects = "uncertain" if p.mutating else "none"
                p.future.set_exception(DockCallError(category, message, effects=effects))
            self._pending.pop(pid, None)

    # ── frames from the Dock ─────────────────────────────────────────────

    def on_frame(self, frame: Any) -> dict[str, Any] | None:
        """Handle one decoded frame. Returns a frame to send back, if any."""
        self._last_seen = time.time()
        if not isinstance(frame, dict):
            return None
        kind = frame.get("t")
        if kind == "result":
            pending = self._pending.get(str(frame.get("id") or ""))
            if pending is None or pending.future.done():
                return None  # late result for a call that already timed out / was cancelled
            if frame.get("ok"):
                pending.future.set_result({"data": frame.get("data"), "timing": frame.get("timing") or {}})
            else:
                err = frame.get("error") if isinstance(frame.get("error"), dict) else {}
                pending.future.set_exception(DockCallError(
                    str(err.get("category") or "internal"), str(err.get("message") or "the Dock reported an error"),
                    effects=str(err.get("effects") or ("uncertain" if pending.mutating else "none")),
                    data=err.get("data"),
                ))
            return None
        if kind == "ping":
            return {"t": "pong", "ts": frame.get("ts")}
        if kind == "pong" and isinstance(frame.get("ts"), (int, float)):
            self._rtt_ms.append(max(0.0, time.time() * 1000 - float(frame["ts"])))
            return None
        if kind == "event":
            ev = frame.get("kind")
            if ev in ("stopped", "paused"):
                self._paused = True
                self._fail_pending("stopped", "the user pressed Stop in the browser")
            elif ev == "resumed":
                self._paused = False
            return None
        return None

    # ── calls from agents ────────────────────────────────────────────────

    async def call(self, tool: str, args: Any = None, *, deadline: Any = None,
                   actor: str = "", cid: Any = None, session: str = "") -> dict[str, Any]:
        """Run *tool* in the Dock. Returns ``{"data", "timing"}`` or raises DockCallError."""
        clean = validate_call(tool, args)
        if not self.connected:
            raise DockCallError("unavailable", "NAVIG Dock is not connected — open Chrome with the Dock and turn on "
                                "Settings → NAVIG daemon → Let AI agents use this browser.")
        if self._paused:
            raise DockCallError("unavailable", "The user paused agent control in the browser (Stop). "
                                "Ask them before continuing; they can resume it from the Dock.")
        if len(self._pending) >= MAX_IN_FLIGHT:
            raise DockCallError("busy", f"too many browser calls in flight (max {MAX_IN_FLIGHT})")
        ms = deadline_ms(deadline)
        call_id = caller_call_id(cid)
        if call_id is None or call_id in self._pending:
            call_id = secrets.token_hex(8)
        loop = asyncio.get_running_loop()
        pending = _Pending(loop.create_future(), tool, TOOLS[tool])
        self._pending[call_id] = pending
        ws = self._ws
        try:
            await ws.send_str(json.dumps({"t": "call", "id": call_id, "tool": tool, "args": clean,
                                          "deadline_ms": ms, "actor": actor[:60], "session": session[:40]},
                                         separators=(",", ":")))
            out = await asyncio.wait_for(asyncio.shield(pending.future), ms / 1000)
            out.setdefault("timing", {})["hub_ms"] = round((time.perf_counter() - pending.started) * 1000, 2)
            return out
        except asyncio.TimeoutError:
            await self._send_cancel(ws, call_id)
            raise DockCallError("timeout", f"{tool} did not finish within {ms} ms",
                                effects="uncertain" if pending.mutating else "none") from None
        except asyncio.CancelledError:
            await self._send_cancel(ws, call_id)
            raise
        except (ConnectionError, RuntimeError) as exc:  # send on a closing socket
            raise DockCallError("disconnected", f"the Dock connection failed: {exc}") from None
        finally:
            self._pending.pop(call_id, None)

    async def cancel(self, call_id: str) -> bool:
        pending = self._pending.get(call_id)
        if pending is None:
            return False
        await self._send_cancel(self._ws, call_id)
        if not pending.future.done():
            pending.future.set_exception(DockCallError("stopped", "cancelled by the caller",
                                                       effects="uncertain" if pending.mutating else "none"))
        return True

    @staticmethod
    async def _send_cancel(ws: Any, call_id: str) -> None:
        if ws is None or getattr(ws, "closed", True):
            return
        try:
            await ws.send_str(json.dumps({"t": "cancel", "id": call_id}))
        except Exception:  # noqa: BLE001 — best effort; the Dock also enforces the deadline
            pass

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        rtts = sorted(self._rtt_ms)
        p = (lambda q: round(rtts[min(len(rtts) - 1, int(q * len(rtts)))], 2)) if rtts else (lambda q: None)
        return {
            "protocol": PROTOCOL_VERSION,
            "connected": self.connected,
            "paused": self._paused,
            "session": self._session if self.connected else "",
            "dock_version": self._info.get("version", ""),
            "browser": self._info.get("browser", ""),
            "caps": self._info.get("caps", []),
            "connected_at": self._connected_at if self.connected else None,
            "last_seen": self._last_seen or None,
            "in_flight": len(self._pending),
            "ping_ms": {"p50": p(0.5), "p95": p(0.95), "samples": len(rtts)},
        }


_bridge: DockBridge | None = None


def get_bridge() -> DockBridge:
    """The process's bridge (one gateway process ↔ one Dock)."""
    global _bridge
    if _bridge is None:
        _bridge = DockBridge()
    return _bridge


def reset_bridge() -> None:
    """Tests only: forget the process bridge."""
    global _bridge
    _bridge = None

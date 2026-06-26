"""
navig.requests.registry — in-memory registry for user-facing requests
(questions + route confirmations + operator proposals).

Mirrors ApprovalManager's asyncio-future design so the two can be merged into a
single deck endpoint:

  * ``ask(...)``    — blocking producer; an agent awaits the user's answer.
  * ``create(...)`` — non-blocking producer; registers a pending request with an
                      optional ``on_answer`` callback that fires when the deck
                      responds. Used for route confirmations and the header
                      "ask navig for next action" proposal.
  * ``answer(...)`` — consumer; resolves a request (deck POST /respond).
  * ``get_pending()`` — list pending requests as unified dicts.

Auto-dispatch: when the user enables ``ai.auto_dispatch`` and a request is both
high-priority and safe, ``create()``/``ask()`` resolve it immediately (running
the callback / returning an approval) and emit a non-blocking notice instead of
parking it for a human. See navig.requests.autodispatch.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from navig.requests.autodispatch import should_auto_dispatch

logger = logging.getLogger("navig.requests")

# Answer payload handed to on_answer callbacks / returned from ask().
Answer = dict[str, Any]


def _auto_dispatch_enabled() -> bool:
    """Best-effort read of the ai.auto_dispatch runtime flag."""
    try:
        from navig.config import get_config_manager

        cfg = get_config_manager()
        ai_cfg = (cfg.get("ai") or {}) if cfg else {}
        return bool(ai_cfg.get("auto_dispatch", False))
    except Exception:
        return False


@dataclass
class UserRequest:
    """A pending decision navig needs from the user."""

    id: str
    kind: str  # "question" | "route" | "plan"
    title: str
    body: str = ""
    options: list[dict] = field(default_factory=list)
    allow_custom: bool = True
    allow_multi: bool = False
    source: str = "agent"
    priority: str = "normal"  # low | normal | important | critical
    level: str | None = None
    command: str | None = None  # for auto-dispatch matching (optional)
    # Structured data for rich request kinds (e.g. "plan" carries
    # {summary, steps[...]}). Emitted only when present.
    payload: dict | None = None
    auto_dispatched: bool = False
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None

    def to_dict(self) -> dict:
        """Emit the unified shape the deck consumes (camelCase booleans)."""
        d = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "options": self.options,
            "allowCustom": self.allow_custom,
            "allowMulti": self.allow_multi,
            "level": self.level,
            "priority": self.priority,
            "auto_dispatched": self.auto_dispatched,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status,
        }
        if self.payload is not None:
            d["payload"] = self.payload
        return d


class RequestRegistry:
    """Thread-confined (single event loop) registry of pending user requests."""

    DEFAULT_TIMEOUT = 300  # seconds

    def __init__(self) -> None:
        self._pending: dict[str, UserRequest] = {}
        self._futures: dict[str, asyncio.Future] = {}
        self._callbacks: dict[str, Callable[[Answer], Any]] = {}
        self._on_request: list[Callable] = []
        self._cleanup_task: asyncio.Task | None = None

    # ── lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("RequestRegistry started")

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        for fut in self._futures.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self._futures.clear()
        self._callbacks.clear()

    def on_request(self, callback: Callable) -> None:
        """Register a listener fired when a request is created or auto-dispatched.

        Used to push a `requests_update` SSE frame so the deck pops a toast.
        """
        self._on_request.append(callback)

    # ── producers ────────────────────────────────────────────────

    async def ask(
        self,
        *,
        title: str,
        body: str = "",
        options: list[dict] | None = None,
        allow_custom: bool = True,
        allow_multi: bool = False,
        kind: str = "question",
        source: str = "agent",
        priority: str = "normal",
        level: str | None = None,
        command: str | None = None,
        payload: dict | None = None,
        timeout: int | None = None,
    ) -> Answer | None:
        """Block until the deck answers, then return ``{choice, custom}``.

        Returns None on timeout. Auto-dispatched requests resolve immediately
        with ``{"auto": True}``.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        req = self._build(
            kind=kind, title=title, body=body, options=options or [],
            allow_custom=allow_custom, allow_multi=allow_multi, source=source,
            priority=priority, level=level, command=command, payload=payload,
            timeout=timeout,
        )

        if self._maybe_auto_dispatch(req):
            return {"auto": True}

        self._pending[req.id] = req
        self._futures[req.id] = future
        await self._notify(req)
        try:
            return await asyncio.wait_for(future, timeout=req_timeout(req))
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            return None
        finally:
            self._pending.pop(req.id, None)
            self._futures.pop(req.id, None)

    async def create(
        self,
        *,
        title: str,
        body: str = "",
        options: list[dict] | None = None,
        allow_custom: bool = False,
        allow_multi: bool = False,
        kind: str = "question",
        source: str = "agent",
        priority: str = "normal",
        level: str | None = None,
        command: str | None = None,
        payload: dict | None = None,
        on_answer: Callable[[Answer], Awaitable[Any] | Any] | None = None,
        timeout: int | None = None,
    ) -> str:
        """Register a non-blocking request; return its id.

        When the user responds, ``on_answer(answer)`` runs. If auto-dispatch
        applies, the callback runs immediately with ``{"auto": True}`` and the
        request is surfaced as a passive "navig handled this" notice.
        """
        req = self._build(
            kind=kind, title=title, body=body, options=options or [],
            allow_custom=allow_custom, allow_multi=allow_multi, source=source,
            priority=priority, level=level, command=command, payload=payload,
            timeout=timeout,
        )
        if on_answer is not None:
            self._callbacks[req.id] = on_answer

        if self._maybe_auto_dispatch(req):
            await self._run_callback(req.id, {"auto": True}, pop=True)
            await self._notify(req)  # passive "handled" notice
            return req.id

        self._pending[req.id] = req
        await self._notify(req)
        return req.id

    # ── consumer ─────────────────────────────────────────────────

    async def answer(
        self,
        request_id: str,
        *,
        choice: str | list[str] | None = None,
        custom: str | None = None,
    ) -> bool:
        """Resolve a pending request. Returns True if it was found."""
        req = self._pending.get(request_id)
        if not req:
            return False
        req.status = "answered"
        payload: Answer = {"choice": choice, "custom": custom}

        fut = self._futures.get(request_id)
        if fut and not fut.done():
            fut.set_result(payload)

        await self._run_callback(request_id, payload, pop=True)
        self._pending.pop(request_id, None)
        self._futures.pop(request_id, None)
        logger.info("Request %s answered", request_id)
        return True

    def get_pending(self) -> list[dict]:
        return [r.to_dict() for r in self._pending.values()]

    def get(self, request_id: str) -> dict | None:
        req = self._pending.get(request_id)
        return req.to_dict() if req else None

    async def replace_pending_by_source(self, source: str) -> int:
        """Drop pending requests from ``source`` so a re-trigger replaces rather
        than stacks (e.g. clicking RUN repeatedly). Returns the count removed.

        Only safe for ``create``-based requests (callback-only). Cancelling a
        ``ask()`` awaiter parked under the same source would strand the caller,
        so keep blocking producers on a distinct source.
        """
        victims = [rid for rid, r in self._pending.items() if r.source == source]
        for rid in victims:
            self._pending.pop(rid, None)
            self._callbacks.pop(rid, None)
            fut = self._futures.pop(rid, None)
            if fut and not fut.done():
                fut.cancel()
        if victims:
            logger.info("Replaced %d pending request(s) from source=%s", len(victims), source)
        return len(victims)

    # ── internals ────────────────────────────────────────────────

    def _build(self, *, timeout: int | None, **kw) -> UserRequest:
        rid = uuid.uuid4().hex[:8]
        if timeout is not None and timeout <= 0:
            # Persistent request: never auto-expired by the cleanup loop. Used by
            # user-facing asks (e.g. the operator plan) that must survive past the
            # default 5-minute TTL and repeated navigation.
            expires_at = None
        else:
            ttl = timeout if timeout is not None else self.DEFAULT_TIMEOUT
            expires_at = datetime.now() + timedelta(seconds=ttl)
        return UserRequest(id=rid, expires_at=expires_at, **kw)

    def _maybe_auto_dispatch(self, req: UserRequest) -> bool:
        if should_auto_dispatch(req.to_dict(), enabled=_auto_dispatch_enabled()):
            req.auto_dispatched = True
            req.status = "auto_dispatched"
            logger.info("Auto-dispatched request %s (%s)", req.id, req.title)
            return True
        return False

    async def _run_callback(self, request_id: str, answer: Answer, *, pop: bool) -> None:
        cb = self._callbacks.pop(request_id, None) if pop else self._callbacks.get(request_id)
        if cb is None:
            return
        try:
            res = cb(answer)
            if inspect.isawaitable(res):
                await res
        except Exception:
            logger.exception("Request callback failed for %s", request_id)

    async def _notify(self, req: UserRequest) -> None:
        for cb in self._on_request:
            try:
                res = cb(req)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                logger.exception("Request notify callback failed")

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
                now = datetime.now()
                expired = [
                    rid for rid, r in self._pending.items()
                    if r.expires_at and r.expires_at < now
                ]
                for rid in expired:
                    self._pending.pop(rid, None)
                    fut = self._futures.pop(rid, None)
                    if fut and not fut.done():
                        fut.set_result(None)
                    self._callbacks.pop(rid, None)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Request cleanup error")


def req_timeout(req: UserRequest) -> float:
    """Seconds until *req* expires (floor 1s)."""
    if not req.expires_at:
        return RequestRegistry.DEFAULT_TIMEOUT
    return max(1.0, (req.expires_at - datetime.now()).total_seconds())

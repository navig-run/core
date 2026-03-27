"""
navig.engine.hooks — Observable execution event system.

Publish/subscribe hooks fired before and after every tool execution.
Observers register async or sync callables; they are isolated — a failing
observer never disrupts the tool run.

Usage
-----
    from navig.engine.hooks import ExecutionHooks, HookPhase

    hooks = ExecutionHooks()

    @hooks.on(HookPhase.BEFORE)
    async def log_before(event):
        print(f"→ {event.tool_name}")

    @hooks.on(HookPhase.AFTER)
    async def log_after(event):
        print(f"← {event.tool_name} {event.elapsed_ms:.0f}ms ok={event.success}")

    # Thread-safe global instance used by ToolRegistry
    from navig.engine.hooks import global_hooks
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union

logger = logging.getLogger(__name__)


class HookPhase(str, Enum):
    """Lifecycle phases for an execution hook."""

    BEFORE = "before"  # fired before tool.run(); result not yet available
    AFTER = "after"  # fired after tool.run(); result available
    ERROR = "error"  # fired when tool.run() raises (unexpected; should not happen)
    STATUS = "status"  # fired for each on_status callback emitted during a run


@dataclass
class ExecutionEvent:
    """Immutable snapshot passed to every registered hook handler."""

    tool_name: str
    phase: HookPhase
    args: dict[str, Any] = field(default_factory=dict)

    # Populated after the run completes (AFTER / ERROR phases)
    success: bool | None = None
    output: Any = None
    error: str | None = None
    elapsed_ms: float = 0.0

    # STATUS phase fields
    status_step: str = ""
    status_detail: str = ""
    status_progress: int = 0

    # Arbitrary metadata that callers may attach
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def before(cls, tool_name: str, args: dict[str, Any]) -> ExecutionEvent:
        return cls(tool_name=tool_name, phase=HookPhase.BEFORE, args=args)

    @classmethod
    def after(
        cls,
        tool_name: str,
        args: dict[str, Any],
        *,
        success: bool,
        output: Any,
        error: str | None,
        elapsed_ms: float,
    ) -> ExecutionEvent:
        return cls(
            tool_name=tool_name,
            phase=HookPhase.AFTER,
            args=args,
            success=success,
            output=output,
            error=error,
            elapsed_ms=elapsed_ms,
        )

    @classmethod
    def status(
        cls,
        tool_name: str,
        step: str,
        detail: str = "",
        progress: int = 0,
    ) -> ExecutionEvent:
        return cls(
            tool_name=tool_name,
            phase=HookPhase.STATUS,
            status_step=step,
            status_detail=detail,
            status_progress=progress,
        )


# Type alias for a hook handler
HookHandler = Union[
    Callable[[ExecutionEvent], None],
    Callable[[ExecutionEvent], Awaitable[None]],
]


class ExecutionHooks:
    """Central publish-subscribe registry for execution lifecycle events.

    Observers are isolated — exceptions in a handler are logged and swallowed
    so they never affect the tool run.

    Thread safety: registrations must happen before concurrent calls to .emit().
    """

    def __init__(self) -> None:
        self._handlers: dict[HookPhase, list[HookHandler]] = {
            phase: [] for phase in HookPhase
        }

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def on(self, phase: HookPhase) -> Callable[[HookHandler], HookHandler]:
        """Decorator / callable to register a handler for *phase*.

        Works for both sync and async functions::

            @hooks.on(HookPhase.AFTER)
            async def record(event): ...

            hooks.on(HookPhase.BEFORE)(sync_fn)
        """

        def decorator(fn: HookHandler) -> HookHandler:
            self._handlers[phase].append(fn)
            return fn

        return decorator

    def register(self, phase: HookPhase, fn: HookHandler) -> None:
        """Imperative alternative to the ``@hooks.on(phase)`` decorator."""
        self._handlers[phase].append(fn)

    def clear(self, phase: HookPhase | None = None) -> None:
        """Remove all handlers for *phase*, or all handlers if phase is None."""
        if phase is None:
            for p in HookPhase:
                self._handlers[p].clear()
        else:
            self._handlers[phase].clear()

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    async def emit(self, event: ExecutionEvent) -> None:
        """Fire all handlers registered for event.phase (isolated)."""
        handlers = self._handlers.get(event.phase, [])
        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as exc:
                logger.debug(
                    "ExecutionHooks: handler %s raised for phase %s: %s",
                    getattr(handler, "__name__", repr(handler)),
                    event.phase,
                    exc,
                )

    def emit_sync(self, event: ExecutionEvent) -> None:
        """Fire sync handlers only (for contexts where no event loop is running)."""
        handlers = self._handlers.get(event.phase, [])
        for handler in handlers:
            if inspect.iscoroutinefunction(handler):
                continue
            try:
                handler(event)
            except Exception as exc:
                logger.debug(
                    "ExecutionHooks: sync handler %s raised: %s",
                    getattr(handler, "__name__", repr(handler)),
                    exc,
                )

    # ------------------------------------------------------------------
    # Context manager / timing helper
    # ------------------------------------------------------------------

    async def instrument(
        self,
        tool_name: str,
        args: dict[str, Any],
        coro: Awaitable[Any],
    ) -> Any:
        """Wrap an awaitable, firing BEFORE and AFTER hooks automatically.

        Returns the result of *coro*.  If *coro* raises, an ERROR event is
        emitted and the exception is re-raised.
        """
        await self.emit(ExecutionEvent.before(tool_name, args))
        t0 = time.monotonic()
        try:
            result = await coro
            elapsed = (time.monotonic() - t0) * 1000
            await self.emit(
                ExecutionEvent.after(
                    tool_name,
                    args,
                    success=getattr(result, "success", True),
                    output=getattr(result, "output", result),
                    error=getattr(result, "error", None),
                    elapsed_ms=elapsed,
                )
            )
            return result
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            await self.emit(
                ExecutionEvent(
                    tool_name=tool_name,
                    phase=HookPhase.ERROR,
                    args=args,
                    success=False,
                    error=str(exc),
                    elapsed_ms=elapsed,
                )
            )
            raise


# ---------------------------------------------------------------------------
# Module-level singleton used by ToolRegistry by default
# ---------------------------------------------------------------------------
global_hooks: ExecutionHooks = ExecutionHooks()

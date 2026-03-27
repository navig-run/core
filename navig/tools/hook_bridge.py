"""
navig.tools.hook_bridge — Bridge between the two hook systems.

NAVIG has two independent hook registries:

* ``navig.tools.hooks.HookRegistry`` — synchronous, fires on ToolRouter events
* ``navig.engine.hooks.ExecutionHooks`` — async, fires on engine pipeline events

The bridge connects them so that a single subscription to ``global_hooks``
(the engine side) receives events from *both* systems.  Consumers that want
full observability register on ``global_hooks``; they don't need to know
about the ToolRouter-level hooks.

Wire once at startup::

    from navig.tools.hook_bridge import ToolHookBridge

    ToolHookBridge.wire()   # uses singletons; safe to call multiple times

After wiring every ``ToolEvent.AFTER_EXECUTE`` fired by the ToolRouter is
re-emitted as ``ExecutionEvent`` (AFTER phase) on the engine's global_hooks,
and every ``ToolEvent.BEFORE_EXECUTE`` is re-emitted as BEFORE phase.

Error and Denied events are bridged to the ERROR phase with an appropriate
``error`` field set.
"""

from __future__ import annotations

import asyncio
import logging

from navig.engine.hooks import ExecutionEvent, HookPhase, global_hooks
from navig.tools.hooks import ToolEvent, ToolExecutionEvent, get_hook_registry

logger = logging.getLogger("navig.tools.hook_bridge")

_WIRED: bool = False


def _fire_engine_event(event: ExecutionEvent) -> None:
    """Schedule an async engine hook emission from a sync tool hook callback."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(global_hooks.emit(event))
    except RuntimeError:
        # No running event loop (sync context) — run directly
        try:
            asyncio.run(global_hooks.emit(event))
        except Exception as exc:
            logger.debug("hook_bridge: failed to emit engine event: %s", exc)


def _make_before_handler():
    def _before(ev: ToolExecutionEvent) -> None:
        engine_event = ExecutionEvent.before(
            tool_name=ev.tool,
            args=ev.parameters,
        )
        _fire_engine_event(engine_event)

    return _before


def _make_after_handler():
    def _after(ev: ToolExecutionEvent) -> None:
        engine_event = ExecutionEvent.after(
            tool_name=ev.tool,
            args=ev.parameters,
            success=True,
            output=ev.output,
            elapsed_ms=ev.elapsed_ms,
        )
        _fire_engine_event(engine_event)

    return _after


def _make_error_handler():
    def _error(ev: ToolExecutionEvent) -> None:
        engine_event = ExecutionEvent(
            tool_name=ev.tool,
            phase=HookPhase.ERROR,
            args=ev.parameters,
            success=False,
            error=ev.error or "unknown error",
            elapsed_ms=ev.elapsed_ms,
        )
        _fire_engine_event(engine_event)

    return _error


def _make_denied_handler():
    def _denied(ev: ToolExecutionEvent) -> None:
        engine_event = ExecutionEvent(
            tool_name=ev.tool,
            phase=HookPhase.ERROR,
            args=ev.parameters,
            success=False,
            error=f"DENIED: {ev.error or 'blocked by policy'}",
            elapsed_ms=0.0,
        )
        _fire_engine_event(engine_event)

    return _denied


class ToolHookBridge:
    """
    One-shot wiring of ToolRouter hooks → engine global_hooks.

    Calling ``wire()`` more than once is safe; subsequent calls are no-ops.
    """

    @staticmethod
    def wire(
        tool_registry=None,
        engine_hooks=None,
    ) -> None:
        """
        Subscribe bridge handlers on the tool hook registry.

        Args:
            tool_registry:  Alternative HookRegistry (defaults to singleton).
            engine_hooks:   Alternative ExecutionHooks (defaults to global_hooks).
        """
        global _WIRED
        if _WIRED:
            return

        reg = tool_registry or get_hook_registry()

        reg.register(ToolEvent.BEFORE_EXECUTE, _make_before_handler())
        reg.register(ToolEvent.AFTER_EXECUTE, _make_after_handler())
        reg.register(ToolEvent.ERROR, _make_error_handler())
        reg.register(ToolEvent.DENIED, _make_denied_handler())

        _WIRED = True
        logger.debug("hook_bridge: wired tool hooks → engine global_hooks")

    @staticmethod
    def unwire() -> None:
        """Reset wired state (used in tests)."""
        global _WIRED
        _WIRED = False

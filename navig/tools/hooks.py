"""
Tool Execution Hooks — Observable lifecycle events for tool calls.

Provides a lightweight publish/subscribe hook system that fires synchronously
at key points in the ToolRouter execution pipeline:

    BEFORE_EXECUTE  — right before the handler is invoked
    AFTER_EXECUTE   — handler returned successfully
    DENIED          — call blocked by safety policy
    ERROR           — handler raised or returned an error status
    NOT_FOUND       — tool name could not be resolved

Usage::

    from navig.tools.hooks import get_hook_registry, ToolEvent

    registry = get_hook_registry()

    @registry.on(ToolEvent.AFTER_EXECUTE)
    def audit(event):
        print(f"{event.tool} finished in {event.elapsed_ms:.1f}ms → {event.status}")

    # Or register directly:
    registry.register(ToolEvent.DENIED, my_alert_handler)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("navig.tools.hooks")


# =============================================================================
# ToolEvent — lifecycle point
# =============================================================================

class ToolEvent(str, Enum):
    """Lifecycle stages emitted by ToolRouter."""
    BEFORE_EXECUTE = "before_execute"
    AFTER_EXECUTE  = "after_execute"
    DENIED         = "denied"
    ERROR          = "error"
    NOT_FOUND      = "not_found"


# =============================================================================
# ToolExecutionEvent — payload carried by each hook fire
# =============================================================================

@dataclass
class ToolExecutionEvent:
    """
    Payload delivered to every hook callback.

    All fields are always present; fields that are not meaningful for a
    particular lifecycle point are set to their default (None / 0 / "").
    """
    event: ToolEvent
    tool: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: str = ""
    output: Any = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


# Type alias for a hook callback
HookCallback = Callable[[ToolExecutionEvent], None]


# =============================================================================
# HookRegistry — stores and fires callbacks per event
# =============================================================================

class HookRegistry:
    """
    Stores per-event hook callbacks and fires them synchronously.

    Callbacks are invoked in registration order.  A failing callback is
    caught and logged — it NEVER propagates to the ToolRouter caller.
    """

    def __init__(self) -> None:
        self._hooks: Dict[ToolEvent, List[HookCallback]] = {e: [] for e in ToolEvent}

    # -- Registration ---------------------------------------------------------

    def on(self, event: ToolEvent) -> Callable[[HookCallback], HookCallback]:
        """Decorator: register *fn* for *event*.

        Usage::

            @hooks.on(ToolEvent.AFTER_EXECUTE)
            def my_hook(ev):
                ...
        """
        def _decorator(fn: HookCallback) -> HookCallback:
            self.register(event, fn)
            return fn
        return _decorator

    def register(self, event: ToolEvent, callback: HookCallback) -> None:
        """Register *callback* to be called when *event* fires."""
        self._hooks[event].append(callback)
        logger.debug("Hook registered: %s → %s", event.value, getattr(callback, "__name__", repr(callback)))

    def unregister(self, event: ToolEvent, callback: HookCallback) -> bool:
        """Remove *callback* from *event*.  Returns True if it was present."""
        try:
            self._hooks[event].remove(callback)
            return True
        except ValueError:
            return False

    # -- Firing ---------------------------------------------------------------

    def fire(self, event: ToolEvent, **kwargs: Any) -> None:
        """
        Construct a ToolExecutionEvent from *kwargs* and invoke all callbacks.

        Always safe to call — never raises, never awaits.
        Unrecognised kwargs are folded into ``metadata``.
        """
        known_fields = {"tool", "parameters", "status", "output", "error", "elapsed_ms", "metadata"}
        ev_kwargs: Dict[str, Any] = {"event": event}
        extra: Dict[str, Any] = {}

        for k, v in kwargs.items():
            if k in known_fields:
                ev_kwargs[k] = v
            else:
                extra[k] = v

        if extra:
            ev_kwargs.setdefault("metadata", {})
            ev_kwargs["metadata"].update(extra)

        payload = ToolExecutionEvent(**ev_kwargs)
        callbacks = self._hooks.get(event, [])
        for cb in callbacks:
            try:
                cb(payload)
            except Exception as exc:
                logger.warning(
                    "Hook callback %s raised for event %s: %s",
                    getattr(cb, "__name__", repr(cb)),
                    event.value,
                    exc,
                )

    # -- Introspection --------------------------------------------------------

    def hook_count(self, event: Optional[ToolEvent] = None) -> int:
        """Return number of registered hooks (optionally filtered by event)."""
        if event is not None:
            return len(self._hooks[event])
        return sum(len(v) for v in self._hooks.values())

    def clear(self, event: Optional[ToolEvent] = None) -> None:
        """Remove all hooks (optionally scoped to *event*)."""
        if event is not None:
            self._hooks[event].clear()
        else:
            for lst in self._hooks.values():
                lst.clear()


# =============================================================================
# Global singleton
# =============================================================================

_hook_registry: Optional[HookRegistry] = None


def get_hook_registry() -> HookRegistry:
    """Return the global HookRegistry singleton."""
    global _hook_registry
    if _hook_registry is None:
        _hook_registry = HookRegistry()
    return _hook_registry


def reset_hook_registry() -> None:
    """Reset the global singleton (for testing)."""
    global _hook_registry
    _hook_registry = None

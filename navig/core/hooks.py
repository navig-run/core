"""
Hook System for NAVIG.

Provides an extensible event-driven hook system for NAVIG events such as
command processing, session lifecycle, and plugin integration.

Usage:
    from navig.core.hooks import register_hook, trigger_hook, HookEvent

    @register_hook("command:before_execute")
    async def validate_command(event: HookEvent) -> None:
        if event.context.get("command") == "dangerous":
            event.cancel = True
            event.messages.append("Blocked dangerous command")

    event = await trigger_hook("command:before_execute", context={
        "command": command_name,
        "args": args,
    })
    if event.cancel:
        return
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

logger = logging.getLogger("navig.hooks")

HookHandler = Callable[["HookEvent"], None | Coroutine[Any, Any, None]]
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Predefined event types (for documentation / discoverability)
# ---------------------------------------------------------------------------

HOOK_EVENT_TYPES: dict[str, str] = {
    "command": "General command events",
    "command:before_execute": "Before a command is executed",
    "command:after_execute": "After a command completes",
    "command:error": "When a command fails",
    "session": "General session events",
    "session:start": "When a session starts",
    "session:end": "When a session ends",
    "session:context_update": "When session context is updated",
    "agent": "General agent events",
    "agent:bootstrap": "When an agent is bootstrapped",
    "agent:message": "When an agent receives a message",
    "agent:response": "When an agent sends a response",
    "agent:tool_call": "When an agent makes a tool call",
    "plugin": "General plugin events",
    "plugin:load": "When a plugin is loaded",
    "plugin:unload": "When a plugin is unloaded",
    "plugin:error": "When a plugin encounters an error",
    "gateway": "General gateway events",
    "gateway:request": "When a gateway request is received",
    "gateway:response": "When a gateway response is sent",
    "memory": "General memory events",
    "memory:index": "When files are indexed",
    "memory:search": "When memory is searched",
    "security": "General security events",
    "security:audit": "When a security audit runs",
    "security:violation": "When a security violation is detected",
    "ssh": "General SSH events",
    "ssh:connect": "When SSH connection is established",
    "ssh:disconnect": "When SSH connection is closed",
    "ssh:command": "When an SSH command is executed",
    "automation": "General automation events",
    "automation:workflow_start": "When a workflow starts",
    "automation:workflow_end": "When a workflow completes",
    "automation:step": "When a workflow step executes",
}


# ---------------------------------------------------------------------------
# HookEvent
# ---------------------------------------------------------------------------


@dataclass
class HookEvent:
    """Event object passed to every hook handler.

    Attributes:
        type:      Event type (e.g. ``'command'``, ``'session'``).
        action:    Specific action suffix (e.g. ``'before_execute'``).
        context:   Arbitrary context data provided by the trigger site.
        timestamp: Creation time of the event (wall clock, local timezone).
        messages:  Handlers may append strings here; callers can read them back.
        cancel:    Handlers set this to ``True`` to request cancellation.
        data:      Arbitrary data that handlers can attach for downstream use.
    """

    type: str
    action: str
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    messages: list[str] = field(default_factory=list)
    cancel: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def event_key(self) -> str:
        """Full ``type:action`` key."""
        return f"{self.type}:{self.action}"

    def __repr__(self) -> str:
        return f"HookEvent({self.event_key!r}, cancel={self.cancel})"


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------


class HookRegistry:
    """Registry of hook handlers, keyed by event string.

    Supports:
    - Registration by event type (``'command'``) or specific key (``'command:before_execute'``).
    - Priority ordering (lower value = runs first; ties broken by registration order).
    - Async and sync handlers.
    - Per-key enable/disable toggling.
    - Error isolation: one handler failure does not prevent others from running.
    """

    def __init__(self) -> None:
        # event_key -> sorted list of (priority, handler)
        self._handlers: dict[str, list[tuple[int, HookHandler]]] = {}
        self._disabled: set[str] = set()

    def register(
        self, event_key: str, handler: HookHandler, priority: int = 100
    ) -> None:
        """Register *handler* for *event_key* with the given *priority*.

        Lower priority integers run first.  Equal priorities are served in
        registration order (stable sort).
        """
        bucket = self._handlers.setdefault(event_key, [])
        bucket.append((priority, handler))
        bucket.sort(key=lambda pair: pair[0])
        logger.debug("Registered hook: %s (priority=%d)", event_key, priority)

    def unregister(self, event_key: str, handler: HookHandler) -> bool:
        """Remove *handler* from *event_key*.  Returns ``True`` if found."""
        bucket = self._handlers.get(event_key)
        if bucket is None:
            return False

        original_len = len(bucket)
        self._handlers[event_key] = [
            (p, h) for p, h in bucket if h is not handler
        ]
        removed = len(self._handlers[event_key]) < original_len

        if not self._handlers[event_key]:
            del self._handlers[event_key]

        if removed:
            logger.debug("Unregistered hook: %s", event_key)
        return removed

    def clear(self, event_key: str | None = None) -> None:
        """Clear handlers for *event_key*, or all handlers when ``None``."""
        if event_key is not None:
            self._handlers.pop(event_key, None)
            logger.debug("Cleared hooks for: %s", event_key)
        else:
            self._handlers.clear()
            logger.debug("Cleared all hooks")

    def disable(self, event_key: str) -> None:
        """Temporarily suppress handlers for *event_key*."""
        self._disabled.add(event_key)

    def enable(self, event_key: str) -> None:
        """Re-enable handlers previously suppressed for *event_key*."""
        self._disabled.discard(event_key)

    def get_handlers(self, event_key: str) -> list[HookHandler]:
        """Return handlers for *event_key* in priority order, or ``[]`` if disabled."""
        if event_key in self._disabled:
            return []
        return [h for _, h in self._handlers.get(event_key, [])]

    def get_event_keys(self) -> list[str]:
        """Return all registered event keys."""
        return list(self._handlers)

    def handler_count(self, event_key: str | None = None) -> int:
        """Count registered handlers, optionally scoped to *event_key*."""
        if event_key is not None:
            return len(self._handlers.get(event_key, []))
        return sum(len(v) for v in self._handlers.values())


# Module-level singleton
_registry = HookRegistry()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_hook(
    event_key: str,
    handler: HookHandler | None = None,
    priority: int = 100,
) -> None | Callable[[HookHandler], HookHandler]:
    """Register a hook handler, usable as a direct call or a decorator.

    Direct call::

        register_hook("command:before_execute", my_handler)

    Decorator::

        @register_hook("command:before_execute")
        async def my_handler(event: HookEvent) -> None: ...

        @register_hook("command:before_execute", priority=50)
        async def early_handler(event: HookEvent) -> None: ...

    Args:
        event_key: Event type or ``type:action`` key.
        handler:   Handler function (omit when using as a decorator).
        priority:  Execution priority — lower integers run first (default ``100``).
    """
    if handler is not None:
        _registry.register(event_key, handler, priority)
        return None

    def decorator(fn: HookHandler) -> HookHandler:
        _registry.register(event_key, fn, priority)
        return fn

    return decorator


def unregister_hook(event_key: str, handler: HookHandler) -> bool:
    """Remove *handler* from *event_key*.  Returns ``True`` if found."""
    return _registry.unregister(event_key, handler)


def clear_hooks(event_key: str | None = None) -> None:
    """Clear handlers for *event_key*, or all handlers when ``None``."""
    _registry.clear(event_key)


def get_registered_hooks() -> list[str]:
    """Return all registered event keys."""
    return _registry.get_event_keys()


async def trigger_hook(
    event_type: str,
    action: str = "",
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> HookEvent:
    """Trigger an event and run all matching handlers asynchronously.

    Handlers are invoked in priority order.  Errors in one handler are logged
    but do **not** prevent subsequent handlers from running.

    Args:
        event_type: Event type (e.g. ``'command'``).
        action:     Specific action suffix (e.g. ``'before_execute'``).
        context:    Context dict forwarded to handlers.
        **kwargs:   Additional ``HookEvent`` field values.

    Returns:
        The ``HookEvent`` after all handlers have run.
    """
    event = HookEvent(
        type=event_type,
        action=action,
        context=context if context is not None else {},
        **kwargs,
    )

    # Handlers registered on the bare type run before action-specific ones.
    handlers = _registry.get_handlers(event_type)
    if action:
        handlers = handlers + _registry.get_handlers(event.event_key)

    for handler in handlers:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.error(
                "Hook handler error [%s]: %s: %s",
                event.event_key,
                type(exc).__name__,
                exc,
            )

    return event


def trigger_hook_sync(
    event_type: str,
    action: str = "",
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> HookEvent:
    """Synchronous variant of :func:`trigger_hook`.

    Async handlers are **skipped** with a warning; use this only from sync
    code where an event loop is unavailable or cannot be awaited.
    """
    event = HookEvent(
        type=event_type,
        action=action,
        context=context if context is not None else {},
        **kwargs,
    )

    handlers = _registry.get_handlers(event_type)
    if action:
        handlers = handlers + _registry.get_handlers(event.event_key)

    for handler in handlers:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                logger.warning(
                    "Async handler skipped in sync context: %s",
                    getattr(handler, "__name__", repr(handler)),
                )
                result.close()  # Prevent "coroutine was never awaited" warning
        except Exception as exc:
            logger.error(
                "Hook handler error [%s]: %s: %s",
                event.event_key,
                type(exc).__name__,
                exc,
            )

    return event


def create_hook_event(
    event_type: str,
    action: str,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> HookEvent:
    """Build a :class:`HookEvent` without triggering it."""
    return HookEvent(
        type=event_type,
        action=action,
        context=context if context is not None else {},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Convenience decorators
# ---------------------------------------------------------------------------


def before_command(
    command_name: str | None = None, priority: int = 100
) -> Callable[[HookHandler], HookHandler]:
    """Decorator: register a before-command hook.

    Args:
        command_name: When given, only triggers for that specific command.
        priority:     Execution priority.

    Example::

        @before_command("deploy")
        async def validate_deploy(event: HookEvent) -> None:
            if not event.context.get("confirmed"):
                event.cancel = True
    """
    event_key = (
        f"command:before_{command_name}" if command_name else "command:before_execute"
    )
    return register_hook(event_key, priority=priority)  # type: ignore[return-value]


def after_command(
    command_name: str | None = None, priority: int = 100
) -> Callable[[HookHandler], HookHandler]:
    """Decorator: register an after-command hook."""
    event_key = (
        f"command:after_{command_name}" if command_name else "command:after_execute"
    )
    return register_hook(event_key, priority=priority)  # type: ignore[return-value]


def on_error(
    event_type: str | None = None, priority: int = 100
) -> Callable[[HookHandler], HookHandler]:
    """Decorator: register an error hook.

    Args:
        event_type: If provided, scopes to ``'{event_type}:error'``.
        priority:   Execution priority.

    Example::

        @on_error("ssh")
        async def handle_ssh_error(event: HookEvent) -> None:
            pass
    """
    event_key = f"{event_type}:error" if event_type else "error"
    return register_hook(event_key, priority=priority)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def hook_stats() -> dict[str, Any]:
    """Return statistics about all registered hooks."""
    event_keys = _registry.get_event_keys()
    return {
        "total_handlers": _registry.handler_count(),
        "event_keys": len(event_keys),
        "events": {key: _registry.handler_count(key) for key in event_keys},
    }


def list_hook_types() -> dict[str, str]:
    """Return a copy of the predefined hook event type catalogue."""
    return HOOK_EVENT_TYPES.copy()

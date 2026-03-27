"""
Hook System for NAVIG

Provides an extensible event-driven hook system for NAVIG events
like command processing, session lifecycle, plugin integration, etc.

Inspired by advanced internal hook systems.

Example Usage:
    from navig.core.hooks import register_hook, trigger_hook, HookEvent

    # Register a hook handler
    @register_hook("command:before_execute")
    async def validate_command(event: HookEvent):
        if event.context.get("command") == "dangerous":
            event.cancel = True
            event.messages.append("⚠️ Blocked dangerous command")

    # Trigger the hook
    event = await trigger_hook("command:before_execute", {
        "command": command_name,
        "args": args
    })
    if event.cancel:
        return  # Command was blocked
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

logger = logging.getLogger("navig.hooks")

# Type definitions
HookHandler = Callable[["HookEvent"], None | Coroutine[Any, Any, None]]
T = TypeVar("T")


# =============================================================================
# Hook Event Types
# =============================================================================

# Predefined hook event types for type safety and documentation
HOOK_EVENT_TYPES = {
    # Command lifecycle
    "command": "General command events",
    "command:before_execute": "Before a command is executed",
    "command:after_execute": "After a command completes",
    "command:error": "When a command fails",
    # Session lifecycle
    "session": "General session events",
    "session:start": "When a session starts",
    "session:end": "When a session ends",
    "session:context_update": "When session context is updated",
    # Agent events
    "agent": "General agent events",
    "agent:bootstrap": "When an agent is bootstrapped",
    "agent:message": "When an agent receives a message",
    "agent:response": "When an agent sends a response",
    "agent:tool_call": "When an agent makes a tool call",
    # Plugin events
    "plugin": "General plugin events",
    "plugin:load": "When a plugin is loaded",
    "plugin:unload": "When a plugin is unloaded",
    "plugin:error": "When a plugin encounters an error",
    # Gateway events
    "gateway": "General gateway events",
    "gateway:request": "When a gateway request is received",
    "gateway:response": "When a gateway response is sent",
    # Memory events
    "memory": "General memory events",
    "memory:index": "When files are indexed",
    "memory:search": "When memory is searched",
    # Security events
    "security": "General security events",
    "security:audit": "When a security audit runs",
    "security:violation": "When a security violation is detected",
    # SSH/Remote events
    "ssh": "General SSH events",
    "ssh:connect": "When SSH connection is established",
    "ssh:disconnect": "When SSH connection is closed",
    "ssh:command": "When an SSH command is executed",
    # Automation events
    "automation": "General automation events",
    "automation:workflow_start": "When a workflow starts",
    "automation:workflow_end": "When a workflow completes",
    "automation:step": "When a workflow step executes",
}


@dataclass
class HookEvent:
    """
    Event object passed to hook handlers.

    Attributes:
        type: The event type (e.g., 'command', 'session', 'agent')
        action: The specific action (e.g., 'before_execute', 'start', 'bootstrap')
        context: Additional context data for the event
        timestamp: When the event was created
        messages: Messages to send back (hooks can append to this)
        cancel: Set to True to cancel the operation (where supported)
        data: Arbitrary data that hooks can attach for downstream use
    """

    type: str
    action: str
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    messages: list[str] = field(default_factory=list)
    cancel: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def event_key(self) -> str:
        """Get the full event key (type:action)."""
        return f"{self.type}:{self.action}"

    def __repr__(self) -> str:
        return f"HookEvent({self.event_key}, cancel={self.cancel})"


# =============================================================================
# Hook Registry
# =============================================================================


class HookRegistry:
    """
    Registry of hook handlers.

    Supports:
    - Registration by event type (e.g., 'command') or specific action (e.g., 'command:new')
    - Priority ordering (lower priority = runs first)
    - Async and sync handlers
    - Error isolation (one handler failure doesn't break others)
    """

    def __init__(self):
        self._handlers: dict[str, list[tuple[int, HookHandler]]] = {}
        self._disabled_hooks: set[str] = set()

    def register(
        self, event_key: str, handler: HookHandler, priority: int = 100
    ) -> None:
        """
        Register a hook handler for an event.

        Args:
            event_key: Event type or type:action (e.g., 'command' or 'command:new')
            handler: Function to call when event triggers
            priority: Lower runs first (default: 100)
        """
        if event_key not in self._handlers:
            self._handlers[event_key] = []

        self._handlers[event_key].append((priority, handler))
        # Sort by priority (stable sort preserves registration order for same priority)
        self._handlers[event_key].sort(key=lambda x: x[0])

        logger.debug(f"Registered hook: {event_key} (priority={priority})")

    def unregister(self, event_key: str, handler: HookHandler) -> bool:
        """
        Unregister a specific hook handler.

        Args:
            event_key: Event key the handler was registered for
            handler: The handler function to remove

        Returns:
            True if handler was found and removed
        """
        if event_key not in self._handlers:
            return False

        original_len = len(self._handlers[event_key])
        self._handlers[event_key] = [
            (p, h) for p, h in self._handlers[event_key] if h != handler
        ]

        # Clean up empty lists
        if not self._handlers[event_key]:
            del self._handlers[event_key]

        removed = len(self._handlers.get(event_key, [])) < original_len
        if removed:
            logger.debug(f"Unregistered hook: {event_key}")
        return removed

    def clear(self, event_key: str | None = None) -> None:
        """
        Clear registered hooks.

        Args:
            event_key: If provided, clear only this event's handlers.
                      If None, clear all handlers.
        """
        if event_key:
            self._handlers.pop(event_key, None)
            logger.debug(f"Cleared hooks for: {event_key}")
        else:
            self._handlers.clear()
            logger.debug("Cleared all hooks")

    def disable(self, event_key: str) -> None:
        """Temporarily disable hooks for an event key."""
        self._disabled_hooks.add(event_key)

    def enable(self, event_key: str) -> None:
        """Re-enable hooks for an event key."""
        self._disabled_hooks.discard(event_key)

    def get_handlers(self, event_key: str) -> list[HookHandler]:
        """Get all handlers for an event key (in priority order)."""
        if event_key in self._disabled_hooks:
            return []
        return [h for _, h in self._handlers.get(event_key, [])]

    def get_event_keys(self) -> list[str]:
        """Get all registered event keys."""
        return list(self._handlers.keys())

    def handler_count(self, event_key: str | None = None) -> int:
        """Get count of registered handlers."""
        if event_key:
            return len(self._handlers.get(event_key, []))
        return sum(len(handlers) for handlers in self._handlers.values())


# Global registry instance
_registry = HookRegistry()


# =============================================================================
# Public API
# =============================================================================


def register_hook(
    event_key: str, handler: HookHandler | None = None, priority: int = 100
) -> None | Callable[[HookHandler], HookHandler]:
    """
    Register a hook handler for an event.

    Can be used as a decorator or called directly:

        # As decorator
        @register_hook("command:before_execute")
        async def my_handler(event):
            pass

        # Direct call
        register_hook("command:before_execute", my_handler)

        # With priority (lower = runs first)
        @register_hook("command:before_execute", priority=50)
        async def early_handler(event):
            pass

    Args:
        event_key: Event type or type:action
        handler: Handler function (optional if using as decorator)
        priority: Execution priority (lower runs first, default: 100)
    """
    if handler is not None:
        _registry.register(event_key, handler, priority)
        return None

    # Return decorator
    def decorator(fn: HookHandler) -> HookHandler:
        _registry.register(event_key, fn, priority)
        return fn

    return decorator


def unregister_hook(event_key: str, handler: HookHandler) -> bool:
    """
    Unregister a specific hook handler.

    Args:
        event_key: Event key the handler was registered for
        handler: The handler function to remove

    Returns:
        True if handler was found and removed
    """
    return _registry.unregister(event_key, handler)


def clear_hooks(event_key: str | None = None) -> None:
    """
    Clear registered hooks.

    Args:
        event_key: If provided, clear only this event's handlers.
                  If None, clear all handlers.
    """
    _registry.clear(event_key)


def get_registered_hooks() -> list[str]:
    """Get all registered event keys."""
    return _registry.get_event_keys()


async def trigger_hook(
    event_type: str,
    action: str = "",
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> HookEvent:
    """
    Trigger a hook event and run all registered handlers.

    Handlers are called in priority order. Errors in handlers are logged
    but don't prevent other handlers from running.

    Args:
        event_type: The event type (e.g., 'command', 'session')
        action: The specific action (e.g., 'before_execute')
        context: Additional context data
        **kwargs: Additional event attributes

    Returns:
        The HookEvent after all handlers have run
    """
    if context is None:
        context = {}

    event = HookEvent(type=event_type, action=action, context=context, **kwargs)

    # Get handlers for both general type and specific action
    type_handlers = _registry.get_handlers(event_type)
    action_handlers = _registry.get_handlers(event.event_key) if action else []

    all_handlers = type_handlers + action_handlers

    if not all_handlers:
        return event

    for handler in all_handlers:
        try:
            result = handler(event)
            # Handle async handlers
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Hook error [{event.event_key}]: {e.__class__.__name__}: {e}")
            # Continue with other handlers

    return event


def trigger_hook_sync(
    event_type: str,
    action: str = "",
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> HookEvent:
    """
    Synchronous version of trigger_hook.

    Only calls synchronous handlers. Async handlers are skipped with a warning.

    Use this when you need to trigger hooks from sync code.
    """
    if context is None:
        context = {}

    event = HookEvent(type=event_type, action=action, context=context, **kwargs)

    type_handlers = _registry.get_handlers(event_type)
    action_handlers = _registry.get_handlers(event.event_key) if action else []

    all_handlers = type_handlers + action_handlers

    for handler in all_handlers:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                logger.warning(
                    f"Async handler skipped in sync context: {handler.__name__}"
                )
                result.close()  # Prevent coroutine warning
        except Exception as e:
            logger.error(f"Hook error [{event.event_key}]: {e.__class__.__name__}: {e}")

    return event


def create_hook_event(
    event_type: str,
    action: str,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> HookEvent:
    """
    Create a HookEvent without triggering it.

    Useful for building events before triggering.
    """
    return HookEvent(type=event_type, action=action, context=context or {}, **kwargs)


# =============================================================================
# Hook Decorators for Common Patterns
# =============================================================================


def before_command(command_name: str | None = None, priority: int = 100):
    """
    Decorator to register a before-command hook.

    Args:
        command_name: If provided, only trigger for this command
        priority: Execution priority

    Example:
        @before_command("deploy")
        async def validate_deploy(event):
            if not event.context.get("confirmed"):
                event.cancel = True
    """
    event_key = (
        f"command:before_{command_name}" if command_name else "command:before_execute"
    )
    return register_hook(event_key, priority=priority)


def after_command(command_name: str | None = None, priority: int = 100):
    """Decorator to register an after-command hook."""
    event_key = (
        f"command:after_{command_name}" if command_name else "command:after_execute"
    )
    return register_hook(event_key, priority=priority)


def on_error(event_type: str | None = None, priority: int = 100):
    """
    Decorator to register an error hook.

    Args:
        event_type: If provided, only trigger for errors in this event type
        priority: Execution priority

    Example:
        @on_error("ssh")
        async def handle_ssh_error(event):
            # Log or notify about SSH errors
            pass
    """
    event_key = f"{event_type}:error" if event_type else "error"
    return register_hook(event_key, priority=priority)


# =============================================================================
# Utility Functions
# =============================================================================


def hook_stats() -> dict[str, Any]:
    """Get statistics about registered hooks."""
    event_keys = _registry.get_event_keys()
    return {
        "total_handlers": _registry.handler_count(),
        "event_keys": len(event_keys),
        "events": {key: _registry.handler_count(key) for key in event_keys},
    }


def list_hook_types() -> dict[str, str]:
    """Get all predefined hook event types and their descriptions."""
    return HOOK_EVENT_TYPES.copy()

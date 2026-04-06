"""
Nervous System - Agent Event Bus

Central event coordination system that allows components to:
- Publish events (emit)
- Subscribe to events (listen)
- Communicate asynchronously

Inspired by the human nervous system's role in coordinating body functions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any
from uuid import uuid4


class EventType(Enum):
    """Types of events in the agent system."""

    # Lifecycle events
    AGENT_STARTING = auto()
    AGENT_STARTED = auto()
    AGENT_STOPPING = auto()
    AGENT_STOPPED = auto()

    # Component events
    COMPONENT_STARTING = auto()
    COMPONENT_STARTED = auto()
    COMPONENT_STOPPING = auto()
    COMPONENT_STOPPED = auto()
    COMPONENT_ERROR = auto()
    COMPONENT_DEGRADED = auto()

    # Heart (orchestrator) events
    HEARTBEAT = auto()
    HEALTH_CHECK = auto()

    # Eyes (monitoring) events
    METRIC_COLLECTED = auto()
    ALERT_TRIGGERED = auto()
    ANOMALY_DETECTED = auto()
    LOG_ENTRY = auto()
    FILE_CHANGED = auto()

    # Ears (input) events
    MESSAGE_RECEIVED = auto()
    COMMAND_RECEIVED = auto()
    WEBHOOK_RECEIVED = auto()
    USER_INPUT = auto()

    # Hands (execution) events
    COMMAND_STARTED = auto()
    COMMAND_COMPLETED = auto()
    COMMAND_FAILED = auto()
    ACTION_PENDING = auto()
    ACTION_APPROVED = auto()
    ACTION_REJECTED = auto()

    # Brain (thinking) events
    THOUGHT = auto()
    DECISION_MADE = auto()
    PLAN_CREATED = auto()
    LEARNING = auto()
    REASONING = auto()

    # Memory events
    CONTEXT_UPDATED = auto()
    MEMORY_STORED = auto()
    MEMORY_RECALLED = auto()

    # Soul (personality) events
    MOOD_CHANGED = auto()
    RESPONSE_GENERATED = auto()

    # System events
    SYSTEM_INFO = auto()
    SYSTEM_WARNING = auto()
    SYSTEM_ERROR = auto()

    # Custom/plugin events
    CUSTOM = auto()


class EventPriority(Enum):
    """Event priority levels."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Event:
    """An event in the agent system."""

    type: EventType
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    priority: EventPriority = EventPriority.NORMAL
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: str(uuid4())[:8])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.name,
            "source": self.source,
            "data": self.data,
            "priority": self.priority.name,
            "timestamp": self.timestamp.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Event {self.type.name} from={self.source} id={self.id}>"


# Type alias for event handlers
EventHandler = Callable[[Event], Any]


class NervousSystem:
    """
    Central event coordination system.

    The nervous system connects all components and allows them to
    communicate through events. Components can:
    - Subscribe to specific event types
    - Emit events for other components
    - React to system-wide events
    """

    def __init__(self):
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._global_handlers: list[EventHandler] = []
        self._event_history: list[Event] = []
        self._max_history = 1000
        self._paused = False
        self._pending_events: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._event_loop_task: asyncio.Task | None = None

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ) -> None:
        """Subscribe to a specific event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        self._global_handlers.append(handler)

    def unsubscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ) -> None:
        """Unsubscribe from a specific event type."""
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass  # malformed value; skip

    def unsubscribe_all(self, handler: EventHandler) -> None:
        """Unsubscribe from all events."""
        try:
            self._global_handlers.remove(handler)
        except ValueError:
            pass  # malformed value; skip

    async def emit(
        self,
        event_type: EventType,
        source: str,
        data: dict[str, Any] | None = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> Event:
        """Emit an event to all subscribers."""
        event = Event(
            type=event_type,
            source=source,
            data=data or {},
            priority=priority,
        )

        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history :]

        if self._paused:
            await self._pending_events.put(event)
            return event

        # Dispatch to handlers
        await self._dispatch(event)

        return event

    async def _dispatch(self, event: Event) -> None:
        """Dispatch event to all relevant handlers."""
        handlers: list[EventHandler] = []

        # Add global handlers
        handlers.extend(self._global_handlers)

        # Add type-specific handlers
        if event.type in self._handlers:
            handlers.extend(self._handlers[event.type])

        # Call handlers concurrently
        if handlers:

            async def safe_call(handler: EventHandler) -> None:
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    # Log but don't crash
                    import logging

                    logging.getLogger("navig.agent.nervous_system").exception(
                        "Error in event handler: %s", e
                    )

            await asyncio.gather(*[safe_call(h) for h in handlers], return_exceptions=True)

    def pause(self) -> None:
        """Pause event dispatching (events are queued)."""
        self._paused = True

    async def resume(self) -> None:
        """Resume event dispatching and process queued events."""
        self._paused = False

        while not self._pending_events.empty():
            try:
                event = self._pending_events.get_nowait()
                await self._dispatch(event)
            except asyncio.QueueEmpty:
                break

    def get_history(
        self,
        event_type: EventType | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Get event history with optional filtering."""
        events = self._event_history

        if event_type:
            events = [e for e in events if e.type == event_type]

        if source:
            events = [e for e in events if e.source == source]

        return events[-limit:]

    def clear_history(self) -> None:
        """Clear event history."""
        self._event_history.clear()

    def get_stats(self) -> dict[str, Any]:
        """Get nervous system statistics."""
        type_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}

        for event in self._event_history:
            type_name = event.type.name
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
            source_counts[event.source] = source_counts.get(event.source, 0) + 1

        return {
            "total_events": len(self._event_history),
            "handler_count": sum(len(h) for h in self._handlers.values()),
            "global_handlers": len(self._global_handlers),
            "paused": self._paused,
            "pending_events": self._pending_events.qsize(),
            "events_by_type": type_counts,
            "events_by_source": source_counts,
        }

    def list_subscriptions(self) -> dict[str, int]:
        """List all event subscriptions."""
        return {event_type.name: len(handlers) for event_type, handlers in self._handlers.items()}


class EventEmitter:
    """
    Mixin class for components that emit events.

    Provides convenient methods for emitting common event types.
    """

    def __init__(self, name: str, nervous_system: NervousSystem | None = None):
        self._emitter_name = name
        self._nervous_system = nervous_system

    def set_nervous_system(self, ns: NervousSystem) -> None:
        self._nervous_system = ns

    async def emit(
        self,
        event_type: EventType,
        data: dict[str, Any] | None = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> Event | None:
        if self._nervous_system:
            return await self._nervous_system.emit(
                event_type,
                source=self._emitter_name,
                data=data,
                priority=priority,
            )
        return None

    async def emit_info(self, message: str, **data: Any) -> Event | None:
        return await self.emit(EventType.SYSTEM_INFO, data={"message": message, **data})

    async def emit_warning(self, message: str, **data: Any) -> Event | None:
        return await self.emit(
            EventType.SYSTEM_WARNING,
            data={"message": message, **data},
            priority=EventPriority.HIGH,
        )

    async def emit_error(
        self, message: str, error: Exception | None = None, **data: Any
    ) -> Event | None:
        return await self.emit(
            EventType.SYSTEM_ERROR,
            data={"message": message, "error": str(error) if error else None, **data},
            priority=EventPriority.CRITICAL,
        )

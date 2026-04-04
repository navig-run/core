"""
System Events - Background task event queue

Handles:
- Event emission and subscription
- Event persistence for recovery
- Priority-based processing
- Event history
"""

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navig.debug_logger import get_debug_logger

if TYPE_CHECKING:
    pass

logger = get_debug_logger()


class EventPriority(Enum):
    """Event priority levels."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class SystemEvent:
    """A system event."""

    id: str
    event_type: str
    payload: dict[str, Any]
    priority: EventPriority
    timestamp: datetime
    processed: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "payload": self.payload,
            "priority": self.priority.name,
            "timestamp": self.timestamp.isoformat(),
            "processed": self.processed,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SystemEvent":
        return cls(
            id=data["id"],
            event_type=data["event_type"],
            payload=data["payload"],
            priority=EventPriority[data["priority"]],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            processed=data.get("processed", False),
            error=data.get("error"),
        )


# Event handler type
EventHandler = Callable[[SystemEvent], Any]


class SystemEventQueue:
    """
    Persistent event queue for background tasks.

    Features:
    - Priority-based processing
    - Event persistence to disk
    - Subscription model
    - Event history
    """

    def __init__(self, storage_path: Path, max_history: int = 1000):
        self.storage_path = storage_path
        self.max_history = max_history

        # Event queue (priority queue simulation)
        self._queue: asyncio.Queue = asyncio.Queue()

        # Pending events (not yet processed)
        self._pending: dict[str, SystemEvent] = {}

        # Event history
        self._history: list[SystemEvent] = []

        # Subscribers by event type
        self._subscribers: dict[str, list[EventHandler]] = {}

        # Wildcard subscribers (receive all events)
        self._wildcard_subscribers: list[EventHandler] = []

        # Running state
        self._running = False
        self._processor_task: asyncio.Task | None = None

        # Event counter for ID generation
        self._event_counter = 0

        # Load persisted events
        self._load_events()

    def _get_events_path(self) -> Path:
        return self.storage_path / "events.json"

    def _load_events(self) -> None:
        """Load pending events from disk."""
        events_path = self._get_events_path()

        if events_path.exists():
            try:
                data = json.loads(events_path.read_text())

                # Load pending events
                for event_data in data.get("pending", []):
                    event = SystemEvent.from_dict(event_data)
                    self._pending[event.id] = event

                # Load counter
                self._event_counter = data.get("counter", 0)

                logger.info("Loaded %s pending events", len(self._pending))

            except Exception as e:
                logger.error("Failed to load events: %s", e)

    def _save_events(self) -> None:
        """Save pending events to disk."""
        self.storage_path.mkdir(parents=True, exist_ok=True)

        data = {
            "counter": self._event_counter,
            "pending": [e.to_dict() for e in self._pending.values()],
        }

        self._get_events_path().write_text(json.dumps(data, indent=2))

    def _generate_id(self) -> str:
        """Generate unique event ID."""
        self._event_counter += 1
        return f"evt_{self._event_counter}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    async def start(self) -> None:
        """Start event processor."""
        if self._running:
            return

        self._running = True

        # Queue pending events for processing
        for event in sorted(
            self._pending.values(),
            key=lambda e: (e.priority.value, e.timestamp),
            reverse=True,  # Higher priority first
        ):
            await self._queue.put(event)

        # Start processor
        self._processor_task = asyncio.create_task(self._process_loop())

        logger.info("Event queue started")

    async def stop(self) -> None:
        """Stop event processor."""
        self._running = False

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        # Save pending events
        self._save_events()

        logger.info("Event queue stopped")

    async def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> str:
        """
        Emit an event.

        Args:
            event_type: Type of event (e.g., 'host_check', 'config_reloaded')
            payload: Event data
            priority: Processing priority

        Returns:
            Event ID
        """
        event = SystemEvent(
            id=self._generate_id(),
            event_type=event_type,
            payload=payload or {},
            priority=priority,
            timestamp=datetime.now(),
        )

        # Add to pending
        self._pending[event.id] = event
        self._save_events()

        # Queue for processing
        await self._queue.put(event)

        logger.debug("Event emitted: %s (id=%s)", event_type, event.id)

        return event.id

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """
        Subscribe to events of a specific type.

        Args:
            event_type: Event type to subscribe to, or '*' for all
            handler: Callback function
        """
        if event_type == "*":
            self._wildcard_subscribers.append(handler)
        else:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(handler)

        logger.debug("Subscribed to event: %s", event_type)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe from events."""
        if event_type == "*":
            if handler in self._wildcard_subscribers:
                self._wildcard_subscribers.remove(handler)
        else:
            if event_type in self._subscribers:
                if handler in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(handler)

    async def _process_loop(self) -> None:
        """Main event processing loop."""
        while self._running:
            try:
                # Get event with timeout
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                await self._process_event(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in event processor: %s", e)

    async def _process_event(self, event: SystemEvent) -> None:
        """Process a single event."""
        logger.debug("Processing event: %s (id=%s)", event.event_type, event.id)

        handlers = []

        # Get type-specific handlers
        if event.event_type in self._subscribers:
            handlers.extend(self._subscribers[event.event_type])

        # Add wildcard handlers
        handlers.extend(self._wildcard_subscribers)

        # Call all handlers
        errors = []
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                errors.append(str(e))
                logger.error("Handler error for %s: %s", event.event_type, e)

        # Mark as processed
        event.processed = True
        if errors:
            event.error = "; ".join(errors)

        # Move to history
        if event.id in self._pending:
            del self._pending[event.id]

        self._history.append(event)

        # Trim history
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history :]

        self._save_events()

    def get_pending(self) -> list[SystemEvent]:
        """Get all pending events."""
        return list(self._pending.values())

    def get_history(self, event_type: str | None = None, limit: int = 50) -> list[SystemEvent]:
        """Get event history."""
        events = self._history

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        return events[-limit:]


# Common event types
class EventTypes:
    """Standard event type constants."""

    # Host events
    HOST_CHECK = "host_check"
    HOST_DOWN = "host_down"
    HOST_UP = "host_up"
    HOST_DISK_WARNING = "host_disk_warning"
    HOST_MEMORY_WARNING = "host_memory_warning"

    # Certificate events
    CERT_EXPIRY_WARNING = "cert_expiry_warning"
    CERT_EXPIRED = "cert_expired"

    # Service events
    SERVICE_DOWN = "service_down"
    SERVICE_UP = "service_up"
    SERVICE_RESTARTED = "service_restarted"

    # Configuration events
    CONFIG_RELOADED = "config_reloaded"
    CONFIG_ERROR = "config_error"

    # Session events
    SESSION_CREATED = "session_created"
    SESSION_EXPIRED = "session_expired"

    # Heartbeat events
    HEARTBEAT_START = "heartbeat_start"
    HEARTBEAT_COMPLETE = "heartbeat_complete"
    HEARTBEAT_FAILED = "heartbeat_failed"

    # Cron events
    CRON_JOB_START = "cron_job_start"
    CRON_JOB_COMPLETE = "cron_job_complete"
    CRON_JOB_FAILED = "cron_job_failed"

    # Notification events
    NOTIFICATION_SENT = "notification_sent"
    NOTIFICATION_SUPPRESSED = "notification_suppressed"

    # Workspace events
    WORKSPACE_FILE_CHANGED = "workspace_file_changed"


class SmartNotificationFilter:
    """
    Filters notifications based on context and patterns.

    Features:
    - HEARTBEAT_OK suppression
    - Duplicate suppression
    - Rate limiting
    - Priority-based delivery
    """

    def __init__(
        self,
        event_queue: SystemEventQueue,
        cooldown_seconds: int = 300,  # 5 minutes
        quiet_hours_enabled: bool = False,
        quiet_hours_start: int = 23,
        quiet_hours_end: int = 7,
        notifications_enabled: bool = True,
    ):
        self.event_queue = event_queue
        self.cooldown_seconds = cooldown_seconds
        self.quiet_hours_enabled = bool(
            quiet_hours_enabled
            if quiet_hours_enabled is not None
            else _env_bool("NAVIG_QUIET_HOURS_ENABLED", False)
        )
        self.quiet_hours_start = int(os.getenv("NAVIG_QUIET_HOURS_START", quiet_hours_start))
        self.quiet_hours_end = int(os.getenv("NAVIG_QUIET_HOURS_END", quiet_hours_end))
        self.notifications_enabled = bool(
            notifications_enabled
            if notifications_enabled is not None
            else _env_bool("NAVIG_NOTIFICATIONS_ENABLED", True)
        )

        # Track recent notifications for dedup
        self._recent: dict[str, datetime] = {}

        # Subscribe to notification events
        event_queue.subscribe(EventTypes.NOTIFICATION_SENT, self._on_notification)

    async def should_notify(
        self,
        notification_type: str,
        message: str,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        """
        Determine if a notification should be sent.

        Returns False for:
        - HEARTBEAT_OK messages
        - Duplicate messages within cooldown
        - Low priority during quiet hours
        """
        if not self.notifications_enabled:
            await self.event_queue.emit(
                EventTypes.NOTIFICATION_SUPPRESSED,
                {"reason": "notifications_disabled", "type": notification_type},
            )
            return False

        # Check for HEARTBEAT_OK
        if "HEARTBEAT_OK" in message:
            logger.debug("Suppressing HEARTBEAT_OK notification")
            await self.event_queue.emit(
                EventTypes.NOTIFICATION_SUPPRESSED,
                {"reason": "heartbeat_ok", "type": notification_type},
            )
            return False

        # Check for duplicates
        cache_key = f"{notification_type}:{hash(message)}"
        now = datetime.now()

        if cache_key in self._recent:
            last_sent = self._recent[cache_key]
            if (now - last_sent).total_seconds() < self.cooldown_seconds:
                logger.debug("Suppressing duplicate notification: %s", notification_type)
                await self.event_queue.emit(
                    EventTypes.NOTIFICATION_SUPPRESSED,
                    {"reason": "duplicate", "type": notification_type},
                )
                return False

        # Critical priority always goes through
        if priority == EventPriority.CRITICAL:
            return True

        # Quiet hours suppression for non-urgent traffic
        if self.quiet_hours_enabled and self._is_quiet_hours():
            if priority in (EventPriority.LOW, EventPriority.NORMAL):
                await self.event_queue.emit(
                    EventTypes.NOTIFICATION_SUPPRESSED,
                    {"reason": "quiet_hours", "type": notification_type},
                )
                return False

        return True

    async def notify(
        self,
        notification_type: str,
        message: str,
        channel: str,
        recipient: str,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        """
        Send a notification if it passes filters.

        Returns True if notification was sent.
        """
        if not await self.should_notify(notification_type, message, priority):
            return False

        # Record notification
        cache_key = f"{notification_type}:{hash(message)}"
        self._recent[cache_key] = datetime.now()

        # Emit event
        await self.event_queue.emit(
            EventTypes.NOTIFICATION_SENT,
            {
                "type": notification_type,
                "channel": channel,
                "recipient": recipient,
                "message_length": len(message),
            },
        )

        return True

    def _on_notification(self, event: SystemEvent) -> None:
        """Track sent notifications."""
        # Cleanup old entries
        now = datetime.now()
        old_keys = [
            k
            for k, v in self._recent.items()
            if (now - v).total_seconds() > self.cooldown_seconds * 2
        ]
        for k in old_keys:
            del self._recent[k]

    def _is_quiet_hours(self) -> bool:
        now_hour = datetime.now().hour
        start = self.quiet_hours_start % 24
        end = self.quiet_hours_end % 24
        if start == end:
            return False
        # Wrap-around window (e.g. 23 -> 7)
        if start > end:
            return now_hour >= start or now_hour < end
        return start <= now_hour < end


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

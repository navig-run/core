"""
Event Bridge — Unified publish pipeline for NAVIG event systems.

Connects the three previously-disconnected event subsystems:

1. **NervousSystem** (agent-internal, typed EventType enum, in-memory)
2. **SystemEventQueue** (gateway-level, string event_type, disk-persistent)
3. **MCP WebSocket notifications** (JSON-RPC push to authenticated clients)

Architecture:
    NervousSystem ──subscribe_all()──┐
                                     ├──► EventBridge ──► filter ──► broadcast
    SystemEventQueue ──subscribe(*)──┘         │
                                               ▼
                                     MCP WebSocket clients
                                     (per-client SubscriptionFilter)

Usage:
    bridge = EventBridge()
    bridge.attach_nervous_system(ns)
    bridge.attach_event_queue(eq)
    bridge.register_client(ws, SubscriptionFilter(topics={"agent.*"}))
    # Events from either source now push directly to filtered WS clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

logger = logging.getLogger("navig.event_bridge")

# ---------------------------------------------------------------------------
# Severity classification (unified across both event systems)
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Unified severity levels for bridged events."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# EventEnvelope — canonical wire format for all bridged events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """
    Normalized event carried across the bridge.

    Every event from NervousSystem or SystemEventQueue is converted into
    this envelope before filtering and broadcast.
    """

    id: str
    topic: str  # dot-delimited, e.g. "agent.heartbeat", "host.disk_warning"
    source: str  # originating component, e.g. "heart", "cron_service"
    severity: Severity
    timestamp: datetime
    data: dict[str, Any]
    origin: str  # "nervous_system" | "system_event_queue" | "direct"

    # ---- serialisation helpers ------------------------------------------

    def to_jsonrpc_notification(self) -> dict[str, Any]:
        """Serialise as a JSON-RPC 2.0 notification (no id)."""
        return {
            "jsonrpc": "2.0",
            "method": f"navig.event.{self.topic}",
            "params": {
                "id": self.id,
                "topic": self.topic,
                "source": self.source,
                "severity": self.severity.value,
                "timestamp": self.timestamp.isoformat(),
                "data": self.data,
                "origin": self.origin,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "source": self.source,
            "severity": self.severity.value,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
            "origin": self.origin,
        }


# ---------------------------------------------------------------------------
# SubscriptionFilter — per-client filter specification
# ---------------------------------------------------------------------------


@dataclass
class SubscriptionFilter:
    """
    Client-side subscription preferences.

    Fields use *include-set* semantics — an empty set means "accept all".
    Topic patterns support simple globs: ``agent.*`` matches ``agent.heartbeat``.
    """

    topics: set[str] = field(default_factory=set)
    severities: set[Severity] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)

    # pre-compiled regexes (built lazily)
    _topic_patterns: list[re.Pattern] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def _compile_topics(self) -> list[re.Pattern]:
        if self._topic_patterns is None:
            patterns: list[re.Pattern] = []
            for t in self.topics:
                # Convert simple glob to regex: "agent.*" → "^agent\..*$"
                regex = "^" + re.escape(t).replace(r"\*", ".*") + "$"
                patterns.append(re.compile(regex))
            self._topic_patterns = patterns
        return self._topic_patterns

    def matches(self, envelope: EventEnvelope) -> bool:
        """Return True if the envelope passes this filter."""
        # Topic filter
        if self.topics:
            patterns = self._compile_topics()
            if not any(p.match(envelope.topic) for p in patterns):
                return False

        # Severity filter
        if self.severities and envelope.severity not in self.severities:
            return False

        # Source filter
        if self.sources and envelope.source not in self.sources:
            return False

        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "topics": sorted(self.topics),
            "severities": sorted(s.value for s in self.severities),
            "sources": sorted(self.sources),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubscriptionFilter:
        return cls(
            topics=set(data.get("topics", [])),
            severities={Severity(s) for s in data.get("severities", [])},
            sources=set(data.get("sources", [])),
        )

    @classmethod
    def accept_all(cls) -> SubscriptionFilter:
        """Factory: filter that accepts every event."""
        return cls()


# ---------------------------------------------------------------------------
# WebSocket client protocol (duck-typed)
# ---------------------------------------------------------------------------


@runtime_checkable
class WebSocketLike(Protocol):
    """Minimal protocol a WebSocket object must satisfy."""

    async def send(self, data: str) -> None: ...


# ---------------------------------------------------------------------------
# Severity mapping helpers
# ---------------------------------------------------------------------------

# NervousSystem EventPriority → Severity
_PRIORITY_TO_SEVERITY = {
    "LOW": Severity.DEBUG,
    "NORMAL": Severity.INFO,
    "HIGH": Severity.WARNING,
    "CRITICAL": Severity.CRITICAL,
}

# SystemEventQueue EventPriority → Severity
_SYS_PRIORITY_TO_SEVERITY = _PRIORITY_TO_SEVERITY  # same enum names

# NervousSystem EventType name → topic prefix mapping
_EVENT_TYPE_TOPIC_MAP: dict[str, str] = {
    # Lifecycle
    "AGENT_STARTING": "agent.starting",
    "AGENT_STARTED": "agent.started",
    "AGENT_STOPPING": "agent.stopping",
    "AGENT_STOPPED": "agent.stopped",
    # Component
    "COMPONENT_STARTING": "component.starting",
    "COMPONENT_STARTED": "component.started",
    "COMPONENT_STOPPING": "component.stopping",
    "COMPONENT_STOPPED": "component.stopped",
    "COMPONENT_ERROR": "component.error",
    "COMPONENT_DEGRADED": "component.degraded",
    # Heart
    "HEARTBEAT": "agent.heartbeat",
    "HEALTH_CHECK": "agent.health_check",
    # Eyes
    "METRIC_COLLECTED": "monitor.metric",
    "ALERT_TRIGGERED": "monitor.alert",
    "ANOMALY_DETECTED": "monitor.anomaly",
    "LOG_ENTRY": "monitor.log",
    "FILE_CHANGED": "monitor.file_changed",
    # Ears
    "MESSAGE_RECEIVED": "input.message",
    "COMMAND_RECEIVED": "input.command",
    "WEBHOOK_RECEIVED": "input.webhook",
    "USER_INPUT": "input.user",
    # Hands
    "COMMAND_STARTED": "exec.started",
    "COMMAND_COMPLETED": "exec.completed",
    "COMMAND_FAILED": "exec.failed",
    "ACTION_PENDING": "approval.pending",
    "ACTION_APPROVED": "approval.approved",
    "ACTION_REJECTED": "approval.rejected",
    # Brain
    "THOUGHT": "brain.thought",
    "DECISION_MADE": "brain.decision",
    "PLAN_CREATED": "brain.plan",
    "LEARNING": "brain.learning",
    "REASONING": "brain.reasoning",
    # Memory
    "CONTEXT_UPDATED": "memory.context",
    "MEMORY_STORED": "memory.stored",
    "MEMORY_RECALLED": "memory.recalled",
    # Soul
    "MOOD_CHANGED": "soul.mood",
    "RESPONSE_GENERATED": "soul.response",
    # System
    "SYSTEM_INFO": "system.info",
    "SYSTEM_WARNING": "system.warning",
    "SYSTEM_ERROR": "system.error",
    # Custom
    "CUSTOM": "custom",
}

# Severity hints derived from EventType name
_EVENT_TYPE_SEVERITY_HINTS: dict[str, Severity] = {
    "COMPONENT_ERROR": Severity.ERROR,
    "COMPONENT_DEGRADED": Severity.WARNING,
    "ALERT_TRIGGERED": Severity.WARNING,
    "ANOMALY_DETECTED": Severity.WARNING,
    "COMMAND_FAILED": Severity.ERROR,
    "SYSTEM_WARNING": Severity.WARNING,
    "SYSTEM_ERROR": Severity.ERROR,
}


# ---------------------------------------------------------------------------
# EventBridge — the core unifier
# ---------------------------------------------------------------------------


class EventBridge:
    """
    Central event bridge that unifies NervousSystem, SystemEventQueue, and
    MCP WebSocket notifications into a single push pipeline.

    Lifecycle:
        bridge = EventBridge()
        bridge.attach_nervous_system(ns)       # optional
        bridge.attach_event_queue(eq)           # optional
        bridge.register_client(ws, filter)
        ...
        bridge.detach_all()
    """

    def __init__(
        self,
        *,
        debounce_seconds: float = 0.5,
        max_payload_bytes: int = 131_072,
        broadcast_timeout: float = 0.25,
        enable_ipc_offload: bool = False,
        ipc_socket_path: str | None = None,
    ):
        self.debounce_seconds = debounce_seconds
        self.max_payload_bytes = max_payload_bytes
        self.broadcast_timeout = broadcast_timeout
        self.enable_ipc_offload = enable_ipc_offload
        import sys

        self.ipc_socket_path = ipc_socket_path or (
            r"\\.\pipe\navig-sysd.sock" if sys.platform == "win32" else "/tmp/navig-sysd.sock"
        )

        # Client registry: ws → filter
        self._clients: dict[int, tuple[WebSocketLike, SubscriptionFilter]] = {}

        # Recent envelopes (for debounce + dedup)
        self._recent: dict[str, float] = {}  # topic → last_emit_time
        self._dedup_window: float = 0.3  # seconds

        # History ring buffer — deque(maxlen) is O(1) on append and auto-evicts
        self._history: deque[EventEnvelope] = deque(maxlen=500)
        self._max_history: int = 500

        # Attached sources
        self._ns_attached = False
        self._eq_attached = False

        # Stats
        self._stats = {
            "events_received": 0,
            "events_broadcast": 0,
            "events_filtered": 0,
            "events_dropped": 0,
            "clients_registered": 0,
        }

        # Listeners to remove on detach
        self._ns_handler: Callable | None = None
        self._eq_handler: Callable | None = None

        # References to attached sources
        self._nervous_system: Any = None
        self._event_queue: Any = None

    # ------------------------------------------------------------------
    # Source attachment
    # ------------------------------------------------------------------

    def attach_nervous_system(self, ns: Any) -> None:
        """
        Subscribe to all NervousSystem events and forward them.

        Args:
            ns: A NervousSystem instance.
        """
        if self._ns_attached:
            return

        async def _on_ns_event(event: Any) -> None:
            envelope = self._normalise_ns_event(event)
            await self.push(envelope)

        ns.subscribe_all(_on_ns_event)
        self._ns_handler = _on_ns_event
        self._nervous_system = ns
        self._ns_attached = True
        logger.info("EventBridge attached to NervousSystem")

    def attach_event_queue(self, eq: Any) -> None:
        """
        Subscribe to all SystemEventQueue events and forward them.

        Args:
            eq: A SystemEventQueue instance.
        """
        if self._eq_attached:
            return

        async def _on_eq_event(event: Any) -> None:
            envelope = self._normalise_eq_event(event)
            await self.push(envelope)

        eq.subscribe("*", _on_eq_event)
        self._eq_handler = _on_eq_event
        self._event_queue = eq
        self._eq_attached = True
        logger.info("EventBridge attached to SystemEventQueue")

    def detach_all(self) -> None:
        """Remove subscriptions from attached sources."""
        if self._ns_attached and self._nervous_system and self._ns_handler:
            self._nervous_system.unsubscribe_all(self._ns_handler)
            self._ns_attached = False
            logger.info("EventBridge detached from NervousSystem")

        if self._eq_attached and self._event_queue and self._eq_handler:
            self._event_queue.unsubscribe("*", self._eq_handler)
            self._eq_attached = False
            logger.info("EventBridge detached from SystemEventQueue")

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    def register_client(
        self,
        ws: WebSocketLike,
        subscription: SubscriptionFilter | None = None,
    ) -> None:
        """Register a WebSocket client with an optional subscription filter."""
        filt = subscription or SubscriptionFilter.accept_all()
        self._clients[id(ws)] = (ws, filt)
        self._stats["clients_registered"] += 1
        logger.debug("Client registered: %s filter=%s", id(ws), filt.to_dict())

    def unregister_client(self, ws: WebSocketLike) -> None:
        """Remove a WebSocket client."""
        self._clients.pop(id(ws), None)
        logger.debug("Client unregistered: %s", id(ws))

    def update_client_filter(self, ws: WebSocketLike, subscription: SubscriptionFilter) -> bool:
        """Update the subscription filter for an existing client."""
        key = id(ws)
        if key in self._clients:
            old_ws, _ = self._clients[key]
            self._clients[key] = (old_ws, subscription)
            return True
        return False

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ------------------------------------------------------------------
    # Core push pipeline
    # ------------------------------------------------------------------

    async def push(self, envelope: EventEnvelope) -> int:
        """
        Push an event envelope through the bridge.

        Returns the number of clients that received the event.
        """
        self._stats["events_received"] += 1

        # Store in history (deque auto-evicts oldest when maxlen is reached)
        self._history.append(envelope)

        # No clients → skip broadcast
        if not self._clients:
            return 0

        # Rate limit: suppress rapid-fire duplicates of same topic
        # dynamically based on serverity to protect UI from freezing
        if envelope.severity in (Severity.DEBUG, Severity.INFO):
            window = self.debounce_seconds
        elif envelope.severity == Severity.WARNING:
            window = 0.2
        else:  # ERROR, CRITICAL
            window = 0.0

        now = time.monotonic()
        last = self._recent.get(envelope.topic)
        if last is not None and (now - last) < window:
            self._stats["events_filtered"] += 1
            # Return early without updating last_time, creating a rate limit
            # instead of a trailing debounce that would indefinitely silence streams.
            return 0

        self._recent[envelope.topic] = now

        # Build JSON-RPC payload once
        payload_obj = envelope.to_jsonrpc_notification()
        payload_str = json.dumps(payload_obj, default=str)

        # Fast-path: offload to Go daemon via IPC
        if self.enable_ipc_offload:
            asyncio.create_task(self._forward_to_ipc(payload_str))

        # Truncate oversized payloads
        if len(payload_str.encode("utf-8", errors="replace")) > self.max_payload_bytes:
            payload_obj["params"] = {
                "id": envelope.id,
                "topic": envelope.topic,
                "severity": envelope.severity.value,
                "timestamp": envelope.timestamp.isoformat(),
                "truncated": True,
            }
            payload_str = json.dumps(payload_obj, default=str)

        # Broadcast to matching clients
        sent = 0
        dead: list[int] = []

        for key, (ws, filt) in list(self._clients.items()):
            if not filt.matches(envelope):
                self._stats["events_filtered"] += 1
                continue
            try:
                await asyncio.wait_for(ws.send(payload_str), timeout=self.broadcast_timeout)
                sent += 1
            except Exception:
                dead.append(key)
                self._stats["events_dropped"] += 1

        # Prune dead sockets
        for key in dead:
            self._clients.pop(key, None)

        self._stats["events_broadcast"] += sent
        return sent

    async def push_direct(
        self,
        topic: str,
        source: str,
        data: dict[str, Any] | None = None,
        severity: Severity = Severity.INFO,
    ) -> int:
        """Push an event directly (not from NervousSystem or SystemEventQueue)."""
        envelope = EventEnvelope(
            id=str(uuid4())[:8],
            topic=topic,
            source=source,
            severity=severity,
            timestamp=datetime.now(),
            data=data or {},
            origin="direct",
        )
        return await self.push(envelope)

    async def _forward_to_ipc(self, payload: str) -> None:
        """
        Forward event payload directly to the Go IPC daemon over domain sockets
        or named pipes, bypassing the heavier Python asyncio broadcast loop.
        """
        if not self.ipc_socket_path:
            return

        import sys

        try:
            if sys.platform == "win32":
                # asyncio.open_connection() cannot connect to Windows named pipes —
                # it is a TCP API and interprets the pipe path as a hostname, always
                # failing. Named-pipe IPC on Windows requires ProactorEventLoop's
                # create_pipe_connection helper which is not available in all
                # Python 3.10 builds. Skip gracefully until a proper implementation
                # is added.
                _logger = logging.getLogger("navig.event_bridge.ipc")
                _logger.debug("IPC offload skipped on Windows (named pipe not supported via asyncio)")
                return
            else:
                # Unix domain socket connection
                reader, writer = await asyncio.open_unix_connection(self.ipc_socket_path)

            writer.write(payload.encode("utf-8") + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            # Silently fail if native daemon isn't running yet
            _logger = logging.getLogger("navig.event_bridge.ipc")
            _logger.debug("IPC offload skipped: %s", e)

    # ------------------------------------------------------------------
    # Normalisation helpers
    # ------------------------------------------------------------------

    def _normalise_ns_event(self, event: Any) -> EventEnvelope:
        """Convert a NervousSystem Event to EventEnvelope."""
        type_name = event.type.name if hasattr(event.type, "name") else str(event.type)
        topic = _EVENT_TYPE_TOPIC_MAP.get(type_name, f"agent.{type_name.lower()}")

        priority_name = event.priority.name if hasattr(event.priority, "name") else "NORMAL"
        severity = _EVENT_TYPE_SEVERITY_HINTS.get(
            type_name, _PRIORITY_TO_SEVERITY.get(priority_name, Severity.INFO)
        )

        return EventEnvelope(
            id=getattr(event, "id", str(uuid4())[:8]),
            topic=topic,
            source=getattr(event, "source", "unknown"),
            severity=severity,
            timestamp=getattr(event, "timestamp", datetime.now()),
            data=getattr(event, "data", {}),
            origin="nervous_system",
        )

    def _normalise_eq_event(self, event: Any) -> EventEnvelope:
        """Convert a SystemEventQueue SystemEvent to EventEnvelope."""
        event_type = getattr(event, "event_type", "unknown")
        # Convert underscore-style to dot-delimited: "host_check" → "host.check"
        topic = event_type.replace("_", ".")

        priority_name = (
            event.priority.name
            if hasattr(event, "priority") and hasattr(event.priority, "name")
            else "NORMAL"
        )
        severity = _SYS_PRIORITY_TO_SEVERITY.get(priority_name, Severity.INFO)

        return EventEnvelope(
            id=getattr(event, "id", str(uuid4())[:8]),
            topic=topic,
            source=getattr(event, "payload", {}).get("source", "gateway"),
            severity=severity,
            timestamp=getattr(event, "timestamp", datetime.now()),
            data=getattr(event, "payload", {}),
            origin="system_event_queue",
        )

    # ------------------------------------------------------------------
    # History / stats
    # ------------------------------------------------------------------

    def get_history(
        self,
        *,
        topic: str | None = None,
        severity: Severity | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[EventEnvelope]:
        """Query bridge event history with optional filters."""
        events: list[EventEnvelope] = list(self._history)

        if topic:
            pat = re.compile("^" + re.escape(topic).replace(r"\*", ".*") + "$")
            events = [e for e in events if pat.match(e.topic)]

        if severity:
            events = [e for e in events if e.severity == severity]

        if source:
            events = [e for e in events if e.source == source]

        return events[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """Return bridge statistics."""
        return {
            **self._stats,
            "active_clients": len(self._clients),
            "history_size": len(self._history),
            "ns_attached": self._ns_attached,
            "eq_attached": self._eq_attached,
        }

    def reset_stats(self) -> None:
        """Reset counters (not history)."""
        for k in self._stats:
            self._stats[k] = 0

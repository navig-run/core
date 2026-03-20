"""NAVIG Blackbox — Event Type Definitions.

All data models for the blackbox recorder subsystem.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

__all__ = ["EventType", "BlackboxEvent", "Bundle"]


class EventType(str, Enum):
    """Classification of a recorded blackbox event."""

    COMMAND = "command"    # CLI command invocation
    CRASH   = "crash"      # Unhandled exception or signal
    ERROR   = "error"      # Caught error (non-crash)
    WARNING = "warning"    # Warnings and deprecations
    OUTPUT  = "output"     # Command stdout/stderr snapshot
    SESSION = "session"    # Session start / end
    SYSTEM  = "system"     # OS/daemon-level events


# Severity ordering for display color coding
_SEVERITY: dict[EventType, int] = {
    EventType.SYSTEM:  0,
    EventType.SESSION: 1,
    EventType.COMMAND: 2,
    EventType.OUTPUT:  3,
    EventType.WARNING: 4,
    EventType.ERROR:   5,
    EventType.CRASH:   6,
}


@dataclass
class BlackboxEvent:
    """A single recorded event."""

    id:          str
    event_type:  EventType
    timestamp:   datetime
    payload:     dict[str, Any]
    tags:        list[str] = field(default_factory=list)
    source:      str        = "navig"

    @staticmethod
    def create(
        event_type: EventType,
        payload: dict[str, Any],
        tags: list[str] | None = None,
        source: str = "navig",
    ) -> "BlackboxEvent":
        return BlackboxEvent(
            id=str(uuid.uuid4())[:8],
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            payload=payload,
            tags=tags or [],
            source=source,
        )

    def to_json(self) -> str:
        return json.dumps({
            "id":         self.id,
            "event_type": self.event_type.value,
            "timestamp":  self.timestamp.isoformat(),
            "payload":    self.payload,
            "tags":       self.tags,
            "source":     self.source,
        }, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict) -> "BlackboxEvent":
        return cls(
            id=data["id"],
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=data.get("payload", {}),
            tags=data.get("tags", []),
            source=data.get("source", "navig"),
        )

    def severity(self) -> int:
        return _SEVERITY.get(self.event_type, 0)


@dataclass
class Bundle:
    """A sealed collection of events and crash reports for export / investigation."""

    id:             str
    created_at:     datetime
    navig_version:  str
    events:         list[BlackboxEvent]
    crash_reports:  list[dict[str, Any]]
    log_tails:      dict[str, str]       # filename → last N lines
    manifest_hash:  str                  # SHA-256 of serialised content
    sealed:         bool = False

    def event_count(self) -> int:
        return len(self.events)

    def crash_count(self) -> int:
        return len(self.crash_reports)

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

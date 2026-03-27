"""
Mission — the unit of autonomous work in the NAVIG runtime.

A Mission is a discrete, trackable work item assigned to a Node.
It has a durable state machine with clear lifecycle transitions and
produces an ExecutionReceipt on completion.

State machine:
    queued → running → succeeded
                     → failed
           → cancelled   (from queued or running)
           → timed_out   (from running)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

# ── Enums ──────────────────────────────────────────────────────────────────────


class MissionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class MissionPriority(int, Enum):
    CRITICAL = 0
    HIGH = 10
    NORMAL = 50
    LOW = 100


# ── Terminal states ────────────────────────────────────────────────────────────

TERMINAL_STATES = frozenset(
    {
        MissionStatus.SUCCEEDED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
        MissionStatus.TIMED_OUT,
    }
)

# Valid state transitions  (from_state → set of allowed to_states)
ALLOWED_TRANSITIONS: Dict[MissionStatus, frozenset] = {
    MissionStatus.QUEUED: frozenset({MissionStatus.RUNNING, MissionStatus.CANCELLED}),
    MissionStatus.RUNNING: frozenset(
        {
            MissionStatus.SUCCEEDED,
            MissionStatus.FAILED,
            MissionStatus.CANCELLED,
            MissionStatus.TIMED_OUT,
        }
    ),
    MissionStatus.SUCCEEDED: frozenset(),
    MissionStatus.FAILED: frozenset({MissionStatus.QUEUED}),  # retry
    MissionStatus.CANCELLED: frozenset(),
    MissionStatus.TIMED_OUT: frozenset({MissionStatus.QUEUED}),  # retry after timeout
}


# ── Model ──────────────────────────────────────────────────────────────────────


@dataclass
class Mission:
    """
    Typed Mission contract with enforced state machine.

    Attributes:
        mission_id:    Globally unique identifier (UUID4).
        title:         Short human-readable description.
        node_id:       Target Node responsible for execution.
        capability:    Required capability slug (e.g. "llm", "ssh").
        payload:       Arbitrary input data for the mission handler.
        priority:      Scheduling priority (lower = higher urgency).
        status:        Current lifecycle state.
        result:        Output data on success.
        error:         Error message on failure or timeout.
        created_at:    ISO-8601 timestamp when mission was created.
        queued_at:     ISO-8601 timestamp when mission entered the queue.
        started_at:    ISO-8601 timestamp when execution began.
        completed_at:  ISO-8601 timestamp when mission reached a terminal state.
        timeout_secs:  Optional hard deadline in seconds (None = unlimited).
        tags:          Operator-defined tags for filtering / grouping.
        metadata:      Extension point for operator-defined fields.
    """

    # Required
    title: str

    # Identity
    mission_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    node_id: Optional[str] = None
    capability: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: int = MissionPriority.NORMAL.value

    # State
    status: MissionStatus = MissionStatus.QUEUED

    # Output
    result: Optional[Any] = None
    error: Optional[str] = None

    # Timestamps
    created_at: str = field(default_factory=lambda: _now_iso())
    queued_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Config
    timeout_secs: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── State machine ─────────────────────────────────────────────────

    def _transition(self, target: MissionStatus) -> None:
        allowed = ALLOWED_TRANSITIONS.get(self.status, frozenset())
        if target not in allowed:
            raise ValueError(
                f"Invalid transition {self.status.value!r} → {target.value!r} "
                f"(allowed: {[s.value for s in allowed]})"
            )
        self.status = target

    def start(self) -> None:
        """Begin execution."""
        self._transition(MissionStatus.RUNNING)
        self.started_at = _now_iso()

    def succeed(self, result: Any = None) -> None:
        """Mark as successfully completed."""
        self._transition(MissionStatus.SUCCEEDED)
        self.result = result
        self.completed_at = _now_iso()

    def fail(self, error: str) -> None:
        """Mark as failed with an error message."""
        self._transition(MissionStatus.FAILED)
        self.error = error
        self.completed_at = _now_iso()

    def cancel(self, reason: str = "operator request") -> None:
        """Cancel from queued or running state."""
        self._transition(MissionStatus.CANCELLED)
        self.error = reason
        self.completed_at = _now_iso()

    def timeout(self) -> None:
        """Mark as timed out."""
        self._transition(MissionStatus.TIMED_OUT)
        self.error = "Execution exceeded timeout limit"
        self.completed_at = _now_iso()

    def retry(self) -> None:
        """Re-queue a failed or timed-out mission."""
        self._transition(MissionStatus.QUEUED)
        self.error = None
        self.result = None
        self.started_at = None
        self.completed_at = None
        self.queued_at = _now_iso()

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def duration_secs(self) -> Optional[float]:
        """Wall-clock duration from start to completion, or None if not complete."""
        if self.started_at and self.completed_at:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
            return (end - start).total_seconds()
        return None

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Mission":
        data = dict(data)
        data["status"] = MissionStatus(data.get("status", "queued"))
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> "Mission":
        return cls.from_dict(json.loads(raw))

    def __repr__(self) -> str:
        return f"<Mission {self.mission_id[:8]} {self.title!r} [{self.status.value}]>"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()

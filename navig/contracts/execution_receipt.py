"""
ExecutionReceipt — the immutable audit record produced by every Mission.

A receipt is created when a Mission reaches a terminal state.
It is append-only: once created it cannot be mutated.
Receipts feed the TrustScore computation for Nodes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from navig.core.dict_utils import now_iso

# ── Enums ──────────────────────────────────────────────────────────────────────


class ReceiptOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


# ── Model ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)  # Immutable — receipts are append-only
class ExecutionReceipt:
    """
    Immutable record of a completed Mission execution.

    Attributes:
        receipt_id:    Globally unique identifier (UUID4).
        mission_id:    Reference to the originating Mission.
        node_id:       Node that executed the mission.
        title:         Mission title (denormalised for quick display).
        capability:    Capability used during execution.
        outcome:       Terminal outcome of the mission.
        started_at:    ISO-8601 when execution began (None if never started).
        completed_at:  ISO-8601 when the terminal state was reached.
        duration_secs: Wall-clock execution time in seconds (None if not started).
        error:         Error message for failed/cancelled/timed_out outcomes.
        artifacts:     Named output artifacts produced during execution.
        metadata:      Operator-defined extension fields.
        recorded_at:   ISO-8601 when this receipt was created.
    """

    mission_id: str
    node_id: str
    title: str
    capability: str
    outcome: ReceiptOutcome
    completed_at: str

    # Optional
    receipt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str | None = None
    duration_secs: float | None = None
    error: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(default_factory=lambda: now_iso())

    # ── Factory method ───────────────────────────────────────────────

    @classmethod
    def from_mission(
        cls,
        mission_id: str,
        node_id: str,
        title: str,
        capability: str,
        outcome: ReceiptOutcome,
        completed_at: str,
        started_at: str | None = None,
        duration_secs: float | None = None,
        error: str | None = None,
        artifacts: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionReceipt:
        return cls(
            mission_id=mission_id,
            node_id=node_id,
            title=title,
            capability=capability,
            outcome=outcome,
            completed_at=completed_at,
            started_at=started_at,
            duration_secs=duration_secs,
            error=error,
            artifacts=artifacts or {},
            metadata=metadata or {},
        )

    # ── Convenience predicates ────────────────────────────────────────

    @property
    def is_success(self) -> bool:
        return self.outcome == ReceiptOutcome.SUCCEEDED

    @property
    def is_failure(self) -> bool:
        return self.outcome in (ReceiptOutcome.FAILED, ReceiptOutcome.TIMED_OUT)

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionReceipt:
        data = dict(data)
        data["outcome"] = ReceiptOutcome(data["outcome"])
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> ExecutionReceipt:
        return cls.from_dict(json.loads(raw))

    def __repr__(self) -> str:
        return (
            f"<ExecutionReceipt {self.receipt_id[:8]} "
            f"mission={self.mission_id[:8]} "
            f"node={self.node_id[:8]} "
            f"outcome={self.outcome.value}>"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


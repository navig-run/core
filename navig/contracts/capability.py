"""
Capability and TrustScore contracts.

A Capability is a named, versioned skill that a Node declares it can handle.
TrustScore is a computed aggregate reputation metric [0.0–1.0] derived from
a Node's ExecutionReceipt history.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# ── Capability ────────────────────────────────────────────────────────────────


@dataclass
class Capability:
    """
    A named capability that a Node can declare and serve.

    Attributes:
        slug:        Machine-readable identifier, e.g. "llm", "ssh", "browser".
        version:     Semantic version string of the capability implementation.
        description: Short human-readable description.
        parameters:  JSON-schema-compatible dict describing accepted parameters.
        metadata:    Extension point for operator-defined fields.
    """

    slug: str
    version: str = "1.0.0"
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Capability":
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> "Capability":
        return cls.from_dict(json.loads(raw))

    def __repr__(self) -> str:
        return f"<Capability {self.slug!r} v{self.version}>"


# ── TrustScore ─────────────────────────────────────────────────────────────────


@dataclass
class TrustScore:
    """
    Aggregate reputation metric for a Node, computed from ExecutionReceipts.

    Score is always clamped to [0.0, 1.0].
    Higher is better.

    Attributes:
        node_id:           Node this score belongs to.
        score:             Current trust value [0.0–1.0].
        total_missions:    Total missions included in the computation.
        success_count:     Number of SUCCEEDED missions.
        failure_count:     Number of FAILED or TIMED_OUT missions.
        cancel_count:      Number of CANCELLED missions.
        avg_duration_secs: Mean wall-clock time across completed missions.
        computed_at:       ISO-8601 timestamp of last recomputation.
    """

    node_id: str
    score: float = 1.0
    total_missions: int = 0
    success_count: int = 0
    failure_count: int = 0
    cancel_count: int = 0
    avg_duration_secs: Optional[float] = None
    computed_at: Optional[str] = None

    def __post_init__(self) -> None:
        self.score = max(0.0, min(1.0, self.score))

    @property
    def success_rate(self) -> float:
        if self.total_missions == 0:
            return 1.0
        return self.success_count / self.total_missions

    @classmethod
    def compute(
        cls,
        node_id: str,
        receipts: List[Any],  # List[ExecutionReceipt] — avoid circular import
    ) -> "TrustScore":
        """
        Compute a TrustScore from a list of ExecutionReceipts.

        Algorithm:
            score = success_rate * decay_factor
        where decay_factor penalises nodes with very few observations
        (converges to 1 for nodes with 20+ missions, starts at 0.5 for new nodes).
        """
        from datetime import datetime, timezone

        from navig.contracts.execution_receipt import ReceiptOutcome

        total = len(receipts)
        success = sum(1 for r in receipts if r.outcome == ReceiptOutcome.SUCCEEDED)
        failures = sum(
            1
            for r in receipts
            if r.outcome in (ReceiptOutcome.FAILED, ReceiptOutcome.TIMED_OUT)
        )
        cancels = sum(1 for r in receipts if r.outcome == ReceiptOutcome.CANCELLED)

        durations = [r.duration_secs for r in receipts if r.duration_secs is not None]
        avg_dur = sum(durations) / len(durations) if durations else None

        raw_rate = success / total if total > 0 else 1.0

        # Bayesian-ish decay: start conservative for new nodes
        # Converges to raw_rate as total → ∞
        decay = total / (total + 10)  # 0 → 0.0, 10 → 0.5, 20 → 0.67, 100 → 0.91
        score = raw_rate * decay + 0.5 * (1 - decay)  # blend toward 0.5 prior

        return cls(
            node_id=node_id,
            score=score,
            total_missions=total,
            success_count=success,
            failure_count=failures,
            cancel_count=cancels,
            avg_duration_secs=avg_dur,
            computed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrustScore":
        return cls(**data)

    @classmethod
    def from_json(cls, raw: str) -> "TrustScore":
        return cls.from_dict(json.loads(raw))

    def __repr__(self) -> str:
        return (
            f"<TrustScore node={self.node_id[:8]} "
            f"score={self.score:.2f} "
            f"({self.success_count}/{self.total_missions})>"
        )

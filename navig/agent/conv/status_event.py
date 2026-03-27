"""Defines the StatusEvent dataclass used by ConversationalAgent to emit structured lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Literal, Optional


@dataclass
class StatusEvent:
    """Structured lifecycle event emitted by ConversationalAgent and TaskExecutor."""

    type: Literal[
        "task_start",
        "step_start",
        "step_done",
        "step_failed",
        "task_done",
        "thinking",
        "streaming_token",
    ]
    task_id: str
    message: str
    timestamp: datetime
    step_index: Optional[int] = None
    total_steps: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

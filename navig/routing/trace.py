"""
Route trace — structured telemetry for every LLM completion.

Every call through the unified router produces a RouteTrace logged
in JSONL format to ~/.navig/logs/router_traces.jsonl.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

TRACE_LOG_PATH = Path.home() / ".navig" / "logs" / "router_traces.jsonl"


@dataclass
class RouteTrace:
    """Structured trace for a single routed LLM call."""

    trace_id: str = ""
    timestamp: float = 0.0

    # Classification
    mode: str = ""
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    capability_profile: str = ""

    # Provider selection
    provider: str = ""
    model: str = ""
    fallbacks_tried: List[str] = field(default_factory=list)

    # Execution
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    # Audit
    audit_result: str = ""  # "pass", "retry_1", "retry_2", "failed"
    tools_used: List[str] = field(default_factory=list)

    # Context
    entrypoint: str = ""  # "forge_chat", "telegram", "cli", "mcp", "http"
    purpose_sent: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def log_trace(trace: RouteTrace) -> None:
    """Append a trace to the JSONL log file."""
    try:
        TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TRACE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace.to_dict(), default=str) + "\n")
    except Exception as e:
        logger.debug("Failed to write route trace: %s", e)


def recent_traces(limit: int = 50) -> List[Dict[str, Any]]:
    """Read the most recent N traces from the JSONL log."""
    if not TRACE_LOG_PATH.exists():
        return []
    try:
        lines = TRACE_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
        traces = []
        for line in lines[-limit:]:
            if line.strip():
                traces.append(json.loads(line))
        return traces
    except Exception:
        return []

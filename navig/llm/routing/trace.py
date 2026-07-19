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
from typing import Any

from navig.platform import paths

logger = logging.getLogger(__name__)

# Test seam — when ``None`` (the normal state), ``_trace_log_path()`` resolves
# at CALL time so NAVIG_CONFIG_DIR isolation set after import still applies
# (see navig/vault/migrate.py:_legacy_db_path).
TRACE_LOG_PATH: Path | None = None


def _trace_log_path() -> Path:
    """Resolve the trace-log path at call time (honours the test seam)."""
    return (
        TRACE_LOG_PATH
        if TRACE_LOG_PATH is not None
        else paths.config_dir() / "logs" / "router_traces.jsonl"
    )


@dataclass
class RouteTrace:
    """Structured trace for a single routed LLM call."""

    trace_id: str = ""
    timestamp: float = 0.0

    # Classification
    mode: str = ""
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    capability_profile: str = ""

    # Provider selection
    provider: str = ""
    model: str = ""
    fallbacks_tried: list[str] = field(default_factory=list)

    # Execution
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    # Audit
    audit_result: str = ""  # "pass", "retry_1", "retry_2", "failed"
    tools_used: list[str] = field(default_factory=list)

    # Context
    entrypoint: str = ""  # "forge_chat", "telegram", "cli", "mcp", "http"
    purpose_sent: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def log_trace(trace: RouteTrace) -> None:
    """Append a trace to the JSONL log file."""
    try:
        trace_path = _trace_log_path()
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace.to_dict(), default=str) + "\n")
    except Exception as e:
        logger.debug("Failed to write route trace: %s", e)


def recent_traces(limit: int = 50) -> list[dict[str, Any]]:
    """Read the most recent N traces from the JSONL log."""
    trace_path = _trace_log_path()
    if not trace_path.exists():
        return []
    try:
        lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
        traces = []
        for line in lines[-limit:]:
            if line.strip():
                traces.append(json.loads(line))
        return traces
    except Exception:
        return []

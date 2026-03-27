"""
JSON API Tool Output Contract — Standardized response envelope.

Every JSON API tool MUST return an ``ApiToolResult`` that wraps:
  - status    : "ok" | "error"
  - raw_json  : unmodified API payload (NEVER sent to LLM or memory by default)
  - normalized: minimal, cleaned subset the LLM can reason over
  - source    : provenance (tool name, endpoint, timestamp)
  - error     : human-readable error string (or None)

Usage:
    from navig.tools.api_schema import ApiToolResult, ApiSource

    result = ApiToolResult(
        status="ok",
        raw_json=response.json(),
        normalized={"price": 42000.0, "symbol": "BTC/USD"},
        source=ApiSource(tool="trading.fetch.ohlc", endpoint="https://..."),
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ─────────────────────────────────────────────────────────────
# Sensitive-field patterns (for redaction)
# ─────────────────────────────────────────────────────────────

_SENSITIVE_KEYS_RE = re.compile(
    r"(?i)"
    r"(api[_-]?key|secret|token|password|passwd|auth|credential"
    r"|private[_-]?key|access[_-]?key|session[_-]?id|cookie"
    r"|ssn|social[_-]?security|credit[_-]?card|card[_-]?number"
    r"|cvv|expir[ey]|pin|otp"
    r"|email|phone|address|birth|dob|ip[_-]?addr)"
)

_REDACTED = "***REDACTED***"


def redact_sensitive(
    data: Any,
    *,
    extra_keys: set[str] | None = None,
) -> Any:
    """
    Recursively redact values whose keys match sensitive patterns.

    Args:
        data: Any JSON-serializable structure.
        extra_keys: Optional additional key names to redact.

    Returns:
        Deep copy with sensitive values replaced by ``***REDACTED***``.
    """
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if (
                _SENSITIVE_KEYS_RE.search(str(k))
                or extra_keys
                and str(k).lower() in extra_keys
            ):
                out[k] = _REDACTED
            else:
                out[k] = redact_sensitive(v, extra_keys=extra_keys)
        return out
    if isinstance(data, list):
        return [redact_sensitive(item, extra_keys=extra_keys) for item in data]
    return data


# ─────────────────────────────────────────────────────────────
# Core data classes
# ─────────────────────────────────────────────────────────────


@dataclass
class ApiSource:
    """Provenance metadata for an API tool result."""

    tool: str
    endpoint: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class ApiToolResult:
    """
    Standardized envelope returned by every JSON API tool.

    Fields:
        status     : "ok" | "error"
        raw_json   : Unmodified API response — NEVER persisted to memory by default.
        normalized : Cleaned, minimal subset safe for LLM + memory.
        source     : Provenance (tool name, endpoint, timestamp).
        error      : Human-readable error string or None on success.
    """

    status: str = "ok"  # "ok" | "error"
    raw_json: dict[str, Any] = field(default_factory=dict)
    normalized: dict[str, Any] | list[Any] = field(default_factory=dict)
    source: ApiSource = field(default_factory=lambda: ApiSource(tool="unknown"))
    error: str | None = None

    # -- helpers --

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.error is None

    def to_dict(self) -> dict[str, Any]:
        """Full serialization (including raw_json)."""
        return {
            "status": self.status,
            "raw_json": self.raw_json,
            "normalized": self.normalized,
            "source": {
                "tool": self.source.tool,
                "endpoint": self.source.endpoint,
                "timestamp": self.source.timestamp,
            },
            "error": self.error,
        }

    def to_snapshot_dict(self) -> dict[str, Any]:
        """
        Serialization for memory snapshots — excludes raw_json, redacts sensitive fields.
        """
        return {
            "status": self.status,
            "normalized": redact_sensitive(self.normalized),
            "source": {
                "tool": self.source.tool,
                "endpoint": self.source.endpoint,
                "timestamp": self.source.timestamp,
            },
            "error": self.error,
        }

    def to_llm_dict(self) -> dict[str, Any]:
        """
        Serialization for LLM context injection — normalized + source.timestamp only.
        Never includes raw_json.
        """
        return {
            "data": self.normalized,
            "tool": self.source.tool,
            "fetched_at": self.source.timestamp,
            "status": self.status,
        }

    @classmethod
    def from_error(cls, tool: str, error: str, endpoint: str = "") -> ApiToolResult:
        """Convenience factory for error results."""
        return cls(
            status="error",
            error=error,
            source=ApiSource(tool=tool, endpoint=endpoint),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiToolResult:
        """Reconstruct from serialized form (e.g. snapshot file)."""
        src = data.get("source", {})
        return cls(
            status=data.get("status", "ok"),
            raw_json=data.get("raw_json", {}),
            normalized=data.get("normalized", {}),
            source=ApiSource(
                tool=src.get("tool", "unknown"),
                endpoint=src.get("endpoint", ""),
                timestamp=src.get("timestamp", ""),
            ),
            error=data.get("error"),
        )


# ─────────────────────────────────────────────────────────────
# Validation helper
# ─────────────────────────────────────────────────────────────


def validate_api_result(result: ApiToolResult) -> list[str]:
    """
    Validate an ApiToolResult, returning a list of issues (empty = valid).

    Checks:
      - status is "ok" or "error"
      - source.tool is non-empty
      - normalized is dict or list
      - if error status, error message is present
    """
    issues: list[str] = []
    if result.status not in ("ok", "error"):
        issues.append(f"Invalid status: {result.status!r} (expected 'ok' or 'error')")
    if not result.source.tool:
        issues.append("source.tool is empty")
    if not isinstance(result.normalized, (dict, list)):
        issues.append(
            f"normalized must be dict or list, got {type(result.normalized).__name__}"
        )
    if result.status == "error" and not result.error:
        issues.append("status is 'error' but error message is empty")
    return issues

"""
Anthropic Claude **subscription OAuth** request rules.

A Claude.ai OAuth token (Pro/Max subscription) is rejected by
``POST https://api.anthropic.com/v1/messages`` — often as a *misleading*
``rate_limit_error`` — unless the request presents as the official Claude Code
CLI. This module holds the exact, verified rules as pure functions so they are
trivially testable and shared by streaming + non-streaming paths.

Requirements for an OAuth token (NOT needed for API keys):
  * ``authorization: Bearer <token>``  (never send ``x-api-key``)
  * ``anthropic-version: 2023-06-01``
  * ``anthropic-beta: oauth-2025-04-20``
  * ``user-agent: claude-cli/<ver> (external, cli)`` and ``x-app: cli``
  * ``system`` must be an ARRAY whose FIRST block is EXACTLY the Claude Code
    identity block; the caller's own system prompt goes in the second block.
  * Retry 429/529/5xx with exponential backoff honoring ``Retry-After``; also
    handle the mid-stream SSE ``event: error`` (rate_limit / overloaded).
"""

from __future__ import annotations

import os
import random
from typing import Any

# The CLI version NAVIG presents as. Format matters more than the exact number;
# overridable so it can track the real CLI without a code change.
CLAUDE_CLI_VERSION = os.environ.get("NAVIG_CLAUDE_CLI_VERSION", "1.0.119")

#: The required first system block — must be present EXACTLY, verbatim.
CLAUDE_CODE_SYSTEM_TEXT = "You are Claude Code, Anthropic's official CLI for Claude."
CLAUDE_CODE_SYSTEM_BLOCK: dict[str, Any] = {"type": "text", "text": CLAUDE_CODE_SYSTEM_TEXT}

ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"
ANTHROPIC_VERSION = "2023-06-01"

# Statuses worth retrying with backoff. 529 is Anthropic's "overloaded".
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
MAX_RETRIES = 5


def build_oauth_headers(token: str) -> dict[str, str]:
    """Headers that make an OAuth-token request look like the official CLI.

    Deliberately omits ``x-api-key`` — sending both confuses Anthropic's auth.
    """
    return {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": ANTHROPIC_OAUTH_BETA,
        "user-agent": f"claude-cli/{CLAUDE_CLI_VERSION} (external, cli)",
        "x-app": "cli",
    }


def frame_oauth_system(user_system: Any) -> list[dict[str, Any]]:
    """Return a system ARRAY whose FIRST block is exactly the Claude Code block.

    The caller's own system prompt (string OR already-blocked array) follows.
    Any caller-supplied block that duplicates the identity text is dropped so the
    identity block is present exactly once, first.
    """
    blocks: list[dict[str, Any]] = [dict(CLAUDE_CODE_SYSTEM_BLOCK)]
    if user_system is None or user_system == "":
        return blocks
    if isinstance(user_system, str):
        blocks.append({"type": "text", "text": user_system})
        return blocks
    if isinstance(user_system, list):
        for b in user_system:
            # skip an accidental duplicate identity block
            if isinstance(b, dict) and b.get("text") == CLAUDE_CODE_SYSTEM_TEXT:
                continue
            blocks.append(b)
        return blocks
    # Unknown shape — stringify defensively.
    blocks.append({"type": "text", "text": str(user_system)})
    return blocks


def is_retryable_status(status: int) -> bool:
    return status in RETRYABLE_STATUSES


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form). Returns seconds or None."""
    if not value:
        return None
    try:
        secs = float(value.strip())
        return max(0.0, secs)
    except (ValueError, AttributeError):
        return None  # HTTP-date form is rare here; fall back to backoff


def compute_backoff(attempt: int, retry_after: float | None = None, *, base: float = 0.5,
                    cap: float = 30.0) -> float:
    """Exponential backoff with full jitter; honors ``Retry-After`` when present.

    ``attempt`` is 0-based (0 = first retry).
    """
    if retry_after is not None:
        return min(retry_after, cap)
    expo = min(cap, base * (2 ** attempt))
    return random.uniform(0.0, expo)  # full jitter


def sse_error(event: dict[str, Any]) -> tuple[str, str] | None:
    """Detect a mid-stream Anthropic SSE error event.

    Returns ``(error_type, message)`` for ``{"type":"error", ...}`` (e.g.
    rate_limit / overloaded), else ``None``.
    """
    if not isinstance(event, dict) or event.get("type") != "error":
        return None
    err = event.get("error") or {}
    return (str(err.get("type", "error")), str(err.get("message", "stream error")))

"""
Hook event types, context, and result dataclasses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HookEvent(str, Enum):
    """Lifecycle event emitted to hook scripts."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_DENIED = "PermissionDenied"
    NOTIFICATION = "Notification"
    SESSION_START = "SessionStart"


@dataclass
class HookContext:
    """Data passed to a hook script via stdin as a JSON object."""

    event: HookEvent
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    tool_error: str | None = None
    session_id: str = ""
    turn_id: str = ""
    # optional free-form metadata (e.g. classifier output, route decision)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "event": self.event.value,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
        }
        if self.tool_result is not None:
            payload["tool_result"] = self.tool_result
        if self.tool_error is not None:
            payload["tool_error"] = self.tool_error
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload, default=str)


@dataclass
class HookResult:
    """Aggregated result after running all hooks for a single event."""

    # If True, the tool call should be blocked (only meaningful for PRE_TOOL_USE).
    block: bool = False
    # Message injected into model context when block=True or exit-code == 2.
    message: str = ""
    # Whether at least one hook ran successfully.
    executed: bool = False
    # Whether the hook asked the model to retry after a PermissionDenied.
    retry: bool = False

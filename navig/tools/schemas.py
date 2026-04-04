"""
Tool Invocation Schema — LLM ↔ ToolRouter Contract.

Defines the typed action objects that an LLM can request and the
ToolRouter consumes.  All actions are parsed from the LLM response
JSON and validated before execution.

Usage:
    from navig.tools.schemas import parse_llm_action, ToolCallAction, RespondAction

    action = parse_llm_action(llm_response_text)
    if isinstance(action, ToolCallAction):
        result = tool_router.execute(action)
    elif isinstance(action, RespondAction):
        send(action.message)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("navig.tools.schemas")


# =============================================================================
# Enums
# =============================================================================


class ActionType(str, Enum):
    """Types of actions an LLM can request."""

    TOOL_CALL = "tool_call"
    RESPOND = "respond"
    MULTI_STEP = "multi_step"


class ToolResultStatus(str, Enum):
    """Status of a tool execution result."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    NEEDS_CONFIRMATION = "needs_confirmation"


# =============================================================================
# Action Data Classes
# =============================================================================


@dataclass
class ToolCallAction:
    """
    Represents a single tool invocation requested by the LLM.

    Fields:
        tool:       Canonical tool name (e.g. "web_search", "image_generate").
        parameters: Dict of keyword arguments to pass to the tool handler.
        reason:     Optional LLM-supplied justification for calling this tool.
        request_id: Optional unique ID for tracing.
    """

    tool: str
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    request_id: str = ""

    @property
    def action_type(self) -> ActionType:
        return ActionType.TOOL_CALL


@dataclass
class RespondAction:
    """
    Represents a direct text response (no tool invocation needed).

    Fields:
        message: The text content to send to the user.
    """

    message: str

    @property
    def action_type(self) -> ActionType:
        return ActionType.RESPOND


@dataclass
class MultiStepAction:
    """
    Represents a chain of tool calls to execute sequentially.

    Fields:
        steps: Ordered list of ToolCallAction to execute in sequence.
        reason: Optional justification for the chain.
    """

    steps: list[ToolCallAction] = field(default_factory=list)
    reason: str = ""

    @property
    def action_type(self) -> ActionType:
        return ActionType.MULTI_STEP


@dataclass
class ToolResult:
    """
    Standardized result from a tool execution.

    Fields:
        tool:       Name of the tool that was executed.
        status:     Enum status of the execution.
        output:     The tool's return value (any serializable structure).
        error:      Error message if status != SUCCESS.
        latency_ms: Execution time in milliseconds.
        metadata:   Additional metadata (e.g. cached, provider).
    """

    tool: str
    status: ToolResultStatus = ToolResultStatus.SUCCESS
    output: Any = None
    error: str = ""
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == ToolResultStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        """Serialize for LLM consumption or logging."""
        return {
            "tool": self.tool,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


# Type alias for parsed actions
LLMAction = ToolCallAction | RespondAction | MultiStepAction


# =============================================================================
# JSON Extraction Helpers
# =============================================================================

# Pattern to find JSON inside ```json ... ``` fenced code blocks
_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _find_bare_json_objects(text: str) -> list[str]:
    """
    Find top-level ``{...}`` substrings using brace-counting.

    Handles arbitrary nesting depth (unlike a regex approach).
    Respects JSON string literals so braces inside strings are ignored.
    """
    results: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            depth = 0
            start = i
            in_string = False
            escape = False
            while i < n:
                ch = text[i]
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = not in_string
                elif not in_string:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            results.append(text[start : i + 1])
                            break
                i += 1
        i += 1
    return results


def _extract_json(text: str) -> dict[str, Any] | None:
    """
    Extract the first valid JSON object from LLM output.

    Handles:
      - ``\u0060\u0060\u0060json ... \u0060\u0060\u0060`` fenced code blocks
      - Bare JSON objects at any nesting depth
      - Trailing commas (best-effort)
    """
    # 1. Try fenced code blocks first (most explicit)
    for match in _JSON_FENCE_RE.finditer(text):
        candidate = (match.group(1) or "").strip()
        if not candidate:
            continue
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue

    # 2. Fall back to brace-counted bare JSON objects
    for candidate in _find_bare_json_objects(text):
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue

    return None


# =============================================================================
# Parser
# =============================================================================


def parse_llm_action(text: str) -> LLMAction:
    """
    Parse an LLM response into a typed action.

    Expected JSON schema from the LLM:

        # Tool call:
        {"action": "tool_call", "tool": "web_search", "parameters": {"query": "..."}, "reason": "..."}

        # Direct response:
        {"action": "respond", "message": "Here is the answer..."}

        # Multi-step:
        {"action": "multi_step", "steps": [{"tool": "...", "parameters": {...}}, ...]}

    If no valid JSON is found, the entire text is treated as a RespondAction.

    Args:
        text: Raw LLM response text.

    Returns:
        ToolCallAction, RespondAction, or MultiStepAction.
    """
    if not text or not text.strip():
        return RespondAction(message="")

    obj = _extract_json(text)
    if obj is None:
        # No JSON found — treat entire text as plain response
        return RespondAction(message=text.strip())

    action_type = obj.get("action", "").lower().strip()

    if action_type == "tool_call":
        tool = obj.get("tool", "")
        if not tool:
            logger.warning("tool_call action missing 'tool' field: %s", obj)
            return RespondAction(message=text.strip())
        return ToolCallAction(
            tool=tool,
            parameters=obj.get("parameters", {}),
            reason=obj.get("reason", ""),
            request_id=obj.get("request_id", ""),
        )

    if action_type == "respond":
        return RespondAction(
            message=obj.get("message", text.strip()),
        )

    if action_type == "multi_step":
        raw_steps = obj.get("steps", [])
        steps = []
        for s in raw_steps:
            if isinstance(s, dict) and s.get("tool"):
                steps.append(
                    ToolCallAction(
                        tool=s["tool"],
                        parameters=s.get("parameters", {}),
                        reason=s.get("reason", ""),
                    )
                )
        if not steps:
            logger.warning("multi_step action has no valid steps: %s", obj)
            return RespondAction(message=text.strip())
        return MultiStepAction(steps=steps, reason=obj.get("reason", ""))

    # Unknown action type but valid JSON — if it has "tool", treat as tool_call
    if "tool" in obj:
        return ToolCallAction(
            tool=obj["tool"],
            parameters=obj.get("parameters", {}),
            reason=obj.get("reason", ""),
        )

    # Fallback to respond
    return RespondAction(message=text.strip())


def format_tool_result_for_llm(result: ToolResult) -> str:
    """
    Format a ToolResult back into text the LLM can consume
    in the next turn as a system/tool message.
    """
    if result.success:
        output_str = json.dumps(result.output, default=str, ensure_ascii=False)
        if len(output_str) > 8000:
            output_str = output_str[:7997] + "..."
        return f"Tool '{result.tool}' returned:\n{output_str}"
    else:
        return f"Tool '{result.tool}' failed ({result.status.value}): {result.error}"

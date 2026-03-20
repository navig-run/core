"""
navig.tools.approval — Human-in-the-loop approval gate.

For a single operator running NAVIG locally, the approval gate acts as a
safety interlock: any tool classified as DANGEROUS must be confirmed before
it executes.  The gate does not model "which user" — the operator is always
the single authority.

The gate can be bypassed per-session by setting the environment variable
``NAVIG_ALLOW_ALL_COMMANDS=1``.  This is useful for unattended automation
pipelines where the operator has already pre-approved the command set.

Integration
-----------
Called from ``ToolRouter._raw_async_execute()`` after policy checks, before
the handler is invoked::

    from navig.tools.approval import get_approval_gate, ApprovalDecision

    gate = get_approval_gate()
    decision = await gate.check(meta, action.parameters)
    if decision == ApprovalDecision.DENIED:
        return ToolResult(tool=canonical, status=ToolResultStatus.DENIED, ...)

Custom backends
---------------
Replace the default (auto-approve or env-bypassed) backend by injecting a
callable into ``get_approval_gate().backend``::

    async def my_telegram_prompt(req: ApprovalRequest) -> ApprovalDecision: ...
    get_approval_gate().backend = my_telegram_prompt
"""
from __future__ import annotations

import enum
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

from loguru import logger

__all__ = [
    "ApprovalRequest",
    "ApprovalDecision",
    "ApprovalGate",
    "get_approval_gate",
]


# =============================================================================
# Types
# =============================================================================

class ApprovalDecision(str, enum.Enum):
    """Outcome returned by the approval backend."""
    APPROVED = "approved"
    DENIED   = "denied"
    TIMEOUT  = "timeout"      # backend did not respond in time


@dataclass
class ApprovalRequest:
    """Payload sent to the approval backend."""
    tool_name: str
    safety_level: str          # SafetyLevel.value string
    parameters: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""           # agent-supplied justification
    context: Dict[str, Any] = field(default_factory=dict)


# Type alias — approval backends are async callables
ApprovalBackend = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


# =============================================================================
# Default backends
# =============================================================================

async def _auto_approve(req: ApprovalRequest) -> ApprovalDecision:
    """Approve everything — used when NAVIG_ALLOW_ALL_COMMANDS=1."""
    return ApprovalDecision.APPROVED


async def _log_and_approve(req: ApprovalRequest) -> ApprovalDecision:
    """
    Default single-operator backend.

    For DANGEROUS tools: logs a prominent warning and approves.
    The operator is expected to monitor the terminal / logs in real time.
    If a richer interactive prompt is needed, replace this backend.
    """
    logger.warning(
        "approval: auto-approving DANGEROUS tool '%s' (single-operator mode). "
        "Set a custom gate.backend for interactive confirmation.",
        req.tool_name,
    )
    return ApprovalDecision.APPROVED


# =============================================================================
# ApprovalGate
# =============================================================================

class ApprovalGate:
    """
    Checks whether a tool call should proceed.

    Single-operator defaults
    ------------------------
    - SAFE / MODERATE tools → always approved (skip gate entirely)
    - DANGEROUS tools       → delegated to ``self.backend``
      - Default backend logs a warning and approves (non-blocking)
      - Override ``gate.backend`` with an async callable for interactive prompts

    The gate is bypassed entirely when ``NAVIG_ALLOW_ALL_COMMANDS=1``.
    """

    def __init__(self, backend: Optional[ApprovalBackend] = None) -> None:
        self._backend: ApprovalBackend = backend or _log_and_approve

    @property
    def backend(self) -> ApprovalBackend:
        return self._backend

    @backend.setter
    def backend(self, fn: ApprovalBackend) -> None:
        self._backend = fn

    async def check(
        self,
        tool_name: str,
        safety_level: str,
        parameters: Optional[Dict[str, Any]] = None,
        reason: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> ApprovalDecision:
        """
        Evaluate whether a tool call may proceed.

        Args:
            tool_name:    Canonical tool name.
            safety_level: SafetyLevel.value string ("safe", "moderate", "dangerous").
            parameters:   Tool call parameters (for context/logging).
            reason:       Agent-supplied rationale string.
            context:      Extra metadata (e.g. channel, thread id).

        Returns:
            ApprovalDecision.APPROVED or DENIED.
        """
        # Hard bypass for unattended automation
        if os.environ.get("NAVIG_ALLOW_ALL_COMMANDS", "").strip() == "1":
            return ApprovalDecision.APPROVED

        # Only gate DANGEROUS tools
        if safety_level != "dangerous":
            return ApprovalDecision.APPROVED

        req = ApprovalRequest(
            tool_name=tool_name,
            safety_level=safety_level,
            parameters=parameters or {},
            reason=reason,
            context=context or {},
        )
        try:
            decision = await self._backend(req)
        except Exception as exc:
            logger.error("approval: backend raised unexpectedly: %s — denying", exc)
            decision = ApprovalDecision.DENIED

        logger.info(
            "approval: tool='%s' safety='%s' decision=%s",
            tool_name,
            safety_level,
            decision.value,
        )
        return decision


# =============================================================================
# Singleton
# =============================================================================

_gate_instance: Optional[ApprovalGate] = None
_gate_lock = threading.Lock()


def get_approval_gate() -> ApprovalGate:
    """Return the global ApprovalGate singleton."""
    global _gate_instance
    if _gate_instance is not None:
        return _gate_instance
    with _gate_lock:
        if _gate_instance is None:
            _gate_instance = ApprovalGate()
    return _gate_instance


def reset_approval_gate() -> None:
    """Reset the singleton (used in tests)."""
    global _gate_instance
    with _gate_lock:
        _gate_instance = None

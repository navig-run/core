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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

__all__ = [
    "ApprovalRequest",
    "ApprovalDecision",
    "ApprovalPolicy",
    "ApprovalGate",
    "get_approval_gate",
    "needs_approval",
    "set_approval_policy",
    "get_approval_policy",
]


# =============================================================================
# Types
# =============================================================================


class ApprovalDecision(str, enum.Enum):
    """Outcome returned by the approval backend."""

    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"  # backend did not respond in time


class ApprovalPolicy(str, enum.Enum):
    """Configures which tool calls the agent must confirm before executing.

    Policies (least-to-most restrictive):

    ``YOLO``
        No gates at all.  Every tool runs without confirmation.
        Suitable for fully-automated CI pipelines where the caller
        has pre-screened the command set.

    ``CONFIRM_DESTRUCTIVE``
        Default.  Confirms tools tagged as ``dangerous`` safety level, **or**
        whose name appears in :data:`DESTRUCTIVE_TOOLS`.

    ``CONFIRM_ALL``
        Requires confirmation for every tool call, including safe ones.
        Useful when demoing the agent to stakeholders or auditing behaviour.

    ``OWNER_ONLY``
        Like ``CONFIRM_DESTRUCTIVE`` but additionally restricts execution to
        tools whose ``owner_only=True`` flag is set in the tool metadata.  All
        others are auto-approved regardless of safety level.  Intended for
        privilege-separated installations.
    """

    YOLO = "yolo"
    CONFIRM_DESTRUCTIVE = "confirm_destructive"
    CONFIRM_ALL = "confirm_all"
    OWNER_ONLY = "owner_only"


# Tools that are considered destructive even if not tagged "dangerous".
# Additions here immediately affect CONFIRM_DESTRUCTIVE behaviour.
DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "bash_exec",
        "delete_file",
        "remove_file",
        "file_remove",
        "run_command",
        "db_query",
        "db_dump",
        "db_restore",
        "docker_exec",
        "web_reload",
        "host_maintenance",
        # DevOps agent tools (MVP3 F-16)
        "navig_run",
        "navig_db_query",
        "navig_db_dump",
        "navig_docker_exec",
        "navig_docker_restart",
        "navig_web_reload",
    }
)


@dataclass
class ApprovalRequest:
    """Payload sent to the approval backend."""

    tool_name: str
    safety_level: str  # SafetyLevel.value string
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""  # agent-supplied justification
    context: dict[str, Any] = field(default_factory=dict)


# Type alias — approval backends are async callables
ApprovalBackend = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


# =============================================================================
# Policy helpers
# =============================================================================

_policy: ApprovalPolicy = ApprovalPolicy.CONFIRM_DESTRUCTIVE
_policy_lock = threading.Lock()


def get_approval_policy() -> ApprovalPolicy:
    """Return the active approval policy for this process."""
    with _policy_lock:
        return _policy


def set_approval_policy(policy: ApprovalPolicy | str) -> None:
    """Set the active approval policy.

    Args:
        policy: An :class:`ApprovalPolicy` member or its string value
                (e.g. ``"yolo"`` or ``"confirm_all"``).
    """
    global _policy
    if isinstance(policy, str):
        policy = ApprovalPolicy(policy.lower())
    with _policy_lock:
        _policy = policy
    logger.debug("approval: policy set to %s", policy.value)


def needs_approval(
    tool_name: str,
    safety_level: str = "safe",
    args: dict | None = None,
    policy: ApprovalPolicy | None = None,
) -> bool:
    """Return True when the active policy requires human confirmation.

    This is a **synchronous** convenience predicate — it does not perform the
    confirmation itself (use :meth:`ApprovalGate.check` for that).  Callers
    can use this to short-circuit before building the full prompt.

    Args:
        tool_name:    Canonical tool name (e.g. ``"bash_exec"``).
        safety_level: ``"safe"``, ``"moderate"``, or ``"dangerous"``.
        args:         Tool parameters (reserved for future content inspection).
        policy:       Override the process-level policy for this check.

    Returns:
        ``True`` if the call should be held for human approval.
    """
    if os.environ.get("NAVIG_ALLOW_ALL_COMMANDS", "").strip() == "1":
        return False

    active_policy = policy or get_approval_policy()

    if active_policy == ApprovalPolicy.YOLO:
        return False

    if active_policy == ApprovalPolicy.CONFIRM_ALL:
        return True

    if active_policy == ApprovalPolicy.CONFIRM_DESTRUCTIVE:
        return safety_level == "dangerous" or tool_name in DESTRUCTIVE_TOOLS

    if active_policy == ApprovalPolicy.OWNER_ONLY:
        # Only approve tools NOT in DESTRUCTIVE_TOOLS and NOT dangerous.
        # Anything else requires approval.
        return safety_level == "dangerous" or tool_name in DESTRUCTIVE_TOOLS

    return False  # unknown policy → be permissive


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

    def __init__(self, backend: ApprovalBackend | None = None) -> None:
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
        parameters: dict[str, Any] | None = None,
        reason: str = "",
        context: dict[str, Any] | None = None,
        policy: ApprovalPolicy | None = None,
    ) -> ApprovalDecision:
        """
        Evaluate whether a tool call may proceed.

        The check now honours the process-level :class:`ApprovalPolicy` via
        :func:`needs_approval`.  Pass *policy* to override per call.

        Args:
            tool_name:    Canonical tool name.
            safety_level: SafetyLevel.value string ("safe", "moderate", "dangerous").
            parameters:   Tool call parameters (for context/logging).
            reason:       Agent-supplied rationale string.
            context:      Extra metadata (e.g. channel, thread id).
            policy:       Override the process-level policy for this call.

        Returns:
            ApprovalDecision.APPROVED or DENIED.
        """
        # Hard bypass for unattended automation
        if os.environ.get("NAVIG_ALLOW_ALL_COMMANDS", "").strip() == "1":
            return ApprovalDecision.APPROVED

        if not needs_approval(tool_name, safety_level, args=parameters, policy=policy):
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

_gate_instance: ApprovalGate | None = None
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

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

Gateway wiring (fail closed)
----------------------------
Inside the gateway process the single-operator default (approve dangerous
tools with a warning) is a fail-open seam: the operator already has real
approval consumers (deck Inbox, Telegram, ``/approval`` routes) wired to
``navig.approval.ApprovalManager``. The gateway therefore calls
:func:`bind_approval_manager` at startup:

- with a live manager → dangerous tools BLOCK on
  ``approval_manager.request_approval`` (timeout follows the ``approval:``
  config section's ``default_action``; every decision lands in the gateway
  audit log as ``tool.execute.<tool_name>``);
- with ``None`` (approval subsystem failed to load) → dangerous tools are
  DENIED, audited, never silently approved.

Non-gateway processes (headless CLI, MCP stdio server, tests) keep the
single-operator default unchanged.
"""

from __future__ import annotations

import asyncio
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
    "check_sync",
    "set_approval_policy",
    "get_approval_policy",
    "bind_approval_manager",
    "gate_agent_tool_call",
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
        # CDP browser-control MCP tools (arbitrary JS / launch / kill a browser).
        # cdp_eval in particular can exfiltrate credentials from a logged-in page
        # (e.g. document.querySelector('#password').value), so it is gated even
        # though the single-operator default backend auto-approves-with-audit.
        "cdp_eval",
        "cdp_launch",
        "cdp_stop",
        "cdp_login",
        "cdp_inject",
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
    logger.debug("approval: policy set to {}", policy.value)


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
        "approval: auto-approving DANGEROUS tool '{}' (single-operator mode). "
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
            logger.error("approval: backend raised unexpectedly: {} — denying", exc)
            decision = ApprovalDecision.DENIED

        logger.info(
            "approval: tool='{}' safety='{}' decision={}",
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


# =============================================================================
# Synchronous bridge (for sync dispatch paths, e.g. the MCP stdio server)
# =============================================================================


def check_sync(
    tool_name: str,
    safety_level: str = "safe",
    parameters: dict[str, Any] | None = None,
    reason: str = "",
    context: dict[str, Any] | None = None,
    policy: ApprovalPolicy | None = None,
) -> ApprovalDecision:
    """Synchronous wrapper around :meth:`ApprovalGate.check`.

    The MCP JSON-RPC server dispatches tools synchronously (no event loop on the
    calling thread), so it cannot ``await`` the async gate. This helper runs the
    gate to completion on a private loop and returns the decision.

    Fast paths (no loop needed): the ``NAVIG_ALLOW_ALL_COMMANDS`` bypass and the
    "policy does not require approval" case both short-circuit to APPROVED
    without touching asyncio, so safe/moderate tools stay zero-overhead.

    On any internal failure it returns :attr:`ApprovalDecision.DENIED` — fail
    closed, never fail open.
    """
    if os.environ.get("NAVIG_ALLOW_ALL_COMMANDS", "").strip() == "1":
        return ApprovalDecision.APPROVED

    if not needs_approval(tool_name, safety_level, args=parameters, policy=policy):
        return ApprovalDecision.APPROVED

    gate = get_approval_gate()

    def _run() -> ApprovalDecision:
        return asyncio.run(
            gate.check(tool_name, safety_level, parameters, reason, context, policy)
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop on this thread → safe to spin a private one.
        try:
            return _run()
        except Exception as exc:  # noqa: BLE001
            logger.error("approval.check_sync failed ({}): {} — denying", tool_name, exc)
            return ApprovalDecision.DENIED

    # A loop is already running on this thread (unexpected for the stdio server);
    # offload to a worker thread so we never re-enter the running loop.
    import concurrent.futures

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_run).result()
    except Exception as exc:  # noqa: BLE001
        logger.error("approval.check_sync failed ({}): {} — denying", tool_name, exc)
        return ApprovalDecision.DENIED


# =============================================================================
# ApprovalManager backend (gateway wiring — fail closed)
# =============================================================================


def _split_session_key(context: dict[str, Any]) -> tuple[str, str, str]:
    """Derive ``(channel, user_id, actor)`` from a gate-check context.

    The agent loop passes the chat-stable session key (e.g.
    ``"telegram:user:12345"``); the first segment is the channel, the rest the
    user. With no context the actor is the local single operator.
    """
    session_key = str(context.get("session_key") or "").strip()
    if ":" in session_key:
        channel, _, rest = session_key.partition(":")
        return channel or "agent", rest or "local", session_key
    if session_key:
        return "agent", session_key, f"agent:{session_key}"
    return "agent", "local", "agent:local"


def _params_preview(parameters: dict[str, Any], limit: int = 200) -> str:
    """Compact, secret-redacted parameter preview for the operator prompt."""
    if not parameters:
        return ""
    import json as _json

    try:
        text = _json.dumps(parameters, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(parameters)
    try:
        from navig.core.security import redact_sensitive_text

        text = redact_sensitive_text(text)
    except Exception:  # noqa: BLE001 — display fallback only
        pass
    return text[:limit] + ("…" if len(text) > limit else "")


def _audit_tool_decision(
    audit_log: Any | None,
    req: ApprovalRequest,
    *,
    actor: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit record for one agent tool-gate decision.

    Action slug matches the gateway convention (``db.query``,
    ``approval.respond``): ``tool.execute.<tool_name>``. Parameters are hashed
    by AuditLog (``input_hash``), never stored verbatim.
    """
    if audit_log is None:
        return
    import json as _json

    try:
        raw_input = _json.dumps(
            {"tool": req.tool_name, "parameters": req.parameters},
            sort_keys=True,
            default=str,
        )
        metadata: dict[str, Any] = {"safety_level": req.safety_level}
        if req.reason:
            metadata["reason"] = req.reason
        metadata.update(extra or {})
        audit_log.record(
            actor=actor,
            action=f"tool.execute.{req.tool_name}",
            policy="require_approval",
            status=status,
            raw_input=raw_input,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001 — a health trace is never worth an outage
        logger.debug("approval: audit record failed for '{}'", req.tool_name)


def bind_approval_manager(manager: Any | None, audit_log: Any | None = None) -> None:
    """Route dangerous-tool approvals through a live ``ApprovalManager``.

    Called by the gateway at startup (after ``approval_manager`` +
    ``audit_log`` are initialised). Replaces the singleton gate's backend:

    - ``manager`` live → gated tools block on ``manager.request_approval``
      (deck Inbox / Telegram / ``/approval`` routes resolve it). Timeout and
      classification follow the operator's ``approval:`` config section
      (``ApprovalPolicy.from_config`` — the same policy #299 wired). The
      request command is ``"tool <name>"``, so operators can pin specific
      tools to safe/dangerous/never via ``approval.levels`` patterns.
    - ``manager is None`` → FAIL CLOSED: gated tools are denied and audited.
      Inside the gateway an unavailable approval subsystem must never
      degrade to approve-with-warning.

    Every decision is recorded on *audit_log* as ``tool.execute.<tool_name>``.
    Non-gateway processes never call this and keep the single-operator default.
    """
    gate = get_approval_gate()

    if manager is None:

        async def _deny_no_manager(req: ApprovalRequest) -> ApprovalDecision:
            _, _, actor = _split_session_key(req.context)
            logger.error(
                "approval: DENYING tool '{}' — no approval manager available "
                "(gateway fail-closed)",
                req.tool_name,
            )
            _audit_tool_decision(
                audit_log,
                req,
                actor=actor,
                status="denied",
                extra={"reason": "approval_unavailable"},
            )
            return ApprovalDecision.DENIED

        gate.backend = _deny_no_manager
        return

    async def _manager_backend(req: ApprovalRequest) -> ApprovalDecision:
        channel, user_id, actor = _split_session_key(req.context)
        _audit_tool_decision(audit_log, req, actor=actor, status="pending_approval")

        description = f"Agent tool call: {req.tool_name} ({req.safety_level})"
        if preview := _params_preview(req.parameters):
            description += f" — {preview}"

        try:
            approved = bool(
                await manager.request_approval(
                    command=f"tool {req.tool_name}",
                    session_key=str(req.context.get("session_key") or f"agent:{user_id}"),
                    channel=channel,
                    user_id=user_id,
                    description=description,
                )
            )
        except Exception:  # noqa: BLE001 — an approval-flow crash must fail closed
            logger.exception(
                "approval: manager flow failed for '{}' — denying", req.tool_name
            )
            approved = False

        _audit_tool_decision(
            audit_log,
            req,
            actor=actor,
            status="approved" if approved else "denied",
            extra={"via": "approval_manager"},
        )
        return ApprovalDecision.APPROVED if approved else ApprovalDecision.DENIED

    gate.backend = _manager_backend


# =============================================================================
# Agent-loop interlock (the ToolRouter seam — shared by both agent editions)
# =============================================================================


async def gate_agent_tool_call(
    tool_name: str,
    *,
    parameters: dict[str, Any] | None = None,
    session_key: str | None = None,
    reason: str = "agentic",
) -> str | None:
    """Approval interlock for one agent tool call.

    Returns ``None`` when the call may proceed, or a human-readable denial
    string the agent loop returns as the tool result (the LLM reads it and
    adapts — never an exception crash).

    FAIL CLOSED: if the gate itself breaks (import error inside the backend,
    unexpected crash), a gated tool is denied rather than executed ungated —
    the agent-loop twin of the #299 policy_check contract.
    """
    try:
        if not needs_approval(tool_name):
            return None
        gate = get_approval_gate()
        context: dict[str, Any] = {}
        if session_key:
            context["session_key"] = session_key
        decision = await gate.check(
            tool_name=tool_name,
            safety_level="moderate",
            parameters=parameters,
            reason=reason,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001 — interlock broke → deny, never proceed
        logger.error(
            "approval: interlock failed for '{}' — denying (fail closed): {}",
            tool_name,
            exc,
        )
        return f"[Denied: approval gate error for '{tool_name}' — failing closed]"

    if decision != ApprovalDecision.APPROVED:
        return f"[Denied: operator did not approve '{tool_name}']"
    return None

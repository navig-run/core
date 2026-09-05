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
Three call sites reach this gate, and all of them funnel through
:func:`needs_approval`, which is therefore the place to change a *default*:

- the **agent loop** — :func:`gate_agent_tool_call`, from
  ``navig/agent/conv/agent.py`` and ``navig/agent/conversational_legacy.py``;
- the **MCP stdio server** — :func:`check_sync`, from ``navig/mcp_server.py``;
- the **MCP client manager** — ``navig/mcp/registry.py``, before dispatching a
  third-party tool call.

(This previously claimed the gate was called from ``ToolRouter._raw_async_execute()``.
It is not, and never was: ``navig/tools/router.py`` does not import this module at all.
That path has its own ``SafetyLevel``/``safety_mode`` policy and is populated only by
first-party tool packs.)

Typical use::

    from navig.tools.approval import get_approval_gate, ApprovalDecision

    gate = get_approval_gate()
    decision = await gate.check(tool_name=..., safety_level=..., parameters=...)
    if decision != ApprovalDecision.APPROVED:
        ...  # refuse; never proceed

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
    "is_destructive_tool",
    "is_external_tool",
    "record_external_tool",
    "forget_external_tools",
    "external_tool_needs_review",
    "external_tool_was_pre_authorised",
    "audit_auto_approval",
    "record_tool_safety",
    "forget_tool_safety",
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
        **Currently identical to ``CONFIRM_DESTRUCTIVE``, and set by nothing.**

        It was documented as "like CONFIRM_DESTRUCTIVE but additionally restricts
        execution to tools whose ``owner_only=True`` flag is set" — behaviour
        :func:`needs_approval` has never implemented, and ``owner_only`` is read by no
        gate at all. Nothing selects this policy in production and no documentation
        tells an operator to, so the description was a promise to nobody.

        Implementing it is a product decision rather than a bug fix: ``owner_only`` is
        an *authorization* flag (~20 read-only devops tools set it), so honouring it as
        an approval trigger would gate a pile of reads. Left as an alias, described
        honestly, until someone needs privilege separation for real.
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
        # ── The rest of the MCP tools classified "dangerous" ──────────────────
        # There are TWO gates and they read different registries: the MCP server
        # asks `_tool_safety` (mcp_server._gate_tool), the agent loop asks this set
        # (gate_agent_tool_call → needs_approval). #759 classified 34 tools as
        # dangerous for the first gate and left this set untouched, so the agent
        # could call `desktop_powershell` — "Execute a PowerShell command … on the
        # local machine" — completely ungated while the MCP path blocked it. The
        # cdp_* entries above show the intended pairing; these complete it.
        # `tests/quality/test_agent_gate_parity.py` now fails if the two drift.
        #
        # local code execution
        "desktop_powershell",
        "desktop_ahk",
        "navig_run_command",
        "navig_block_apply",
        # process / registry / service control
        "desktop_process_kill",
        "desktop_registry_set",
        "desktop_registry_delete",
        "navig_agent_service_install",
        "navig_agent_service_uninstall",
        "navig_agent_component_restart",
        # synthetic input — types into whatever has focus, incl. a shell
        "desktop_type",
        "desktop_set_value",
        "desktop_multi_edit",
        "desktop_shortcut",
        # capture / read of whatever the operator has on screen or in the clipboard
        "desktop_screenshot",
        "desktop_clipboard_get",
        # file system (one tool, eight modes — delete among them)
        "desktop_filesystem",
        # agent + runtime actions that change the system
        "navig_agent_goal_start",
        "navig_agent_remediation_retry",
        "navig_runtime_mission_action",
        # marketplace acquisition (entitlement + writes installed content)
        "navig_bay_acquire",
    }
)

#: Connector write tools are generated per installed connector
#: (``connector_{id}_act``), so they cannot be enumerated above — the set of
#: installed connectors is not known until runtime. Their action vocabulary is
#: reply/create/update/delete/archive/label/send/move, i.e. "send mail as the
#: operator" and "delete the row", so they are destructive by construction.
_DESTRUCTIVE_NAME_SUFFIXES: tuple[tuple[str, str], ...] = (("connector_", "_act"),)


def is_destructive_tool(tool_name: str) -> bool:
    """True when *tool_name* must be treated as destructive.

    **The single answer to that question**, covering all four ways a tool can qualify —
    only the first of which core can enumerate at import time:

    1. the explicit :data:`DESTRUCTIVE_TOOLS` set;
    2. a plugin's self-declared ``safety = "dangerous"`` (:func:`record_tool_safety`);
    3. generated names matched by shape (``connector_*_act``);
    4. an externally-defined tool that is not a declared read
       (:func:`external_tool_needs_review`).

    Keeping (4) out of here was a mistake worth naming: it made this predicate mean
    "known destructive" while :func:`needs_approval` separately meant "must be
    confirmed", so the two could — and did — disagree. The adversarial verifier in
    ``agent/conv/agent.py`` consults this, and under the split it skipped every
    external tool the gate was holding. One predicate, one meaning.
    """
    if tool_name in DESTRUCTIVE_TOOLS:
        return True
    with _declared_lock:
        if tool_name in _declared_dangerous:
            return True
    if any(
        tool_name.startswith(prefix) and tool_name.endswith(suffix)
        for prefix, suffix in _DESTRUCTIVE_NAME_SUFFIXES
    ):
        return True
    return external_tool_needs_review(tool_name)


# =============================================================================
# Self-declared danger (plugin and core tools)
# =============================================================================
#
# :data:`DESTRUCTIVE_TOOLS` can only ever list names this module knows at import time.
# A **plugin** registers agent tools of its own (`navig-games` ships `games_claim`,
# which completes a checkout on the operator's real store account), and core cannot
# enumerate those without hardcoding a plugin's vocabulary into the gate — coupling
# that does not scale to the next plugin.
#
# `navig.tools.bridge` already established the convention that a `BaseTool` may declare
# ``safety = "dangerous"``; nothing on the agent side read it. Registration now pushes
# that declaration in here, the same inversion used by `record_external_tool` and
# `bind_approval_manager`, so a tool can classify itself without core knowing its name.
#
# Note the polarity: this can only ever ADD names, never remove one — a tool declaring
# itself safe is not believed over `DESTRUCTIVE_TOOLS`.

_declared_dangerous: set[str] = set()
_declared_lock = threading.Lock()


def record_tool_safety(tool_name: str, *, dangerous: bool) -> None:
    """Record a tool's self-declared safety level.

    Called by :meth:`navig.agent.agent_tool_registry.AgentToolRegistry.register_entry`
    for every tool that declares ``safety``. Only ``dangerous`` is acted upon.
    """
    if not dangerous:
        return
    with _declared_lock:
        _declared_dangerous.add(tool_name)


def forget_tool_safety(tool_name: str) -> None:
    """Drop a self-declaration (used when a tool is deregistered)."""
    with _declared_lock:
        _declared_dangerous.discard(tool_name)


# =============================================================================
# Externally-defined tools (MCP)
# =============================================================================
#
# :data:`DESTRUCTIVE_TOOLS` is an allowlist inverted into a denylist: it names ~45 of
# NAVIG's own tools, so its answer for anything it has never heard of is "safe". That
# is the correct default for a closed set and exactly the wrong one for tools defined
# by a third party — a server publishing ``delete_all_repositories`` was not judged
# safe, it simply had nothing to be looked up in.
#
# Provenance is not derivable from a bare string, and the gate holds only a string, so
# it has to travel *in* the name: :func:`navig.mcp.trust.namespaced_tool_name` prefixes
# every external tool with ``mcp__``. The shape rule below then reads that prefix and
# defaults to **needs approval**.
#
# A classification pushed in at discovery (:func:`record_external_tool`) may downgrade a
# tool the server honestly declared read-only. It is *pushed* rather than pulled so this
# module — on the hot dispatch path, currently stdlib + loguru only — never has to
# import ``navig.mcp``. Same inversion as :func:`bind_approval_manager`.
#
# Fails closed twice: an external name with no index entry is held by shape, whether or
# not the classifier ever ran.

#: Prefix marking a registry name as externally defined. Mirrors
#: :data:`navig.mcp.trust.TOOL_NAME_PREFIX`; duplicated rather than imported to keep
#: this module free of ``navig.mcp``. Pinned by ``tests/quality/test_external_tool_gate_parity.py``.
_EXTERNAL_TOOL_PREFIX = "mcp__"

#: registry_name -> (server, mode, auto_approvable, operator_pre_authorised)
_external_tools: dict[str, tuple[str, str, bool, bool]] = {}
_external_lock = threading.Lock()


def is_external_tool(tool_name: str) -> bool:
    """True when *tool_name* was defined by a third party rather than by NAVIG."""
    return tool_name.startswith(_EXTERNAL_TOOL_PREFIX)


def record_external_tool(
    tool_name: str,
    *,
    mode: str,
    auto_approvable: bool = False,
    operator_pre_authorised: bool = False,
    server: str | None = None,
) -> None:
    """Record a classification decided by :mod:`navig.mcp.trust`.

    Args:
        tool_name: The namespaced registry name (``mcp__<server>__<tool>``).
        mode: ``"read"`` or ``"action"``, as classified at discovery.
        auto_approvable: **Gate 1** — the classifier's verdict, from the *server's* own
            annotations on a *vetted* endpoint: non-destructive and idempotent.
        operator_pre_authorised: **Gate 2** — the operator named this tool in
            ``mcp.trust.servers.<id>.auto_approve``.
        server: Owning server id; derived from *tool_name* when omitted.

    An action runs unprompted only when **both** gates are true. Either alone is the
    wrong authority: gate 1 alone is the server marking its own homework, and gate 2
    alone is a blank cheque written against a tool description the operator cannot see
    change. Both false is the default, and is why this fails closed.
    """
    if not is_external_tool(tool_name):
        return
    if server is None:
        rest = tool_name[len(_EXTERNAL_TOOL_PREFIX) :]
        server = rest.partition("__")[0]
    with _external_lock:
        _external_tools[tool_name] = (
            server,
            mode,
            bool(auto_approvable),
            bool(operator_pre_authorised),
        )


def forget_external_tools(server: str) -> None:
    """Drop every classification belonging to *server*.

    A disconnected server's downgrade must not outlive it: after this, its tool names
    fall back to the shape rule, which denies.
    """
    with _external_lock:
        for name in [n for n, rec in _external_tools.items() if rec[0] == server]:
            del _external_tools[name]


def external_tool_needs_review(tool_name: str) -> bool:
    """Whether an externally-defined tool must be held for the operator.

    Returns False for names NAVIG owns — this predicate has no opinion on those.

    An action is released only when BOTH gates agree (see :func:`record_external_tool`).
    A read never needed one.
    """
    if not is_external_tool(tool_name):
        return False
    with _external_lock:
        record = _external_tools.get(tool_name)
    if record is None:
        return True  # unclassified: the server never vouched, and neither did we
    _, mode, auto_approvable, pre_authorised = record
    if mode == "read":
        return False
    return not (auto_approvable and pre_authorised)


def external_tool_was_pre_authorised(tool_name: str) -> bool:
    """True when this action ran unprompted because both gates agreed.

    Distinguishes "the operator was never asked because they pre-authorised it" from
    "the operator was never asked because it is a read". The caller records the first
    in the audit log — an action that applied without a prompt must still name the
    human who allowed it, or the audit answers 'who approved this?' with silence.
    """
    if not is_external_tool(tool_name):
        return False
    with _external_lock:
        record = _external_tools.get(tool_name)
    if record is None:
        return False
    _, mode, auto_approvable, pre_authorised = record
    return mode != "read" and auto_approvable and pre_authorised


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

    # `is_destructive_tool` is the single answer to "must this be treated as
    # destructive" — it already folds in self-declared plugin tools, the connector
    # shape rule and unclassified external names.
    if active_policy in (ApprovalPolicy.CONFIRM_DESTRUCTIVE, ApprovalPolicy.OWNER_ONLY):
        return safety_level == "dangerous" or is_destructive_tool(tool_name)

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
    """Reset the singleton (used in tests).

    Clears **every** process-global this module keeps, because leaving one behind is
    exactly how the leak this function exists for keeps coming back:

    * the gate singleton — a test's backend answering for every later test;
    * the external-tool index — a classification downgrading a name for later tests;
    * self-declared danger — a plugin's declaration outliving its registry;
    * the bound audit log — a test's fake receiving records from later tests.

    That last one was added after this docstring already named the failure mode, and
    was still missed: six tests in ``test_gate_manager_wiring.py`` bind a real fake and
    every one of them leaked it. **Add a module global here, add it to this list.**

    Note the *policy* is reset separately (``set_approval_policy``); resetting only the
    gate is what left that half behind last time.
    """
    global _gate_instance, _bound_audit_log
    with _gate_lock:
        _gate_instance = None
    with _external_lock:
        _external_tools.clear()
    with _declared_lock:
        _declared_dangerous.clear()
    _bound_audit_log = None


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


def _approval_description(req: ApprovalRequest) -> str:
    """The text the operator reads before deciding.

    Prefers a card the caller already built — `navig.mcp.registry` and the gateway's
    MCP-register route render one through :mod:`navig.tools.untrusted_text`, with the
    server's own description quoted, the arguments fenced, and the provenance of the
    read/action classification stated. That card used to be **discarded here**: this
    function rebuilt a one-liner from scratch, so the careful rendering reached nobody
    and the whole point of building it was lost.

    Falling back, the default is built with the same defences rather than an f-string.
    `tool_name` is not always NAVIG's: an external tool carries a name a third party
    chose, and it lands in a Markdown surface (`/approval/pending` → deck / OS Inbox →
    Telegram). Interpolating it raw let a crafted name open a heading or close a fence
    and continue in NAVIG's own voice.
    """
    supplied = req.context.get("description")
    if isinstance(supplied, str) and supplied.strip():
        return supplied

    try:
        from navig.tools.untrusted_text import code_span, defuse_fences, plain_inline

        head = f"Agent tool call: {code_span(req.tool_name)} ({plain_inline(req.safety_level)})"
        preview = _params_preview(req.parameters)
        return f"{head} — {defuse_fences(preview)}" if preview else head
    except Exception:  # noqa: BLE001 — the prompt must render even if rendering breaks
        logger.debug("approval: could not render description for '{}'", req.tool_name)
        return f"Agent tool call: {req.tool_name} ({req.safety_level})"


#: The gateway's audit log, captured when the manager is bound. A pre-authorised call
#: never reaches the backend closure that normally holds it, so it needs a way in — an
#: action that applied without a prompt still has to appear in the log.
_bound_audit_log: Any | None = None


def audit_auto_approval(tool_name: str, *, enabled_by: str, parameters: dict | None = None) -> None:
    """Record an action that ran unprompted because the operator pre-authorised it.

    The reference design is explicit that an auto-applied action must still name a
    human, and this is why: without it, the audit log answers "who approved this?" with
    silence for exactly the calls nobody watched happen. `enabled_by` is the config key
    that allowed it, so the answer points at the decision rather than at the machine.

    Best-effort and never raises — a missing audit line is bad; a tool call that fails
    because logging it failed is worse.
    """
    if _bound_audit_log is None:
        return
    try:
        _bound_audit_log.record(
            actor="agent:auto",
            action=f"tool.execute.{tool_name}",
            policy="auto_approved",
            status="approved",
            raw_input=_json_dumps({"tool": tool_name, "parameters": parameters or {}}),
            metadata={"auto_approved": True, "enabled_by": enabled_by},
        )
    except Exception:  # noqa: BLE001 — a health trace is never worth an outage
        logger.debug("approval: auto-approval audit failed for '{}'", tool_name)


def _json_dumps(payload: dict) -> str:
    import json as _json

    try:
        return _json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(payload)


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
    global _bound_audit_log
    _bound_audit_log = audit_log

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

        description = _approval_description(req)

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

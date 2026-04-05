"""
navig.agent.plan_mode — Structured planning mode for the agentic loop.

Provides a read-only planning phase where the agent can research, explore,
and build a step-by-step plan *before* making any mutations.  Once the user
approves the plan it transitions to execute mode where all tools are available.

Key components:

* :class:`PlanState`        — Lifecycle enum (INACTIVE → PLANNING → REVIEWING ...)
* :class:`PlanStep`         — Single step in a plan (description, files, risk).
* :class:`PlanSession`      — The full plan: state + ordered steps + context.
* :class:`PlanInterceptor`  — Gate that blocks write-tools while planning.

Usage (from ``ConversationalAgent``)::

    interceptor = PlanInterceptor()
    interceptor.start()                     # enter planning mode
    interceptor.should_block("write_file")  # → True during PLANNING
    interceptor.add_step(PlanStep(...))
    interceptor.review()                    # move to REVIEWING
    interceptor.approve()                   # move to EXECUTING
    # now interceptor.should_block("write_file") → False

FA-01 implementation per `.navig/plans/claude/06_implementation_plans/fa1_plan_mode.md`.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Enums & data classes
# ─────────────────────────────────────────────────────────────


class PlanState(Enum):
    """Lifecycle states for a plan session."""

    INACTIVE = "inactive"  # Normal mode — no plan active
    PLANNING = "planning"  # Read-only mode — building plan
    REVIEWING = "reviewing"  # Plan complete — awaiting approval
    EXECUTING = "executing"  # Approved — executing plan steps
    COMPLETED = "completed"  # All steps executed


@dataclass
class PlanStep:
    """A single directed step in a plan.

    Attributes:
        description:    Human-readable description of the change.
        tool_calls:     Predicted tool names the step will invoke.
        files_affected: Files that will be created / modified / deleted.
        risk_level:     ``"low"`` | ``"medium"`` | ``"high"``.
        status:         ``"pending"`` | ``"in_progress"`` | ``"done"`` | ``"skipped"``.
    """

    description: str
    tool_calls: list[str] = field(default_factory=list)
    files_affected: list[str] = field(default_factory=list)
    risk_level: str = "low"
    status: str = "pending"

    # ── Validation ───────────────────────────────────────────

    _VALID_RISK = frozenset({"low", "medium", "high"})
    _VALID_STATUS = frozenset({"pending", "in_progress", "done", "skipped"})

    def __post_init__(self) -> None:
        if self.risk_level not in self._VALID_RISK:
            raise ValueError(
                f"risk_level must be one of {sorted(self._VALID_RISK)}, got {self.risk_level!r}"
            )
        if self.status not in self._VALID_STATUS:
            raise ValueError(
                f"status must be one of {sorted(self._VALID_STATUS)}, got {self.status!r}"
            )


@dataclass
class PlanSession:
    """Container for a complete plan: metadata + ordered steps.

    Attributes:
        plan_id:           Unique identifier (UUID hex).
        state:             Current :class:`PlanState`.
        steps:             Ordered list of :class:`PlanStep`.
        context_gathered:  Description strings of files / searches reviewed
                           during the planning phase.
        created_at:        Epoch timestamp.
        approved_at:       Epoch timestamp when user approved (if ever).
    """

    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: PlanState = PlanState.INACTIVE
    steps: list[PlanStep] = field(default_factory=list)
    context_gathered: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    approved_at: float | None = None

    # ── Convenience ──────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """True if the plan is in any non-terminal state."""
        return self.state in (PlanState.PLANNING, PlanState.REVIEWING, PlanState.EXECUTING)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "pending"]

    @property
    def files_at_risk(self) -> list[str]:
        """Unique sorted list of all files affected across every step."""
        seen: set[str] = set()
        result: list[str] = []
        for step in self.steps:
            for f in step.files_affected:
                if f not in seen:
                    seen.add(f)
                    result.append(f)
        return sorted(result)

    def summary(self) -> dict[str, Any]:
        """Machine-readable summary of the plan."""
        return {
            "plan_id": self.plan_id,
            "state": self.state.value,
            "total_steps": self.step_count,
            "pending": len(self.pending_steps),
            "files_affected": self.files_at_risk,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
        }


# ─────────────────────────────────────────────────────────────
# Plan Interceptor
# ─────────────────────────────────────────────────────────────


class PlanInterceptor:
    """Gate that controls tool availability based on plan state.

    In PLANNING state only :attr:`READ_ONLY_TOOLS` (and plan management tools)
    are allowed.  All other tools are blocked with a helpful message.

    In INACTIVE / EXECUTING / COMPLETED states nothing is blocked.
    """

    # Tools the agent may always call while in planning mode.
    # Includes read tools, search, list, plan management tools.
    READ_ONLY_TOOLS: frozenset[str] = frozenset(
        {
            # File reading
            "read_file",
            "list_files",
            # Search / lookup
            "search",
            "web_fetch",
            "grep_search",
            "semantic_search",
            "find_definition",
            "find_references",
            "symbols",
            # Memory / wiki reads
            "memory_read",
            "kb_lookup",
            "wiki_search",
            "wiki_read",
            # DevOps read-only
            "navig_host_show",
            "navig_host_test",
            "navig_host_monitor",
            "navig_app_list",
            "navig_app_show",
            "navig_db_list",
            "navig_docker_ps",
            "navig_file_show",
            "navig_file_list",
            "navig_web_vhosts",
            # Git read-only
            "git_status",
            "git_diff",
            "git_log",
            "git_stash",
            # Plan management
            "plan_add_step",
            "plan_show",
            "plan_approve",
        }
    )

    BLOCK_MESSAGE = (
        "🚫 Blocked in plan mode — this tool modifies state.  "
        "Add this action as a plan step instead using `plan_add_step`."
    )

    def __init__(self) -> None:
        self._session = PlanSession()

    # ── Lifecycle ────────────────────────────────────────────

    def start(self) -> PlanSession:
        """Enter planning mode.  Creates a fresh session.

        Raises:
            RuntimeError: If a plan is already active.
        """
        if self._session.is_active:
            raise RuntimeError(
                f"A plan is already active (id={self._session.plan_id}, "
                f"state={self._session.state.value}).  Cancel it first."
            )
        self._session = PlanSession(state=PlanState.PLANNING)
        logger.info("Plan mode started: %s", self._session.plan_id)
        return self._session

    def cancel(self) -> None:
        """Discard the current plan and return to INACTIVE."""
        old_id = self._session.plan_id
        self._session = PlanSession()  # fresh INACTIVE session
        logger.info("Plan cancelled: %s", old_id)

    def review(self) -> None:
        """Transition from PLANNING → REVIEWING.

        Raises:
            RuntimeError: If not currently in PLANNING state.
        """
        if self._session.state != PlanState.PLANNING:
            raise RuntimeError(
                f"Cannot review — plan is in {self._session.state.value} state, "
                "expected 'planning'."
            )
        if not self._session.steps:
            raise RuntimeError("Cannot review an empty plan — add at least one step.")
        self._session.state = PlanState.REVIEWING
        logger.info("Plan %s moved to REVIEWING", self._session.plan_id)

    def approve(self) -> None:
        """Transition from REVIEWING → EXECUTING.

        Raises:
            RuntimeError: If not currently in REVIEWING state.
        """
        if self._session.state != PlanState.REVIEWING:
            raise RuntimeError(
                f"Cannot approve — plan is in {self._session.state.value} state, "
                "expected 'reviewing'."
            )
        self._session.state = PlanState.EXECUTING
        self._session.approved_at = time.time()
        logger.info("Plan %s approved, entering EXECUTING", self._session.plan_id)

    def complete(self) -> None:
        """Mark the plan as completed (all steps executed)."""
        if self._session.state != PlanState.EXECUTING:
            raise RuntimeError(
                f"Cannot complete — plan is in {self._session.state.value} state, "
                "expected 'executing'."
            )
        self._session.state = PlanState.COMPLETED
        logger.info("Plan %s completed", self._session.plan_id)

    # ── Step management ──────────────────────────────────────

    def add_step(self, step: PlanStep) -> int:
        """Append a step during PLANNING.  Returns the step index (0-based).

        Raises:
            RuntimeError: If not in PLANNING state.
        """
        if self._session.state != PlanState.PLANNING:
            raise RuntimeError("Can only add steps while in 'planning' state.")
        self._session.steps.append(step)
        idx = len(self._session.steps) - 1
        logger.debug(
            "Plan %s: added step %d — %s", self._session.plan_id, idx, step.description[:60]
        )
        return idx

    def record_context(self, description: str) -> None:
        """Record a file/search that was reviewed during planning."""
        self._session.context_gathered.append(description)

    def mark_step(self, index: int, status: str) -> None:
        """Update a step's status during execution.

        Args:
            index:  0-based step index.
            status: One of ``"pending"``, ``"in_progress"``, ``"done"``, ``"skipped"``.
        """
        if index < 0 or index >= len(self._session.steps):
            raise IndexError(f"Step index {index} out of range (0..{len(self._session.steps) - 1})")
        # Validate status
        step = self._session.steps[index]
        if status not in PlanStep._VALID_STATUS:
            raise ValueError(f"Invalid status {status!r}")
        step.status = status

    # ── Tool gating ──────────────────────────────────────────

    def should_block(self, tool_name: str) -> bool:
        """Return ``True`` if *tool_name* is forbidden in the current plan state.

        Only blocks during PLANNING — all other states allow everything.
        """
        if self._session.state != PlanState.PLANNING:
            return False
        return tool_name not in self.READ_ONLY_TOOLS

    def get_block_reason(self, tool_name: str) -> str | None:
        """If *tool_name* is blocked, return a human-readable reason.

        Returns ``None`` if the tool is allowed.
        """
        if not self.should_block(tool_name):
            return None
        return self.BLOCK_MESSAGE

    # ── Accessors ────────────────────────────────────────────

    @property
    def session(self) -> PlanSession:
        """Current plan session (may be INACTIVE)."""
        return self._session

    @property
    def state(self) -> PlanState:
        return self._session.state

    @property
    def is_planning(self) -> bool:
        return self._session.state == PlanState.PLANNING

    @property
    def is_active(self) -> bool:
        return self._session.is_active

    def format_plan(self) -> str:
        """Format the current plan as human-readable Markdown."""
        s = self._session
        lines: list[str] = [
            f"## Plan `{s.plan_id}` — {s.state.value}",
            "",
        ]
        if not s.steps:
            lines.append("_(no steps yet)_")
            return "\n".join(lines)

        for i, step in enumerate(s.steps, 1):
            icon = {"pending": "⬜", "in_progress": "🔄", "done": "✅", "skipped": "⏭️"}.get(
                step.status, "❓"
            )
            risk_badge = {"low": "", "medium": " ⚠️", "high": " 🔴"}.get(step.risk_level, "")
            lines.append(f"{icon} **Step {i}**{risk_badge}: {step.description}")
            if step.files_affected:
                lines.append(f"   Files: {', '.join(step.files_affected)}")
            if step.tool_calls:
                lines.append(f"   Tools: {', '.join(step.tool_calls)}")
            lines.append("")

        if s.context_gathered:
            lines.append("### Context gathered")
            for c in s.context_gathered:
                lines.append(f"- {c}")
            lines.append("")

        return "\n".join(lines)

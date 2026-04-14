"""TaskExecutor: dataclasses for task state + execution with exponential-backoff retry."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from navig.agent.conv.localization import LocalizationStore
from navig.agent.conv.status_event import StatusEvent

if TYPE_CHECKING:
    from navig.tools.schemas import MultiStepAction

logger = logging.getLogger(__name__)

_EXEC_RETRY_BASE_DELAY_SECONDS = 1.0
_EXEC_RETRY_JITTER_MAX_SECONDS = 0.5
_EXEC_RETRY_MAX_DELAY_SECONDS = 30.0
_REFLECTION_REMEDIATION_CONFIDENCE_THRESHOLD = 70
_MAX_REMEDIATION_STEPS = 2


class TaskStatus(Enum):
    PENDING = auto()
    PLANNING = auto()
    EXECUTING = auto()
    WAITING_INPUT = auto()
    SUCCESS = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class ExecutionStep:
    """A single step within a task execution plan."""

    action: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    result: str | None = None
    error: str | None = None


@dataclass
class Task:
    """A goal the agent is autonomously working toward."""

    id: str
    goal: str
    context: str = ""
    status: TaskStatus = TaskStatus.PENDING
    plan: list[ExecutionStep] = field(default_factory=list)
    current_step: int = 0
    attempts: int = 0
    max_attempts: int = 3
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    final_result: str | None = None
    # Executor-private: excluded from __init__, __repr__, __eq__ to keep Task
    # as a pure data object; TaskExecutor sets this directly after construction.
    _reflection_attempted: bool = field(default=False, repr=False, compare=False, init=False)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the essential task fields to a plain dict for logging / API responses."""
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.name,
            "current_step": self.current_step,
            "total_steps": len(self.plan),
            "attempts": self.attempts,
        }


class TaskExecutor:
    """
    Runs Task plans step-by-step with per-step exponential-backoff retry.
    Owns the step-dispatch logic and localized result assembly.
    Guarantees: execute() always returns a string; never propagates step exceptions.
    """

    def __init__(
        self,
        on_status_update: Callable[[StatusEvent], Awaitable[None]] | None = None,
        localization: LocalizationStore | None = None,
        max_attempts: int = 3,
    ) -> None:
        """Initialise the executor with an optional status callback, localisation store, and retry limit."""
        self._notify_cb: Callable[[StatusEvent], Awaitable[None]] | None = on_status_update
        self._loc: LocalizationStore = localization or LocalizationStore()
        self._max_attempts = max(1, max_attempts)
        self.current_task: Task | None = None

    @staticmethod
    def _compute_retry_delay(attempt: int) -> float:
        return min(
            _EXEC_RETRY_BASE_DELAY_SECONDS * (2**attempt)
            + random.uniform(0.0, _EXEC_RETRY_JITTER_MAX_SECONDS),
            _EXEC_RETRY_MAX_DELAY_SECONDS,
        )

    async def execute_plan(self, plan_data: dict[str, Any]) -> str:
        """Build a ``Task`` from a validated plan dict and execute or stage it.

        If the plan has ``confirmation_needed=True``, the task is left in
        ``PLANNING`` state and a human-readable confirmation prompt is returned.
        Otherwise execution begins immediately via :meth:`execute`.

        Returns a string in all cases (prompt, result, or fallback message).
        """
        steps = plan_data.get("plan", [])
        message = plan_data.get("message", "Working on it...")
        if not steps:
            return message
        task = Task(
            id=str(uuid.uuid4())[:8],
            goal=plan_data.get("understanding", "Execute task"),
            status=(
                TaskStatus.PLANNING
                if plan_data.get("confirmation_needed")
                else TaskStatus.EXECUTING
            ),
            plan=[
                ExecutionStep(
                    action=s.get("action", "unknown"),
                    description=s.get("description", ""),
                    params=s.get("params", {}),
                )
                for s in steps
            ],
        )
        self.current_task = task
        if task.status == TaskStatus.PLANNING:
            steps_desc = "\n".join(f"  {i + 1}. {s.description}" for i, s in enumerate(task.plan))
            return f"{message}\n\nPlan:\n{steps_desc}\n\nReply 'yes' to proceed or 'no' to cancel."
        return await self.execute(task)

    async def execute(self, task: Task) -> str:
        self.current_task = task  # ensure current_task is set even when called directly
        task.status = TaskStatus.EXECUTING
        task.attempts += 1
        await self._emit_event(
            StatusEvent(
                type="task_start",
                task_id=task.id,
                message=task.goal,
                timestamp=datetime.now(),
            )
        )
        lang = "en"
        results: list[str] = []
        for i, step in enumerate(task.plan):
            task.current_step = i
            await self._emit_event(
                StatusEvent(
                    type="step_start",
                    task_id=task.id,
                    message=step.description,
                    timestamp=datetime.now(),
                    step_index=i,
                    total_steps=len(task.plan),
                )
            )
            last_err: Exception | None = None
            for attempt in range(self._max_attempts):
                try:
                    result = await self._execute_step(step)
                    step.status, step.result = "success", str(result)
                    results.append(f"✅ {step.description}")
                    await self._emit_event(
                        StatusEvent(
                            type="step_done",
                            task_id=task.id,
                            message=step.description,
                            timestamp=datetime.now(),
                            step_index=i,
                            total_steps=len(task.plan),
                        )
                    )
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
                    await self._emit_event(
                        StatusEvent(
                            type="step_failed",
                            task_id=task.id,
                            message=step.description,
                            timestamp=datetime.now(),
                            step_index=i,
                            total_steps=len(task.plan),
                            metadata={
                                "error": str(exc),
                                "attempt": attempt,
                                "is_final": attempt >= self._max_attempts - 1,
                            },
                        )
                    )
                    if attempt < self._max_attempts - 1:
                        delay = self._compute_retry_delay(attempt)
                        await asyncio.sleep(delay)
            if last_err is not None:
                step.status, step.error = "failed", str(last_err)
                results.append(f"❌ {step.description}: {last_err}")
                task.status = TaskStatus.FAILED
                break
        if task.status != TaskStatus.FAILED:
            task.status = TaskStatus.SUCCESS
            task.completed_at = datetime.now()

        # --- Single-cycle self-reflection pass ---
        reflection_confidence: int | None = None
        if task.status == TaskStatus.SUCCESS and not task._reflection_attempted:
            task._reflection_attempted = True
            try:
                step_summaries_and_results = "\n".join(
                    f"{idx + 1}. action={s.action} | result={s.result}"
                    for idx, s in enumerate(task.plan)
                    if s.status == "success"
                )
                reflection_prompt = (
                    f"Here is what I just executed: {step_summaries_and_results}.\n"
                    f"The original goal was: {task.goal}.\n"
                    "Did I fully achieve the goal? Reply with JSON only:\n"
                    '{ "achieved": bool, "confidence": 0-100, '
                    '"gap": "description of what was missed, or empty string" }'
                )
                from navig.llm_generate import (
                    run_llm,  # lazy: heavy module, only used on reflection pass
                )

                llm_result = await asyncio.to_thread(
                    run_llm,
                    messages=[{"role": "user", "content": reflection_prompt}],
                    mode="small",
                )
                reflection_data = json.loads(llm_result.content)
                achieved: bool = bool(reflection_data.get("achieved", True))
                confidence: int = int(reflection_data.get("confidence", 100))
                reflection_confidence = confidence

                if not achieved and confidence < _REFLECTION_REMEDIATION_CONFIDENCE_THRESHOLD:
                    task.status = TaskStatus.EXECUTING
                    from navig.agent.conv.planner import FallbackPlanner  # lazy

                    remediation_plan = FallbackPlanner().plan(task.goal) or {}
                    remediation_steps = [
                        ExecutionStep(
                            action=s.get("action", "unknown"),
                            description=s.get("description", ""),
                            params=s.get("params", {}),
                        )
                        for s in remediation_plan.get("plan", [])[:_MAX_REMEDIATION_STEPS]
                    ]
                    rem_any_failed = False
                    for rem_step in remediation_steps:
                        last_rem_err: Exception | None = None
                        for attempt in range(self._max_attempts):
                            try:
                                await self._emit_event(
                                    StatusEvent(
                                        type="step_start",
                                        task_id=task.id,
                                        message=rem_step.description,
                                        timestamp=datetime.now(),
                                    )
                                )
                                rem_result = await self._execute_step(rem_step)
                                rem_step.status, rem_step.result = "success", str(rem_result)
                                results.append(f"✅ (remediation) {rem_step.description}")
                                await self._emit_event(
                                    StatusEvent(
                                        type="step_done",
                                        task_id=task.id,
                                        message=rem_step.description,
                                        timestamp=datetime.now(),
                                    )
                                )
                                last_rem_err = None
                                break
                            except Exception as exc:
                                last_rem_err = exc
                                await self._emit_event(
                                    StatusEvent(
                                        type="step_failed",
                                        task_id=task.id,
                                        message=rem_step.description,
                                        timestamp=datetime.now(),
                                        metadata={
                                            "error": str(exc),
                                            "attempt": attempt,
                                            "is_final": attempt >= self._max_attempts - 1,
                                        },
                                    )
                                )
                                if attempt < self._max_attempts - 1:
                                    delay = self._compute_retry_delay(attempt)
                                    await asyncio.sleep(delay)
                        if last_rem_err is not None:
                            rem_step.status, rem_step.error = "failed", str(last_rem_err)
                            results.append(
                                f"❌ (remediation) {rem_step.description}: {last_rem_err}"
                            )
                            rem_any_failed = True
                    if not rem_any_failed:
                        task.status = TaskStatus.SUCCESS
                        task.completed_at = datetime.now()
                    else:
                        task.status = TaskStatus.FAILED
            except Exception as exc:
                logger.warning("Self-reflection pass failed (non-fatal): %s", exc)
        # --- End self-reflection pass ---

        ok = task.status == TaskStatus.SUCCESS
        emoji = "🎉" if ok else "😅"
        label = self._loc.get("completed" if ok else "issues", lang)
        footer = self._loc.get("anything_else" if ok else "different_approach", lang)
        result_str = f"{emoji} {label}\n\n" + "\n".join(results) + f"\n\n{footer}"
        if reflection_confidence is not None:
            result_str += f" (Self-assessed confidence: {reflection_confidence}/100)"
        # Emit task_done BEFORE clearing current_task so callbacks can inspect
        # executor.current_task if needed.
        await self._emit_event(
            StatusEvent(
                type="task_done",
                task_id=task.id,
                message=result_str,
                timestamp=datetime.now(),
            )
        )
        self.current_task = None
        return result_str

    async def _emit_event(self, event: StatusEvent) -> None:
        """Fire the StatusEvent callback; guards against None, awaits coroutines, swallows errors."""
        if self._notify_cb is None:
            return
        try:
            # Use inspect (not deprecated asyncio.iscoroutinefunction) and also
            # detect callable class instances whose __call__ is async.
            cb = self._notify_cb
            is_coro_fn = inspect.iscoroutinefunction(cb) or inspect.iscoroutinefunction(
                getattr(cb, "__call__", None)  # noqa: B004
            )
            if is_coro_fn:
                await cb(event)
            else:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as exc:
            logger.warning("StatusEvent callback error: %s", exc)

    async def _execute_step(self, step: ExecutionStep) -> object:
        """Dispatch a single step via ActionRegistry; unknown actions fall through to ToolRouter."""
        action, params = step.action, step.params

        # ── ActionRegistry dispatch ─────────────────────────────────────────
        # All built-in actions (wait, command, evolve.workflow, workflow.run,
        # auto.*) are registered in navig.agent.action_registry.
        # Adding a new action no longer requires editing this file.
        from navig.agent.action_registry import get_action_registry

        _matched, _result = await get_action_registry().dispatch(action, params)
        if _matched:
            return _result

        # ── ToolRouter fallthrough ──────────────────────────────────────────
        # Action was not found in the ActionRegistry.  Attempt to dispatch
        # through the registered tool packs (web, image, code, system, …).
        # Returns the tool output on success; raises ValueError if the tool
        # is genuinely unknown (preserves existing error contract).
        from navig.tools.router import ToolResultStatus, get_tool_router
        from navig.tools.schemas import ToolCallAction as _ToolCallAction

        _router = get_tool_router()
        _result = await _router.async_execute(_ToolCallAction(tool=action, parameters=params))
        if _result.status == ToolResultStatus.SUCCESS:
            return _result.output
        if _result.status == ToolResultStatus.NOT_FOUND:
            raise ValueError(f"Unknown action: {action!r}")
        raise RuntimeError(f"Tool '{action}' failed ({_result.status.value}): {_result.error}")

    async def execute_multi_step_action(
        self,
        action: MultiStepAction,
    ) -> str:
        """
        Execute a MultiStepAction — a chain of ToolCallActions produced by the
        LLM planner — and return a combined result summary.

        Each step is dispatched through the ToolRouter.  Failure of any step
        raises RuntimeError immediately, halting the chain.
        """
        from navig.tools.router import ToolResultStatus, get_tool_router

        _router = get_tool_router()
        outputs: list[str] = []
        for i, step in enumerate(action.steps, 1):
            result = await _router.async_execute(step)
            if result.status == ToolResultStatus.SUCCESS:
                outputs.append(f"[{i}] {step.tool}: {result.output}")
            else:
                raise RuntimeError(
                    f"Step {i} ('{step.tool}') failed ({result.status.value}): {result.error}"
                )
        return "\n".join(outputs) if outputs else "(no steps executed)"

"""
navig.agent.coordinator — Multi-agent orchestration (Coordinator Mode).

Splits a complex user request into work items, dispatches them to worker
models (potentially in parallel), and synthesises a final summary using a
cheap orchestrator model.

**Lifecycle**:  plan → execute → synthesise

FB-01 implementation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from navig._llm_defaults import _DEFAULT_MAX_TOKENS

logger = logging.getLogger(__name__)

# ── Model constants ──────────────────────────────────────────
# Current models. Workers run as full ReAct children via run_agentic, so these are
# the *intent*; the actual model is resolved by run_agentic's tier routing (which,
# with an Anthropic key present, prefers Opus 4.8 for the big tier). Prompt caching +
# effort (shipped separately) make a fleet of specialists affordable.
ORCHESTRATOR_MODEL = "claude-haiku-4-5"
WORKER_MODEL_FAST = "claude-haiku-4-5"
WORKER_MODEL_SMART = "claude-opus-4-8"
WORKER_MODEL_DEFAULT = WORKER_MODEL_SMART

# ── Caps ─────────────────────────────────────────────────────
MAX_WORKERS = 5
MAX_CONCURRENT = 3


# ── Data structures ──────────────────────────────────────────


class WorkerState(Enum):
    """Lifecycle state of an individual worker."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class WorkerSpec:
    """Immutable specification for a single worker task."""

    worker_id: str
    task_description: str
    tools_allowed: list[str] = field(default_factory=list)
    model: str = "auto"
    depends_on: list[str] = field(default_factory=list)
    timeout: int = 120
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "task_description": self.task_description,
            "tools_allowed": self.tools_allowed,
            "model": self.model,
            "depends_on": self.depends_on,
            "timeout": self.timeout,
            "context": self.context,
        }


@dataclass
class WorkerResult:
    """Outcome produced by a single worker execution."""

    worker_id: str
    state: WorkerState
    output: str = ""
    error: str | None = None
    tokens_used: int = 0
    cost: float = 0.0
    duration: float = 0.0
    tool_calls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "state": self.state.value,
            "output": self.output,
            "error": self.error,
            "tokens_used": self.tokens_used,
            "cost": self.cost,
            "duration": self.duration,
            "tool_calls": self.tool_calls,
        }


# ── CoordinatorAgent ─────────────────────────────────────────


class CoordinatorAgent:
    """Orchestrates multi-step work using cheap planning + smart execution.

    Parameters
    ----------
    tool_registry : dict[str, Any]
        Mapping of available tool names to tool objects (or ``None``).
    session_context : dict[str, Any] | None
        Optional session metadata passed through to workers.
    """

    MAX_WORKERS = MAX_WORKERS
    MAX_CONCURRENT = MAX_CONCURRENT
    ORCHESTRATOR_MODEL = ORCHESTRATOR_MODEL
    WORKER_MODEL_FAST = WORKER_MODEL_FAST
    WORKER_MODEL_SMART = WORKER_MODEL_SMART
    WORKER_MODEL_DEFAULT = WORKER_MODEL_DEFAULT

    def __init__(
        self,
        tool_registry: dict[str, Any] | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> None:
        self.tool_registry: dict[str, Any] = tool_registry or {}
        self.session_context: dict[str, Any] = session_context or {}
        self._workers: dict[str, WorkerSpec] = {}
        self._results: dict[str, WorkerResult] = {}
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

    # ── Public API ───────────────────────────────────────────

    async def orchestrate(
        self, user_request: str, preset_specs: list[WorkerSpec] | None = None
    ) -> str:
        """Run the full plan→execute→synthesise lifecycle.

        *preset_specs*: when provided (e.g. by the formation bridge), the planning
        phase is skipped and these specialists are used directly. Returns a
        human-readable summary of all worker outcomes.
        """
        # Reset state for a fresh run
        self._workers = {}
        self._results = {}

        # Phase 1: Plan (or use injected formation specialists)
        specs = preset_specs if preset_specs else await self._plan_work(user_request)

        # Cap at MAX_WORKERS
        specs = specs[: self.MAX_WORKERS]

        # Update internal tracker AFTER cap
        self._workers = {s.worker_id: s for s in specs}

        # Phase 2: Execute
        await self._execute_workers(specs)

        # Phase 3: Synthesise
        summary = await self._synthesize_results(user_request)
        return summary

    # ── Properties ───────────────────────────────────────────

    @property
    def worker_count(self) -> int:
        """Number of workers (post-cap)."""
        return len(self._workers)

    @property
    def results(self) -> dict[str, WorkerResult]:
        """Snapshot of all worker results."""
        return dict(self._results)

    @property
    def failed_workers(self) -> list[str]:
        """IDs of workers that ended in FAILED state."""
        return [
            wid
            for wid, res in self._results.items()
            if res.state == WorkerState.FAILED
        ]

    # ── Phase 1: Planning ────────────────────────────────────

    async def _plan_work(self, request: str) -> list[WorkerSpec]:
        """Use the cheap orchestrator model to break *request* into workers."""
        tool_names = sorted(self.tool_registry.keys())
        prompt = (
            "Break this task into independent work items.\n\n"
            f"Task: {request}\n\n"
            f"Available tools: {tool_names}\n\n"
            "For each item, specify:\n"
            "- worker_id: short identifier\n"
            "- task_description: what to do\n"
            "- tools_allowed: list of tool names from the set above\n"
            "- model: 'fast' for simple, 'smart' for complex\n"
            "- depends_on: list of worker_ids that must complete first\n\n"
            "Respond as a JSON array of objects."
        )
        messages = [
            {"role": "system", "content": "You are a task planner. Respond only with a JSON array."},
            {"role": "user", "content": prompt},
        ]
        result = await asyncio.to_thread(self._call_orchestrator, messages)
        return self._parse_plan(result.content)

    def _parse_plan(self, response: str) -> list[WorkerSpec]:
        """Parse orchestrator JSON into ``WorkerSpec`` objects.

        Tolerates markdown code fences and auto-assigns/deduplicates ids.
        """
        text = response.strip()
        # Strip markdown code fences
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()

        # Try full parse first
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: find the first JSON array in the text
            match = re.search(r"\[.*]", text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.warning("Could not parse plan JSON, returning empty")
                    return []
            else:
                logger.warning("No JSON array found in plan response")
                return []

        if not isinstance(data, list):
            data = [data]

        specs: list[WorkerSpec] = []
        seen_ids: set[str] = set()
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            wid = str(item.get("worker_id", f"w{idx}"))
            # Deduplicate
            if wid in seen_ids:
                wid = f"{wid}_{idx}"
            seen_ids.add(wid)

            try:
                timeout = int(item.get("timeout", 120))
            except (ValueError, TypeError):
                timeout = 120

            specs.append(
                WorkerSpec(
                    worker_id=wid,
                    task_description=str(item.get("task_description", item.get("description", ""))),
                    tools_allowed=item.get("tools_allowed", []),
                    model=str(item.get("model", "auto")),
                    depends_on=item.get("depends_on", []),
                    timeout=timeout,
                    context=str(item.get("context", "")),
                )
            )
        return specs

    # ── Phase 2: Execution ───────────────────────────────────

    async def _execute_workers(self, specs: list[WorkerSpec]) -> None:
        """Execute workers in topological-dependency order."""
        batches = self._topo_sort(specs)
        for batch in batches:
            tasks = [self._run_worker(spec) for spec in batch]
            await asyncio.gather(*tasks)

    async def _run_worker(self, spec: WorkerSpec) -> WorkerResult:
        """Run a single worker with semaphore-based concurrency control."""
        async with self._semaphore:
            result = WorkerResult(
                worker_id=spec.worker_id,
                state=WorkerState.RUNNING,
            )
            start = time.monotonic()

            try:
                context = self._build_worker_context(spec)
                model = self._resolve_model(spec.model)

                output = await self._run_worker_conversation(
                    task=spec.task_description,
                    context=context,
                    tools=spec.tools_allowed,
                    model=model,
                    timeout=spec.timeout,
                )

                result.output = output
                result.state = WorkerState.COMPLETED

            except Exception as exc:
                result.error = str(exc)
                result.state = WorkerState.FAILED
                logger.debug("Worker %s failed: %s", spec.worker_id, exc)

            result.duration = time.monotonic() - start
            self._results[spec.worker_id] = result
            return result

    async def _run_worker_conversation(
        self,
        task: str,
        context: str,
        tools: list[str],
        model: str,
        timeout: int,
    ) -> str:
        """Run a worker as a real ReAct child agent and return its final answer.

        Each worker is a scoped ``ConversationalAgent.run_agentic`` child (full
        tool-using loop), not a single-turn LLM call — the same proven path
        DelegateTool uses. Guards copied from DelegateTool: bounded iteration budget,
        toolset scoping, and **no delegation/coordinator toolset** so a worker can't
        recursively spawn another coordinator (depth is capped at 1 here).
        """
        from navig.agent.conv import ConversationalAgent

        # Map the planner's model hint to a routing tier. run_agentic resolves the
        # concrete model (Opus 4.8 for "big" when an Anthropic key is present).
        tier = "big" if str(model) in ("smart", WORKER_MODEL_SMART) else "small"

        # Scope tools. tools_allowed are toolset names (e.g. "research", "code"); never
        # hand a child "delegation"/"full" so it cannot re-enter the coordinator.
        unsafe = {"delegation", "full", "coordinator"}
        scoped = [t for t in (tools or []) if t not in unsafe]
        toolset: str | list[str] = scoped or "core"

        prompt = task if not context else f"Context:\n{context}\n\nTask:\n{task}"
        # Bounded budget — keep specialists cheap; the parent's loop does the heavy lift.
        budget = max(4, min(15, getattr(self, "_worker_iterations", 12)))

        child = ConversationalAgent()
        try:
            child._user_identity = (self.session_context or {}).get("user_identity", {})
        except Exception:  # noqa: BLE001
            pass
        return await asyncio.wait_for(
            child.run_agentic(
                message=prompt,
                max_iterations=budget,
                toolset=toolset,
                tier_override=tier,
                cost_tracker=getattr(self, "cost_tracker", None),
            ),
            timeout=timeout,
        )

    # ── Phase 3: Synthesis ───────────────────────────────────

    async def _synthesize_results(self, original_request: str) -> str:
        """Use cheap model to produce a summary of all worker outcomes.

        When the adversarial verifier is enabled, each completed specialist's output
        is fact-checked *before* it's merged, so the coordinator returns a **verified**
        synthesis rather than a blind concatenation. Flagged outputs are labelled so the
        synthesizer (and the user) can weigh them appropriately.
        """
        verdicts = await self._verify_worker_outputs(original_request)

        parts: list[str] = []
        for wid, res in self._results.items():
            status = "✓" if res.state == WorkerState.COMPLETED else "✗"
            text = res.output[:2000] if res.output else (res.error or "no output")
            flag = ""
            v = verdicts.get(wid)
            if v is not None and not v.safe:
                flag = f" ⚠ UNVERIFIED ({v.reason})"
            parts.append(f"### Worker {wid} [{status}]{flag}\n{text}")
        results_text = "\n\n".join(parts)

        prompt = (
            f"Original request: {original_request}\n\n"
            f"Worker results:\n{results_text}\n\n"
            "Provide a concise summary of what was accomplished, "
            "any failures, and suggested next steps."
        )
        messages = [
            {"role": "system", "content": "Summarise worker results concisely."},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await asyncio.to_thread(self._call_orchestrator, messages)
            return result.content
        except Exception as exc:
            # Graceful fallback: build a local summary
            logger.debug("Synthesis LLM failed, building local summary: %s", exc)
            lines = [f"Completed {len(self._results)} workers for: {original_request}"]
            for wid, res in self._results.items():
                mark = "✓" if res.state == WorkerState.COMPLETED else "✗"
                lines.append(f"  {mark} {wid}: {res.output[:200] if res.output else res.error or 'no output'}")
            return "\n".join(lines)

    async def _verify_worker_outputs(self, original_request: str) -> dict[str, Any]:
        """Adversarially fact-check completed worker outputs. Returns {worker_id: Verdict}.

        No-op (empty dict) when the verifier is disabled. Best-effort and concurrent.
        """
        try:
            from navig.agent.verifier import get_verifier

            verifier = get_verifier()
            if not verifier.enabled:
                return {}
        except Exception:  # noqa: BLE001
            return {}

        completed = [
            (wid, res)
            for wid, res in self._results.items()
            if res.state == WorkerState.COMPLETED and res.output
        ]
        if not completed:
            return {}

        async def _check(wid: str, output: str):
            try:
                return wid, await verifier.verify_claim(output[:2000], context=original_request)
            except Exception:  # noqa: BLE001
                return wid, None

        results = await asyncio.gather(*[_check(wid, res.output) for wid, res in completed])
        return {wid: v for wid, v in results if v is not None}

    # ── LLM call helpers ─────────────────────────────────────

    @staticmethod
    def _call_orchestrator(messages: list[dict[str, str]]):
        """Synchronous call to the cheap orchestrator model.

        Wrapped by ``asyncio.to_thread`` in async callers.
        """
        from navig.llm_generate import run_llm

        return run_llm(
            messages=messages,
            model_override=ORCHESTRATOR_MODEL,
            temperature=0.2,
            max_tokens=2048,
        )

    @staticmethod
    def _call_worker_model(
        messages: list[dict[str, str]],
        model: str,
        timeout: int,
    ):
        """Synchronous call to a worker model.

        Wrapped by ``asyncio.to_thread`` in async callers.
        """
        from navig.llm_generate import run_llm

        return run_llm(
            messages=messages,
            model_override=model,
            temperature=0.4,
            max_tokens=_DEFAULT_MAX_TOKENS,
            timeout=float(timeout),
        )

    # ── Dependency graph utilities ───────────────────────────

    def _topo_sort(
        self, specs: list[WorkerSpec]
    ) -> list[list[WorkerSpec]]:
        """Topological sort into parallel execution batches.

        Each batch contains workers whose dependencies are satisfied by
        all previous batches.  Falls back to sequential (one per batch)
        if a cycle is detected.
        """
        id_map = {s.worker_id: s for s in specs}
        remaining = set(id_map.keys())
        completed: set[str] = set()
        batches: list[list[WorkerSpec]] = []

        max_iters = len(specs) + 1
        for _ in range(max_iters):
            if not remaining:
                break
            batch: list[WorkerSpec] = []
            for wid in list(remaining):
                deps = set(id_map[wid].depends_on) & set(id_map.keys())
                if deps <= completed:
                    batch.append(id_map[wid])
            if not batch:
                # Cycle detected — fall back to sequential
                logger.warning("Cycle detected in worker dependencies, falling back to sequential")
                batches.extend([[id_map[wid]] for wid in remaining])
                break
            for spec in batch:
                remaining.discard(spec.worker_id)
                completed.add(spec.worker_id)
            batches.append(batch)

        return batches

    def _build_worker_context(self, spec: WorkerSpec) -> str:
        """Assemble context from spec and completed dependency results."""
        parts: list[str] = []
        if spec.context:
            parts.append(spec.context)
        for dep_id in spec.depends_on:
            dep_result = self._results.get(dep_id)
            if dep_result and dep_result.state == WorkerState.COMPLETED:
                parts.append(
                    f"[Result from {dep_id}]: {dep_result.output[:1000]}"
                )
        return "\n\n".join(parts)

    def _resolve_model(self, hint: str) -> str:
        """Map a model hint to a concrete model identifier."""
        if hint == "fast":
            return WORKER_MODEL_FAST
        if hint == "smart":
            return WORKER_MODEL_SMART
        return WORKER_MODEL_DEFAULT

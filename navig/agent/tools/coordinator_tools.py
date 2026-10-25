"""
navig.agent.tools.coordinator_tools — Agent tools for coordinator mode.

Provides tools for the LLM to orchestrate multi-step work:

* ``coordinator_run``    — dispatch a complex request to CoordinatorAgent
                           (optionally ``background=true`` → run_id)
* ``coordinator_status`` — inspect per-worker outcomes from the last run
* ``subagent_list``      — live + recent orchestration runs
* ``subagent_inspect``   — one run's full record (workers, results, errors)
* ``subagent_interrupt`` — cancel one of THIS process's background runs

These tools are registered in the ``"coordinator"`` toolset (recursion-guarded:
a worker never receives the coordination tools).

Mission B 4b: run tracking extends the coordinator's own identity (the
one-shot run is the unit — ``coordination.AgentRegistry`` models long-lived
agents and the background-task manager models shell jobs, so neither fits a
run record).  Background runs execute on ONE module-owned daemon loop thread
because each tool dispatch runs on an ephemeral ``asyncio.run`` loop that dies
with the call.  ``subagent_send`` is intentionally absent: coordinator workers
are one-shot (no mid-run input channel) — an honest defer, not a fake stub.

FB-01 implementation · Mission B 4b.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from navig.agent.coordinator import CoordinatorAgent
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

#: Bounded history of orchestration runs (insertion order = start recency).
_MAX_RUNS = 32
_RUNS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

_BG_LOCK = threading.Lock()
_BG_LOOP: asyncio.AbstractEventLoop | None = None
_BG_THREAD: threading.Thread | None = None


# ── background loop (one daemon thread for every background run) ──


def _run_loop_forever(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop)]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        finally:
            loop.close()


def _ensure_bg_loop() -> asyncio.AbstractEventLoop:
    """The module-owned daemon loop; lazily started, restartable if closed."""
    global _BG_LOOP, _BG_THREAD
    with _BG_LOCK:
        if _BG_LOOP is None or _BG_LOOP.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=_run_loop_forever,
                args=(loop,),
                name="navig-subagent-runs",
                daemon=True,
            )
            thread.start()
            _BG_LOOP = loop
            _BG_THREAD = thread
        return _BG_LOOP


def _new_record(kind: str, task_desc: str, request: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "run_id": f"run-{__import__('uuid').uuid4().hex[:8]}",
        "kind": kind,
        "status": "running",
        "task": task_desc,
        "request": request[:500],
        "started_at": time.time(),
        "finished_at": None,
        "workers": None,
        "summary": None,
        "results": None,
        "error": None,
        "_task": None,  # asyncio.Task on the background loop (not serialized)
    }
    _RUNS[record["run_id"]] = record
    while len(_RUNS) > _MAX_RUNS:
        _RUNS.popitem(last=False)
    return record


def _public_record(record: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    out = {k: v for k, v in record.items() if not k.startswith("_")}
    if not full:
        # Worker results stay inspect-only; the (≤500-char) request rides along
        # so a list row identifies its run without a second call.
        out.pop("results", None)
    return out


async def _run_coordinator_background(
    record: dict[str, Any],
    agent: CoordinatorAgent,
    request: str,
) -> None:
    """Await the orchestration on the background loop; update the record."""
    try:
        summary = await agent.orchestrate(request)
    except asyncio.CancelledError:
        record["status"] = "cancelled"
        record["finished_at"] = time.time()
        raise
    except Exception as exc:  # noqa: BLE001 — recorded, never swallowed silently
        record["status"] = "failed"
        record["error"] = str(exc)[:2000]
        record["finished_at"] = time.time()
        return
    record["status"] = "done"
    record["summary"] = summary
    record["workers"] = agent.worker_count
    record["results"] = {wid: res.to_dict() for wid, res in agent.results.items()}
    record["finished_at"] = time.time()
    # Mirror into coordinator_status's last-run view for back-compat.
    CoordinatorStatusTool._last_results = dict(record["results"] or {})


# ── coordinator_run ──────────────────────────────────────────


class CoordinatorRunTool(BaseTool):
    """Dispatch a complex multi-step request to the coordinator agent."""

    name = "coordinator_run"
    description = (
        "Break a complex request into parallel worker tasks, execute them "
        "with appropriate models, and return a synthesised summary. "
        "Use this when a task clearly has multiple independent sub-steps. "
        "With background=true the run starts and returns a run_id immediately "
        "— poll subagent_list / subagent_inspect for its outcome."
    )
    owner_only = False
    parameters = [
        {
            "name": "request",
            "type": "string",
            "description": "The multi-step user request to orchestrate.",
            "required": True,
        },
        {
            "name": "tool_names",
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of tool names workers may use.",
            "required": False,
        },
        {
            "name": "background",
            "type": "boolean",
            "description": (
                "Start the run in the background and return its run_id "
                "immediately (default: false = run to completion inline)."
            ),
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        """Execute the coordinator orchestration."""
        request = args.get("request", "")
        if not request:
            return ToolResult(
                name=self.name,
                success=False,
                error="'request' parameter is required",
            )

        tool_names: list[str] = args.get("tool_names", [])
        background = bool(args.get("background", False))

        await self._emit(on_status, "planning", "Breaking request into workers")

        try:
            registry = dict.fromkeys(tool_names)
            agent = CoordinatorAgent(tool_registry=registry)

            if background:
                record = _new_record("coordinator", "background", request)
                loop = _ensure_bg_loop()
                # Create the task ON the background loop (call_soon_threadsafe):
                # ensure_future from this thread would touch another loop's
                # state without the required synchronisation.
                started = threading.Event()

                def _spawn() -> None:
                    try:
                        record["_task"] = loop.create_task(
                            _run_coordinator_background(record, agent, request)
                        )
                    finally:
                        started.set()

                loop.call_soon_threadsafe(_spawn)
                if not started.wait(timeout=5):
                    record.update(status="failed", error="background loop did not start")
                    return ToolResult(
                        name=self.name,
                        success=False,
                        error="background loop did not start",
                    )
                await self._emit(
                    on_status, "started", f"{record['run_id']} running in background"
                )
                return ToolResult(
                    name=self.name,
                    success=True,
                    output=json.dumps(
                        {
                            "run_id": record["run_id"],
                            "status": "running",
                            "note": (
                                "Background run started — poll subagent_list or "
                                "subagent_inspect(run_id) for the outcome; "
                                "subagent_interrupt cancels it."
                            ),
                        }
                    ),
                )

            record = _new_record("coordinator", "inline", request)
            summary = await agent.orchestrate(request)

            # Store results for coordinator_status
            results = {wid: res.to_dict() for wid, res in agent.results.items()}
            CoordinatorStatusTool._last_results = results
            record.update(
                status="done",
                summary=summary,
                workers=agent.worker_count,
                results=results,
                finished_at=time.time(),
            )

            await self._emit(on_status, "done", f"{agent.worker_count} workers finished")

            output = {
                "summary": summary,
                "workers": agent.worker_count,
                "failed": len(agent.failed_workers),
            }
            return ToolResult(
                name=self.name,
                success=True,
                output=json.dumps(output),
            )

        except Exception as exc:
            logger.debug("coordinator_run failed: %s", exc)
            return ToolResult(
                name=self.name,
                success=False,
                error=str(exc),
            )


# ── coordinator_status ───────────────────────────────────────


class CoordinatorStatusTool(BaseTool):
    """Inspect per-worker outcomes from the last coordinator run."""

    name = "coordinator_status"
    description = (
        "Return the status and results of each worker from the most recent "
        "coordinator_run invocation. Useful for debugging or reviewing "
        "individual worker outcomes."
    )
    owner_only = False
    parameters = [
        {
            "name": "coordinator_id",
            "type": "string",
            "description": "Reserved for future multi-session support. Currently ignored.",
            "required": False,
        },
    ]

    # Class-level storage populated by CoordinatorRunTool
    _last_results: dict[str, dict[str, Any]] = {}

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        """Return the per-worker results from the last coordinator run."""
        if not self._last_results:
            return ToolResult(
                name=self.name,
                success=True,
                output=json.dumps({"workers": {}, "message": "No coordinator run results available"}),
            )

        await self._emit(on_status, "fetching", "Retrieving worker results")

        output = {
            "workers": self._last_results,
            "total": len(self._last_results),
        }
        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps(output),
        )


# ── subagent lifecycle (Mission B 4b) ────────────────────────


class SubagentListTool(BaseTool):
    """List live and recent orchestration runs of this process."""

    name = "subagent_list"
    description = (
        "List this session's orchestration runs (coordinator background and "
        "inline): run_id, status (running/done/failed/cancelled), task and "
        "timing. Use subagent_inspect for details."
    )
    owner_only = False
    parameters: list[dict[str, Any]] = []

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        runs = [_public_record(r) for r in reversed(_RUNS.values())]
        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps({"runs": runs, "total": len(runs)}),
            elapsed_ms=0,
        )


class SubagentInspectTool(BaseTool):
    """Inspect one orchestration run's full record."""

    name = "subagent_inspect"
    description = (
        "Return the full record of one run by run_id: status, workers, "
        "summary, per-worker results, and any error."
    )
    owner_only = False
    parameters = [
        {
            "name": "run_id",
            "type": "string",
            "description": "The run id (see subagent_list)",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        run_id = str(args.get("run_id") or "").strip()
        record = _RUNS.get(run_id)
        if record is None:
            return ToolResult(
                name=self.name,
                success=False,
                output=f"No run {run_id!r} — see subagent_list for live run ids.",
                error=f"unknown run_id {run_id!r}",
            )
        return ToolResult(
            name=self.name,
            success=True,
            output=json.dumps(_public_record(record, full=True)),
        )


class SubagentInterruptTool(BaseTool):
    """Cancel one of this process's background runs."""

    name = "subagent_interrupt"
    description = (
        "Request cancellation of a BACKGROUND run by run_id and report the "
        "observed state honestly: 'cancelled' only once the task actually "
        "cancelled, otherwise 'cancellation requested'. Inline (finished) runs "
        "cannot be cancelled."
    )
    owner_only = False
    parameters = [
        {
            "name": "run_id",
            "type": "string",
            "description": "The run id to interrupt (see subagent_list)",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        run_id = str(args.get("run_id") or "").strip()
        record = _RUNS.get(run_id)
        if record is None:
            return ToolResult(
                name=self.name,
                success=False,
                output=f"No run {run_id!r} — see subagent_list for live run ids.",
                error=f"unknown run_id {run_id!r}",
            )
        if record["status"] != "running":
            return ToolResult(
                name=self.name,
                success=True,
                output=f"Run {run_id} already finished with status={record['status']}.",
            )
        task = record.get("_task")
        loop = _BG_LOOP
        if task is None or loop is None or loop.is_closed():
            return ToolResult(
                name=self.name,
                success=False,
                output=(
                    f"Run {run_id} is marked running but has no cancellable "
                    "handle (process-local background loop unavailable) — "
                    "refusing to claim a cancellation that did not happen."
                ),
                error="no cancellable handle",
            )

        # Cross-thread cancel on the loop that owns the task, then observe.
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Could not reach the background loop: {exc}",
                error=str(exc),
            )
        for _ in range(30):  # ≤3s observation window
            if task.done():
                break
            await asyncio.sleep(0.1)
        if task.cancelled() or (task.done() and record["status"] == "cancelled"):
            status = "cancelled"
        elif task.done():
            status = record["status"]  # finished before the cancel landed
        else:
            status = "cancellation-requested (still stopping)"
        return ToolResult(
            name=self.name,
            success=True,
            output=f"Run {run_id}: {status}",
        )


def register_subagent_tools() -> None:
    """Register subagent_list/inspect/interrupt (toolset ``coordinator``)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(SubagentListTool(), toolset="coordinator")
    _AGENT_REGISTRY.register(SubagentInspectTool(), toolset="coordinator")
    _AGENT_REGISTRY.register(SubagentInterruptTool(), toolset="coordinator")
    logger.debug("Subagent lifecycle tools registered (list/inspect/interrupt)")

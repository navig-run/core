"""Tests for the Mission B 4b subagent lifecycle tools (coordinator additions).

Uses a FAKE CoordinatorAgent (no LLM): the points under test are run
bookkeeping, the background loop's survival beyond the tool's ephemeral loop,
accurate interrupt reporting, and honest unknown-run refusals.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

import navig.agent.tools.coordinator_tools as ct
from navig.agent.tools.coordinator_tools import (
    SubagentInspectTool,
    SubagentInterruptTool,
    SubagentListTool,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeAgent:
    """Stands in for CoordinatorAgent with controllable latency."""

    delay = 0.3
    result_summary = "fake summary"

    def __init__(self, tool_registry=None):
        self.tool_registry = tool_registry
        self.worker_count = 2
        self.results = {"w1": _FakeWorkerResult(), "w2": _FakeWorkerResult()}
        self.failed_workers = set()

    async def orchestrate(self, request: str):
        await asyncio.sleep(self.delay)
        return self.result_summary


class _FakeWorkerResult:
    def to_dict(self):
        return {"status": "success", "output": "ok"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fake agent + clean run store around every test."""
    monkeypatch.setattr(ct, "CoordinatorAgent", _FakeAgent)
    ct._RUNS.clear()
    yield
    ct._RUNS.clear()
    # Stop this test's background loop AND drop the reference so the next
    # test gets a fresh loop (a stopped-but-not-closed loop would be
    # reused by _ensure_bg_loop and silently never run callbacks).
    loop = ct._BG_LOOP
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)
    ct._BG_LOOP = None
    ct._BG_THREAD = None


class TestRegistration:
    def test_all_tools_registered_in_coordinator_toolset(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools

        register_all_tools()
        for name in (
            "coordinator_run",
            "coordinator_status",
            "subagent_list",
            "subagent_inspect",
            "subagent_interrupt",
        ):
            entry = _AGENT_REGISTRY.get_entry(name)
            assert entry is not None, f"{name} not registered"
            assert entry.toolset == "coordinator"


class TestRunRecords:
    def test_inline_run_recorded_as_done(self):
        result = _run(ct.CoordinatorRunTool().run({"request": "do the thing"}))
        assert result.success is True
        payload = json.loads(result.output)
        assert payload["summary"] == "fake summary"

        runs = json.loads(_run(SubagentListTool().run({})).output)
        assert runs["total"] == 1
        assert runs["runs"][0]["status"] == "done"
        # Back-compat mirror for coordinator_status still fills.
        assert ct.CoordinatorStatusTool._last_results != {}

    def test_background_run_survives_the_tools_loop(self):
        _FakeAgent.delay = 0.5
        result = _run(
            ct.CoordinatorRunTool().run({"request": "bg task", "background": True})
        )
        assert result.success is True
        payload = json.loads(result.output)
        assert payload["status"] == "running"
        run_id = payload["run_id"]
        assert run_id.startswith("run-")

        # The tool's asyncio.run loop is LONG GONE here — the record must
        # still reach done on the module background loop.
        deadline = time.monotonic() + 5
        status = "running"
        while time.monotonic() < deadline and status != "done":
            time.sleep(0.1)
            rec = ct._RUNS.get(run_id)
            status = rec["status"] if rec else "?"
        assert status == "done", f"background run stuck at status={status}"

        inspected = json.loads(
            _run(SubagentInspectTool().run({"run_id": run_id})).output
        )
        assert inspected["status"] == "done"
        assert inspected["summary"] == "fake summary"
        assert inspected["workers"] == 2

    def test_interrupt_reports_cancelled_accurately(self):
        _FakeAgent.delay = 30  # long enough that cancel lands mid-flight
        result = _run(
            ct.CoordinatorRunTool().run({"request": "long", "background": True})
        )
        run_id = json.loads(result.output)["run_id"]

        # Give the task a moment to actually start on the background loop.
        time.sleep(0.2)
        outcome = _run(SubagentInterruptTool().run({"run_id": run_id}))
        assert outcome.success is True
        assert "cancelled" in outcome.output, outcome.output

        rec = ct._RUNS[run_id]
        assert rec["status"] == "cancelled"

    def test_interrupt_of_finished_run_is_honest(self):
        _FakeAgent.delay = 0
        result = _run(ct.CoordinatorRunTool().run({"request": "quick"}))
        assert result.success is True
        run_id = json.loads(
            _run(SubagentListTool().run({})).output
        )["runs"][0]["run_id"]

        outcome = _run(SubagentInterruptTool().run({"run_id": run_id}))
        assert outcome.success is True
        assert "already finished" in outcome.output

    def test_unknown_run_id_refused_everywhere(self):
        for tool in (SubagentInspectTool(), SubagentInterruptTool()):
            result = _run(tool.run({"run_id": "run-nope"}))
            assert result.success is False
            assert "subagent_list" in result.output

    def test_list_shows_order_and_shape(self):
        _FakeAgent.delay = 0
        _run(ct.CoordinatorRunTool().run({"request": "first"}))
        _run(ct.CoordinatorRunTool().run({"request": "second"}))
        runs = json.loads(_run(SubagentListTool().run({})).output)
        assert runs["total"] == 2
        # Most recent first.
        assert runs["runs"][0]["request"] == "second"
        for r in runs["runs"]:
            assert {"run_id", "kind", "status", "task", "started_at"} <= set(r)

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from navig.agent.coordinator import (
    CoordinatorAgent,
    WorkerResult,
    WorkerSpec,
    WorkerState,
)
from navig.agent.tools.coordinator_tools import CoordinatorRunTool, CoordinatorStatusTool


def test_parse_plan_handles_fenced_json_and_dedupes_ids():
    agent = CoordinatorAgent(tool_registry={"read_file": None})
    response = """```json
    [
      {"worker_id": "w", "task_description": "first", "model": "fast"},
      {"worker_id": "w", "task_description": "second", "model": "smart"}
    ]
    ```"""

    specs = agent._parse_plan(response)

    assert len(specs) == 2
    assert specs[0].worker_id == "w"
    assert specs[1].worker_id == "w_1"
    assert specs[1].model == "smart"


def test_topo_sort_batches_by_dependency():
    agent = CoordinatorAgent()
    specs = [
        WorkerSpec(worker_id="a", task_description="a"),
        WorkerSpec(worker_id="b", task_description="b", depends_on=["a"]),
        WorkerSpec(worker_id="c", task_description="c", depends_on=["a"]),
    ]

    batches = agent._topo_sort(specs)
    batch_ids = [{s.worker_id for s in batch} for batch in batches]

    assert batch_ids[0] == {"a"}
    assert batch_ids[1] == {"b", "c"}


@pytest.mark.asyncio
async def test_orchestrate_caps_workers_and_returns_summary():
    agent = CoordinatorAgent()
    many_specs = [
        WorkerSpec(worker_id=f"w{i}", task_description=f"task {i}")
        for i in range(CoordinatorAgent.MAX_WORKERS + 2)
    ]

    with (
        patch.object(agent, "_plan_work", AsyncMock(return_value=many_specs)),
        patch.object(agent, "_execute_workers", AsyncMock()),
        patch.object(agent, "_synthesize_results", AsyncMock(return_value="ok")),
    ):
        out = await agent.orchestrate("do things")

    assert out == "ok"
    assert agent.worker_count == CoordinatorAgent.MAX_WORKERS


@pytest.mark.asyncio
async def test_coordinator_run_tool_success_sets_status_cache():
    tool = CoordinatorRunTool()

    fake_result = WorkerResult(worker_id="w1", state=WorkerState.COMPLETED, output="done")

    class _FakeAgent:
        def __init__(self, tool_registry=None):
            self._results = {"w1": fake_result}

        async def orchestrate(self, request: str) -> str:
            return f"summary: {request}"

        @property
        def results(self):
            return self._results

        @property
        def worker_count(self):
            return 1

        @property
        def failed_workers(self):
            return []

    with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", _FakeAgent):
        result = await tool.run({"request": "test request", "tool_names": ["read_file"]})

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["workers"] == 1
    assert payload["failed"] == 0
    assert "w1" in CoordinatorStatusTool._last_results


@pytest.mark.asyncio
async def test_coordinator_status_tool_empty_then_populated():
    CoordinatorStatusTool._last_results = {}
    status_tool = CoordinatorStatusTool()

    empty = await status_tool.run({})
    assert empty.success is True
    empty_payload = json.loads(empty.output)
    assert empty_payload["workers"] == {}

    CoordinatorStatusTool._last_results = {
        "w1": {"worker_id": "w1", "state": "completed", "output": "done"}
    }
    filled = await status_tool.run({})
    assert filled.success is True
    payload = json.loads(filled.output)
    assert payload["total"] == 1
    assert payload["workers"]["w1"]["state"] == "completed"


def test_register_coordinator_tools_adds_tool_names():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_coordinator_tools

    before = set(_AGENT_REGISTRY.available_names())
    register_coordinator_tools()
    after = set(_AGENT_REGISTRY.available_names())

    assert "coordinator_run" in after
    assert "coordinator_status" in after
    assert {"coordinator_run", "coordinator_status"}.issubset(after | before)

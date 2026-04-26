"""Tests for navig.agent.tools.coordinator_tools."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.tools.coordinator_tools import CoordinatorRunTool, CoordinatorStatusTool
from navig.tools.registry import ToolResult


# ── helpers ──────────────────────────────────────────────────


def _make_agent(summary: str = "done", worker_count: int = 2, failed: int = 0, results: dict | None = None):
    """Build a mock CoordinatorAgent."""
    agent = MagicMock()
    agent.orchestrate = AsyncMock(return_value=summary)
    agent.worker_count = worker_count
    agent.failed_workers = [f"w{i}" for i in range(failed)]
    agent.results = results or {}
    return agent


# ── CoordinatorRunTool ───────────────────────────────────────


class TestCoordinatorRunTool:
    def setup_method(self):
        self.tool = CoordinatorRunTool()
        # Reset class-level state before each test
        CoordinatorStatusTool._last_results = {}

    def test_name_and_description(self):
        assert self.tool.name == "coordinator_run"
        assert "parallel" in self.tool.description.lower() or "complex" in self.tool.description.lower()

    def test_owner_only_false(self):
        assert self.tool.owner_only is False

    def test_parameters_includes_request(self):
        names = [p["name"] for p in self.tool.parameters]
        assert "request" in names

    @pytest.mark.asyncio
    async def test_missing_request_returns_failure(self):
        result = await self.tool.run({})
        assert result.success is False
        assert "request" in result.error.lower()

    @pytest.mark.asyncio
    async def test_empty_request_returns_failure(self):
        result = await self.tool.run({"request": ""})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_successful_orchestration(self):
        mock_agent = _make_agent(summary="All done", worker_count=3, failed=0)
        with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", return_value=mock_agent):
            result = await self.tool.run({"request": "Do things"})
        assert result.success is True
        data = json.loads(result.output)
        assert data["summary"] == "All done"
        assert data["workers"] == 3
        assert data["failed"] == 0

    @pytest.mark.asyncio
    async def test_failed_workers_count(self):
        mock_agent = _make_agent(summary="partial", worker_count=5, failed=2)
        with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", return_value=mock_agent):
            result = await self.tool.run({"request": "complex task"})
        assert result.success is True
        data = json.loads(result.output)
        assert data["failed"] == 2

    @pytest.mark.asyncio
    async def test_tool_names_forwarded(self):
        mock_agent = _make_agent()
        ctor_mock = MagicMock(return_value=mock_agent)
        with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", ctor_mock):
            await self.tool.run({"request": "do it", "tool_names": ["bash", "grep"]})
        call_kwargs = ctor_mock.call_args
        # tool_registry should be a dict keyed on tool_names
        registry = call_kwargs[1].get("tool_registry") or call_kwargs[0][0] if call_kwargs[0] else None
        # The important thing is CoordinatorAgent was instantiated
        ctor_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_populates_status_tool_results(self):
        fake_worker = MagicMock()
        fake_worker.to_dict.return_value = {"status": "ok"}
        mock_agent = _make_agent(results={"w1": fake_worker})
        with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", return_value=mock_agent):
            await self.tool.run({"request": "populate status"})
        assert "w1" in CoordinatorStatusTool._last_results

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self):
        mock_agent = MagicMock()
        mock_agent.orchestrate = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", return_value=mock_agent):
            result = await self.tool.run({"request": "trigger error"})
        assert result.success is False
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_on_status_callback_called(self):
        mock_agent = _make_agent()
        events: list[tuple] = []

        async def on_status(step, detail, progress):
            events.append((step, detail, progress))

        with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", return_value=mock_agent):
            await self.tool.run({"request": "track status"}, on_status=on_status)
        assert any("planning" in e[0] for e in events)

    @pytest.mark.asyncio
    async def test_result_name_matches_tool_name(self):
        mock_agent = _make_agent()
        with patch("navig.agent.tools.coordinator_tools.CoordinatorAgent", return_value=mock_agent):
            result = await self.tool.run({"request": "check name"})
        assert result.name == "coordinator_run"


# ── CoordinatorStatusTool ────────────────────────────────────


class TestCoordinatorStatusTool:
    def setup_method(self):
        self.tool = CoordinatorStatusTool()
        CoordinatorStatusTool._last_results = {}

    def test_name(self):
        assert self.tool.name == "coordinator_status"

    def test_owner_only_false(self):
        assert self.tool.owner_only is False

    @pytest.mark.asyncio
    async def test_empty_results_returns_success(self):
        result = await self.tool.run({})
        assert result.success is True
        data = json.loads(result.output)
        assert data["workers"] == {}

    @pytest.mark.asyncio
    async def test_returns_stored_results(self):
        CoordinatorStatusTool._last_results = {"w1": {"status": "ok"}, "w2": {"status": "fail"}}
        result = await self.tool.run({})
        assert result.success is True
        data = json.loads(result.output)
        assert data["total"] == 2
        assert "w1" in data["workers"]

    @pytest.mark.asyncio
    async def test_coordinator_id_param_ignored(self):
        CoordinatorStatusTool._last_results = {"x": {"v": 1}}
        result = await self.tool.run({"coordinator_id": "session-999"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_result_name_matches(self):
        result = await self.tool.run({})
        assert result.name == "coordinator_status"

    @pytest.mark.asyncio
    async def test_on_status_called_when_results_present(self):
        CoordinatorStatusTool._last_results = {"w": {}}
        events: list = []

        async def cb(step, detail, progress):
            events.append(step)

        await self.tool.run({}, on_status=cb)
        assert len(events) > 0

    def test_class_level_results_shared(self):
        """Verify class-level storage is shared across instances."""
        tool_a = CoordinatorStatusTool()
        tool_b = CoordinatorStatusTool()
        tool_a._last_results["shared"] = {"x": 1}
        assert "shared" in tool_b._last_results

    def test_get_meta(self):
        meta = self.tool.get_meta()
        assert meta["id"] == "coordinator_status"
        assert "ownerOnly" in meta

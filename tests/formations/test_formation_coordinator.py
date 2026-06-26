"""Tests for the formation → coordinator bridge (local multi-agent)."""

from __future__ import annotations

import pytest

from navig.agent.coordinator import WORKER_MODEL_FAST, WORKER_MODEL_SMART
from navig.formations.coordinator import _specs_from_formation, run_formation_coordinator


class _Agent:
    def __init__(self, id, name, role, tools, weight, prompt="do the thing"):
        self.id = id
        self.name = name
        self.role = role
        self.tools = tools
        self.council_weight = weight
        self.system_prompt = prompt
        self.personality = "focused"


class _Formation:
    id = "test_formation"

    def __init__(self, agents):
        self.loaded_agents = {a.id: a for a in agents}


class TestSpecMapping:
    def test_maps_agents_to_worker_specs(self):
        f = _Formation([
            _Agent("arch", "Architect", "architect", ["code"], 2.0),
            _Agent("qa", "QA", "qa", ["research"], 1.0),
        ])
        specs = _specs_from_formation(f, "review the repo", max_workers=5)
        assert {s.worker_id for s in specs} == {"arch", "qa"}
        # Heavier council weight → smart/big model; lighter → fast.
        by_id = {s.worker_id: s for s in specs}
        assert by_id["arch"].model == WORKER_MODEL_SMART
        assert by_id["qa"].model == WORKER_MODEL_FAST
        # The agent's tools flow into the worker's scoped toolset.
        assert by_id["qa"].tools_allowed == ["research"]
        # The request is embedded in each task description.
        assert "review the repo" in by_id["arch"].task_description

    def test_respects_max_workers(self):
        f = _Formation([_Agent(f"a{i}", f"A{i}", "r", [], 1.0) for i in range(5)])
        specs = _specs_from_formation(f, "x", max_workers=2)
        assert len(specs) == 2


class TestRunFormation:
    @pytest.mark.asyncio
    async def test_runs_specialists_and_synthesizes(self, monkeypatch):
        # Patch the worker execution so no real LLM is called — each specialist
        # returns a canned answer; the coordinator should synthesize them.
        from navig.agent import coordinator as C

        async def fake_worker(self, task, context, tools, model, timeout):
            return f"done: {task[:15]}"

        monkeypatch.setattr(C.CoordinatorAgent, "_run_worker_conversation", fake_worker)

        # Synthesis uses a cheap model — stub it to a deterministic concat.
        async def fake_synth(self, req):
            return "SUMMARY: " + " | ".join(
                f"{wid}={res.output}" for wid, res in self._results.items()
            )

        monkeypatch.setattr(C.CoordinatorAgent, "_synthesize_results", fake_synth)

        f = _Formation([
            _Agent("arch", "Architect", "architect", ["code"], 2.0),
            _Agent("qa", "QA", "qa", ["research"], 1.0),
        ])
        result = await run_formation_coordinator(f, "audit the repo", max_workers=3)
        assert result["workers"] == 2
        assert result["failed"] == 0
        assert "SUMMARY:" in result["summary"]
        assert result["formation"] == "test_formation"

    @pytest.mark.asyncio
    async def test_no_formation_is_graceful(self):
        result = await run_formation_coordinator(None, "x")
        assert result["workers"] == 0

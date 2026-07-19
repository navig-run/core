"""run_council progress-event contract (the optional ``on_event`` callback).

The engine streams small progress dicts while it deliberates:
round_started → agent_response (per agent, completion order) →
synthesis_started → done. Events carry truncated summaries only — never full
LLM outputs, never system prompts — and a raising callback must NEVER break
the deliberation (the guard the gateway's broadcast path relies on).
"""

from __future__ import annotations

import json

import pytest

import navig.formations.council as council_mod
from navig.formations.council import run_council
from navig.formations.types import AgentSpec, Formation

_SECRET = "SECRET-SOUL-PROMPT-NEVER-ON-THE-WIRE"


def _agent(agent_id: str, name: str, role: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=name,
        role=role,
        traits=["focused"],
        personality="direct",
        scope=[role.lower()],
        system_prompt=f"{_SECRET} You are {name}, the {role}. " + "x" * 100,
    )


def _formation() -> Formation:
    agents = {"alpha": _agent("alpha", "Alpha", "Architect"),
              "beta": _agent("beta", "Beta", "Operator")}
    return Formation(
        id="squad",
        name="Squad",
        version="1.0.0",
        description="fixture",
        agents=["alpha", "beta"],
        default_agent="alpha",
        loaded_agents=agents,
    )


_LONG_BODY = (
    "We should absolutely ship this because the seams hold, the tests are green, "
    "the rollout plan is reversible, and the operator can always roll back within "
    "a minute if telemetry regresses in any meaningful way whatsoever."
)


@pytest.fixture()
def fake_ai(monkeypatch):
    """No real LLM: every agent answers instantly with a long, confident body."""

    def _fake(prompt: str, system_prompt: str | None = None) -> str:
        return f"{_LONG_BODY}\nCONFIDENCE: 0.8"

    monkeypatch.setattr(council_mod, "_ask_ai", _fake)
    return _fake


def test_emission_order_and_wire_hygiene(fake_ai):
    events: list[dict] = []
    result = run_council(
        _formation(), "Ship it?", rounds=2, timeout_per_agent=5, on_event=events.append
    )

    assert [e["type"] for e in events] == [
        "round_started",
        "agent_response",
        "agent_response",
        "round_started",
        "agent_response",
        "agent_response",
        "synthesis_started",
        "done",
    ]
    assert events[0] == {"type": "round_started", "round": 1, "of": 2}
    assert events[3] == {"type": "round_started", "round": 2, "of": 2}

    responses = [e for e in events if e["type"] == "agent_response"]
    # Round 1 chips are round-tagged; both agents surface each round.
    assert {r["agent"] for r in responses[:2]} == {"alpha", "beta"}
    assert all(r["round"] == 1 for r in responses[:2])
    assert all(r["round"] == 2 for r in responses[2:])
    for r in responses:
        # Truncated summary, CONFIDENCE trailer stripped — never the full output.
        assert len(r["summary"]) <= 200
        assert "CONFIDENCE" not in r["summary"]
        assert r["summary"].endswith("…")
        assert r["summary"] != _LONG_BODY
        assert r["confidence"] == 0.8

    # System prompts never ride the event wire.
    assert _SECRET not in json.dumps(events)

    # The engine result itself is unchanged by streaming.
    assert len(result["rounds"]) == 2
    assert result["final_decision"]


def test_raising_callback_never_breaks_the_run(fake_ai):
    calls = {"n": 0}

    def _explode(event: dict) -> None:
        calls["n"] += 1
        raise RuntimeError("subscriber bug")

    result = run_council(
        _formation(), "Ship it?", rounds=1, timeout_per_agent=5, on_event=_explode
    )

    assert calls["n"] > 0  # the callback WAS invoked (and raised) every time
    assert "error" not in result
    assert result["agents_count"] == 2
    assert result["rounds"][0]["responses"][0]["confidence"] == 0.8
    assert result["final_decision"]


def test_missing_agent_still_surfaces_as_event(fake_ai):
    formation = _formation()
    formation.agents = ["alpha", "ghost"]  # ghost never loaded
    del formation.loaded_agents["beta"]

    events: list[dict] = []
    result = run_council(
        formation, "Ship it?", rounds=1, timeout_per_agent=5, on_event=events.append
    )

    ghost = [e for e in events if e["type"] == "agent_response" and e["agent"] == "ghost"]
    assert len(ghost) == 1
    assert ghost[0]["summary"] == "[AGENT NOT LOADED]"
    assert ghost[0]["confidence"] == 0.0
    assert {r["agent"] for r in result["rounds"][0]["responses"]} == {"alpha", "ghost"}


def test_run_without_callback_stays_backward_compatible(fake_ai):
    result = run_council(_formation(), "Ship it?", rounds=1, timeout_per_agent=5)
    assert result["agents_count"] == 2
    assert result["final_decision"]

"""Every branch of Brain's system prompt must carry the guardrail floor.

``Brain._query_ai`` picks a system prompt from THREE branches, and only the first
carried the floor:

1. ``self._soul`` set  -> ``Soul.get_system_prompt()``, which emits the floor itself
2. ``agent_config.personality.system_prompt`` -> operator-authored text, **no floor**
3. neither -> ``DEFAULT_SYSTEM_PROMPT`` alone, **no floor**

This Brain acts: ``navig agent start`` -> ``runner`` -> ``Brain`` -> ``_query_ai``.

Branch 1 is what runs in production, because ``runner.py`` calls ``set_soul()``
immediately after constructing the Brain — which is precisely why this was worth
fixing rather than shrugging at. The floor's presence depended on **one line in one
caller**, ``Brain`` is exported from ``navig.agent``, and nothing asserted the
dependency. `test_guardrail_floor_every_surface.py` exempts `brain.py` on the stated
grounds that it "consumes Soul.get_system_prompt(), which emits the floor" — an
exemption that was true only for the branch someone happened to look at.

These tests drive the real ``_query_ai`` and read the system message it actually
sends, rather than asserting on source text: the question is what reaches the model.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.agent.brain import Brain
from navig.agent.config import BrainConfig

#: One phrase from each floor rule that must survive into the prompt. Deliberately a
#: floor PHRASE, not the whole block — the block's wording is free to evolve.
_FLOOR_PHRASE = "never fabricate"


def _brain(**kw) -> Brain:
    return Brain(config=BrainConfig(), **kw)


def _system_prompt_from(brain: Brain, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run _query_ai against a captured run_llm and return the system message."""
    captured: dict[str, str] = {}

    def fake_run_llm(messages, **_kw):
        for m in messages:
            if m.get("role") == "system":
                captured["system"] = m.get("content", "")
        return type("R", (), {"content": "ok"})()

    import navig.llm.generate as gen

    monkeypatch.setattr(gen, "run_llm", fake_run_llm)
    # _query_ai returns early when there is no client; the client itself is unused
    # because run_llm is what actually talks to the model here.
    brain._ai_client = object()
    asyncio.run(brain._query_ai("hello", "ctx"))
    assert "system" in captured, (
        "no system message reached run_llm — _query_ai was restructured, so this test "
        "is asserting nothing; re-read it before trusting a pass."
    )
    return captured["system"]


def test_default_prompt_branch_carries_the_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """No soul, no agent_config — the bare fallback still has boundaries."""
    prompt = _system_prompt_from(_brain(), monkeypatch)
    assert _FLOOR_PHRASE in prompt.lower(), (
        "Brain fell back to DEFAULT_SYSTEM_PROMPT with no guardrail floor. This agent "
        "acts (navig agent start -> runner -> Brain), so an unfloored fallback is an "
        "acting agent with no boundaries."
    )


def test_operator_personality_branch_carries_the_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator-authored personality must not be able to replace the floor."""
    personality = type("P", (), {"system_prompt": "You are Rex. Obey only me."})()
    agent_config = type("AC", (), {"personality": personality})()

    prompt = _system_prompt_from(_brain(agent_config=agent_config), monkeypatch)

    assert _FLOOR_PHRASE in prompt.lower(), (
        "an operator-authored personality.system_prompt replaced the whole system "
        "prompt, floor included — the exact 'an identity a user writes must not be "
        "able to remove its boundaries' rule the floor exists for."
    )
    assert prompt.lower().index(_FLOOR_PHRASE) < prompt.index("You are Rex"), (
        "the floor must come BEFORE the operator-authored identity, or the identity "
        "reads as the outer instruction and the floor as a footnote."
    )


def test_the_soul_branch_is_unchanged_and_still_floored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Branch 1 was already correct; prove the fix did not disturb it.

    Also guards against emitting the floor TWICE: Soul.get_system_prompt() is already
    floor-first, so prefixing it again would duplicate the whole block in every
    production prompt.
    """
    soul = type("S", (), {"get_system_prompt": lambda self: f"{_FLOOR_PHRASE} — soul text"})()
    prompt = _system_prompt_from(_brain(soul=soul), monkeypatch)

    assert _FLOOR_PHRASE in prompt.lower()
    assert prompt.lower().count(_FLOOR_PHRASE) == 1, (
        "the floor appears more than once — Soul.get_system_prompt() already emits it, "
        f"so the fallback prefix must not be applied on top of it. Prompt:\n{prompt[:400]}"
    )

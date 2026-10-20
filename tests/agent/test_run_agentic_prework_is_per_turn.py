"""Regression test: run_agentic's "pre-work" telemetry is per-turn, not run-cumulative.

The stage-telemetry comment at the log site promises pre-work = "everything BEFORE
the model's first byte" for the turn, and that telegram's "first token after"
minus pre-work = the model's own time-to-first-token. The timer (`_turn_t0`) was
set once at run entry, so from turn 2 on the number folded in every earlier
turn's model call and tool dispatch. Live gateway.log showed the damage: a
"pre-work" p50 of 54 s and a 46-minute maximum in runs whose actual assembly was
~30 ms — every latency investigation reads those numbers first, so a cumulative
timer sends the diagnosis at the LLM when the metric itself is lying.

Shape: turn 1 returns a tool call (with a deliberately SLOW fake model),
turn 2 returns the final answer. After the fix, turn 2's logged pre-work must
NOT include turn 1's model sleep.
"""

import re
from types import SimpleNamespace

import pytest


def _fake_llm_config():
    return SimpleNamespace(
        provider="openrouter",
        model="openai/gpt-4o",
        temperature=0.2,
        max_tokens=512,
        base_url=None,
    )


def _tool_call(name, args, tc_id=None):
    from navig.providers.clients import ToolCall

    tc = ToolCall.__new__(ToolCall)
    tc.id = tc_id or f"tc-{name}"
    tc.name = name
    tc.arguments = __import__("json").dumps(args)
    return tc


def _fake_usage():
    return {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


_MODEL_SLEEP_S = 0.6
# Turn 2's pre-work is pure in-memory assembly with everything mocked (a few ms
# measured); 500 ms leaves ~100x headroom while staying well under the 600 ms
# model sleep it must not inherit.
_TURN2_BUDGET_MS = 500


@pytest.mark.asyncio
async def test_second_turn_prework_excludes_first_turn_model_time(monkeypatch, log_messages):
    from navig.agent.conv import ConversationalAgent as ConvAgent

    class _Registry:
        def get_openai_schemas(self, toolsets):
            return []

        def available_names(self, toolsets):
            return ["search"]

        def dispatch(self, name, args, vault_injector=None):
            return f"result-{name}"

    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", _Registry())
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr("navig.llm.router.resolve_llm", lambda mode="coding": _fake_llm_config())
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())

    responses = iter(
        [
            SimpleNamespace(
                content=None,
                tool_calls=[_tool_call("search", {"query": "x"}, "id-1")],
                usage=_fake_usage(),
            ),
            SimpleNamespace(content="done", tool_calls=None, usage=_fake_usage()),
        ]
    )

    import asyncio

    class _FakeClient:
        async def complete(self, request):
            # Simulate a slow model on the FIRST turn only: 600 ms the old
            # run-scoped timer would fold into turn 2's "pre-work".
            if not getattr(self, "_slept", False):
                self._slept = True
                await asyncio.sleep(_MODEL_SLEEP_S)
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    agent = ConvAgent()
    result = await agent.run_agentic(message="do a two-turn thing", max_iterations=5)
    assert result == "done"

    prework = [
        int(m.group(1))
        for line in log_messages
        if (m := re.search(r"run_agentic pre-work: (\d+)ms", line))
    ]
    assert len(prework) >= 2, (
        f"expected a pre-work log per model turn, got {len(prework)}: {prework}"
    )

    turn1_ms, turn2_ms = prework[0], prework[1]
    sleep_ms = int(_MODEL_SLEEP_S * 1000)
    assert turn2_ms < _TURN2_BUDGET_MS, (
        f"turn 2 pre-work was {turn2_ms} ms — it inherited turn 1's {sleep_ms} ms "
        f"model sleep (run-cumulative timer). turn1={turn1_ms}ms, all={prework}"
    )
    # Belt and braces: the value must be smaller than the sleep it used to absorb,
    # with both numbers in the message so a regression is self-explaining.
    assert turn2_ms < sleep_ms, (
        f"turn 2 pre-work {turn2_ms} ms >= first-turn model sleep {sleep_ms} ms; "
        f"timer no longer restarts per turn? all={prework}"
    )

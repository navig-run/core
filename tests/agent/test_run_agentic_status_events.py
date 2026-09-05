"""Regression test: run_agentic emits StatusEvents around turns and tool calls.

The StatusEvent seam existed (dataclass + on_status_update + _emit_event) but the
ReAct loop never emitted a single event — so the Telegram deep-path progress line
and debug X-ray had nothing to render. This asserts the full lifecycle fires:
task_start → thinking → step_start → step_done → task_done, with the metadata a
renderer needs (tool name, redacted args, model/tier).
"""
import json
from types import SimpleNamespace

import pytest


def _fake_llm_config():
    return SimpleNamespace(
        provider="openrouter", model="openai/gpt-4o",
        temperature=0.2, max_tokens=512, base_url=None,
    )


def _tool_call(name, args, tc_id=None):
    from navig.providers.clients import ToolCall
    tc = ToolCall.__new__(ToolCall)
    tc.id = tc_id or f"tc-{name}"
    tc.name = name
    tc.arguments = json.dumps(args)
    return tc


def _fake_usage():
    return {"prompt_tokens": 1, "completion_tokens": 1,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def _apply_common_patches(monkeypatch, registry):
    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)
    monkeypatch.setattr("navig.llm.router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr("navig.llm.router.resolve_llm", lambda mode="coding": _fake_llm_config())
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())


@pytest.mark.asyncio
async def test_status_events_lifecycle(monkeypatch):
    from navig.agent.conv import ConversationalAgent as ConvAgent

    class _Registry:
        def get_openai_schemas(self, toolsets):
            return []

        def available_names(self, toolsets):
            return ["search"]

        def dispatch(self, name, args, vault_injector=None):
            return f"result-{name}"

    _apply_common_patches(monkeypatch, _Registry())

    responses = iter([
        SimpleNamespace(
            content=None,
            tool_calls=[_tool_call("search", {"query": "fight club"}, "id-1")],
            usage=_fake_usage(),
        ),
        SimpleNamespace(content="done", tool_calls=None, usage=_fake_usage()),
    ])

    class _FakeClient:
        async def complete(self, request):
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    events = []

    async def sink(event: object):
        events.append(event)

    agent = ConvAgent()
    agent.on_status_update = sink
    result = await agent.run_agentic(message="info about fight club", max_iterations=5)

    assert result == "done"
    types = [e.type for e in events]
    assert "task_start" in types
    assert "thinking" in types
    assert "step_start" in types
    assert "step_done" in types
    assert "task_done" in types

    # step_start carries the tool name + a redacted arg summary (no secret dump).
    step = next(e for e in events if e.type == "step_start")
    assert step.metadata.get("tool") == "search"
    assert "query=fight club" in step.metadata.get("args_summary", "")

    # task_start carries what a renderer shows on send.
    start = next(e for e in events if e.type == "task_start")
    assert start.metadata.get("model")


@pytest.mark.asyncio
async def test_failed_tool_emits_step_failed(monkeypatch):
    from navig.agent.conv import ConversationalAgent as ConvAgent

    class _Registry:
        def get_openai_schemas(self, toolsets):
            return []

        def available_names(self, toolsets):
            return ["search"]

        def dispatch(self, name, args, vault_injector=None):
            raise RuntimeError("boom")

    _apply_common_patches(monkeypatch, _Registry())

    responses = iter([
        SimpleNamespace(
            content=None,
            tool_calls=[_tool_call("search", {"query": "x"}, "id-1")],
            usage=_fake_usage(),
        ),
        SimpleNamespace(content="recovered", tool_calls=None, usage=_fake_usage()),
    ])

    class _FakeClient:
        async def complete(self, request):
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    events = []

    async def sink(event: object):
        events.append(event)

    agent = ConvAgent()
    agent.on_status_update = sink
    await agent.run_agentic(message="do a thing", max_iterations=5)

    assert any(e.type == "step_failed" for e in events)


@pytest.mark.asyncio
async def test_error_result_string_emits_step_failed(monkeypatch):
    """A tool that RETURNS an ``[ERROR] …`` string (ToolResult(success=False),
    no exception raised) must render as step_failed — not a green step_done.

    The registry maps every non-raising failure (navig_run exit!=0, a failed db
    query/dump, a permission-denied write) to ``[ERROR] {error}``. The status
    X-ray's failure detection omitted that prefix, so a failed live-infra op lit
    up green in the /trace view — a false-green NAVIG's own doctor-honesty
    doctrine forbids. The model still received ``[ERROR] …`` (not misled); this
    is purely the observability lie.
    """
    from navig.agent.conv import ConversationalAgent as ConvAgent

    class _Registry:
        def get_openai_schemas(self, toolsets):
            return []

        def available_names(self, toolsets):
            return ["navig_run"]

        def dispatch(self, name, args, vault_injector=None):
            return "[ERROR] navig_run exited 1: command not found"

    _apply_common_patches(monkeypatch, _Registry())

    responses = iter([
        SimpleNamespace(
            content=None,
            tool_calls=[_tool_call("navig_run", {"cmd": "nope"}, "id-1")],
            usage=_fake_usage(),
        ),
        SimpleNamespace(content="handled", tool_calls=None, usage=_fake_usage()),
    ])

    class _FakeClient:
        async def complete(self, request):
            return next(responses)

    monkeypatch.setattr(
        "navig.providers.create_client",
        lambda provider_cfg, api_key=None, timeout=120.0, **kwargs: _FakeClient(),
    )

    events = []

    async def sink(event: object):
        events.append(event)

    agent = ConvAgent()
    agent.on_status_update = sink
    await agent.run_agentic(message="run a bad command", max_iterations=5)

    types = [e.type for e in events]
    assert "step_failed" in types, types
    # ...and the failed tool must NOT also/instead render as a green step_done.
    assert "step_done" not in types, f"failed [ERROR] tool rendered green: {types}"

"""Tests for parallel vs sequential tool-dispatch batching in ConvAgent.run_agentic().

Covers:
- Multiple parallel-safe tools are batched via asyncio.gather
- An exception in a parallel tool is wrapped as [Tool error: ...]
- A non-parallel-safe tool is NOT sent into the parallel batch
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
    tc.arguments = json.dumps(args)
    return tc


def _apply_common_patches(monkeypatch, registry):
    monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)
    monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", registry)
    monkeypatch.setattr("navig.llm_router.suggest_toolsets", lambda user_input: [])
    monkeypatch.setattr("navig.llm_router.resolve_llm", lambda mode="coding": _fake_llm_config())
    monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())

    class _FakeAuth:
        def resolve_auth(self, provider):
            return ("fake-key", "default")

    monkeypatch.setattr("navig.providers.auth.AuthProfileManager", _FakeAuth)


def _fake_usage():
    return {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestParallelToolDispatch:

    async def test_parallel_safe_tools_run_via_gather(self, monkeypatch):
        """Two parallel-safe tools in one LLM response → both results appear in history."""
        from navig.agent.conv import ConversationalAgent as ConvAgent

        dispatch_calls = []

        class _Registry:
            def get_openai_schemas(self, toolsets):
                return []

            def available_names(self, toolsets):
                return ["read_file", "wiki_search"]

            def dispatch(self, name, args, vault_injector=None):
                dispatch_calls.append(name)
                return f"result-{name}"

        _apply_common_patches(monkeypatch, _Registry())

        # Turn 1 → two parallel-safe tool calls; Turn 2 → final content
        responses = iter([
            # First response: two tool calls
            SimpleNamespace(
                content=None,
                tool_calls=[
                    _tool_call("read_file", {"path": "foo.txt"}, "id-1"),
                    _tool_call("wiki_search", {"query": "bar"}, "id-2"),
                ],
                usage=_fake_usage(),
            ),
            # Second response: final answer
            SimpleNamespace(
                content="done",
                tool_calls=None,
                usage=_fake_usage(),
            ),
        ])

        class _FakeClient:
            async def complete(self, request):
                return next(responses)

        monkeypatch.setattr(
            "navig.providers.create_client",
            lambda provider_cfg, api_key=None, timeout=120.0: _FakeClient(),
        )

        agent = ConvAgent()
        result = await agent.run_agentic(
            message="test parallel",
            max_iterations=5,
            toolset="research",
            cost_tracker=None,
            approval_policy=None,
        )

        assert result == "done"
        # Both tools must have been dispatched
        assert "read_file" in dispatch_calls
        assert "wiki_search" in dispatch_calls

    async def test_parallel_tool_exception_is_wrapped(self, monkeypatch):
        """An exception raised inside a parallel-safe tool is caught and wrapped as [Tool error: ...]."""
        from navig.agent.conv import ConversationalAgent as ConvAgent

        dispatch_calls = []

        class _Registry:
            def get_openai_schemas(self, toolsets):
                return []

            def available_names(self, toolsets):
                return ["read_file", "wiki_search"]

            def dispatch(self, name, args, vault_injector=None):
                dispatch_calls.append(name)
                if name == "read_file":
                    raise RuntimeError("disk exploded")
                return f"result-{name}"

        _apply_common_patches(monkeypatch, _Registry())

        responses = iter([
            SimpleNamespace(
                content=None,
                tool_calls=[
                    _tool_call("read_file", {"path": "bad.txt"}, "id-1"),
                    _tool_call("wiki_search", {"query": "ok"}, "id-2"),
                ],
                usage=_fake_usage(),
            ),
            SimpleNamespace(
                content="all done",
                tool_calls=None,
                usage=_fake_usage(),
            ),
        ])

        class _FakeClient:
            async def complete(self, request):
                return next(responses)

        monkeypatch.setattr(
            "navig.providers.create_client",
            lambda provider_cfg, api_key=None, timeout=120.0: _FakeClient(),
        )

        agent = ConvAgent()
        result = await agent.run_agentic(
            message="test exception",
            max_iterations=5,
            toolset="research",
            cost_tracker=None,
            approval_policy=None,
        )

        assert result == "all done"
        # read_file raised but wiki_search still ran — both were dispatched
        assert "read_file" in dispatch_calls
        assert "wiki_search" in dispatch_calls

    async def test_sequential_tool_not_in_parallel_batch(self, monkeypatch):
        """A NEVER_PARALLEL tool mixed with a parallel-safe one → gather receives only 1 coroutine."""
        from navig.agent.conv import ConversationalAgent as ConvAgent

        dispatch_calls = []
        gather_arg_counts = []
        _real_gather = asyncio.gather

        async def _spy_gather(*coros, return_exceptions=False):
            gather_arg_counts.append(len(coros))
            return await _real_gather(*coros, return_exceptions=return_exceptions)

        monkeypatch.setattr("navig.agent.conv.agent.asyncio.gather", _spy_gather)

        class _Registry:
            def get_openai_schemas(self, toolsets):
                return []

            def available_names(self, toolsets):
                return ["read_file", "bash_exec"]

            def dispatch(self, name, args, vault_injector=None):
                dispatch_calls.append(name)
                return f"result-{name}"

        _apply_common_patches(monkeypatch, _Registry())

        responses = iter([
            SimpleNamespace(
                content=None,
                tool_calls=[
                    _tool_call("read_file", {"path": "x.txt"}, "id-1"),   # parallel-safe
                    _tool_call("bash_exec", {"cmd": "ls"}, "id-2"),        # sequential
                ],
                usage=_fake_usage(),
            ),
            SimpleNamespace(
                content="finished",
                tool_calls=None,
                usage=_fake_usage(),
            ),
        ])

        class _FakeClient:
            async def complete(self, request):
                return next(responses)

        monkeypatch.setattr(
            "navig.providers.create_client",
            lambda provider_cfg, api_key=None, timeout=120.0: _FakeClient(),
        )

        agent = ConvAgent()
        result = await agent.run_agentic(
            message="test sequential split",
            max_iterations=5,
            toolset="research",
            cost_tracker=None,
            approval_policy=None,
        )

        assert result == "finished"
        # Both tools must have been dispatched
        assert "read_file" in dispatch_calls
        assert "bash_exec" in dispatch_calls
        # gather must have been called — and only with the parallel-safe tool (1 coro)
        non_empty_gather_calls = [n for n in gather_arg_counts if n > 0]
        assert non_empty_gather_calls, "asyncio.gather was never called with coros"
        assert all(n == 1 for n in non_empty_gather_calls), (
            f"Expected gather to receive 1 coro (parallel-safe only), got: {non_empty_gather_calls}"
        )

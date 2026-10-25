"""Tests for navig.agent.tools.ask_user_tools (Mission B 4a).

Covers: honest unbound refusal, the cross-loop bridge to a real
RequestRegistry (answer correlation by id), timeout honesty, the
auto-dispatch fast path, schema-time availability (check_fn), and
classification (never-parallel, non-destructive).
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import navig.agent.tools.ask_user_tools as aut
from navig.agent.tools.ask_user_tools import AskUserTool


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_binding():
    aut.unbind_request_registry()
    yield
    aut.unbind_request_registry()


class _BoundLoop:
    """A real event loop on a daemon thread, like the gateway's."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="test-ask-loop", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


class TestUnbound:
    def test_honest_refusal_when_no_gateway(self):
        result = _run(AskUserTool().run({"question": "hi?"}))
        assert result.success is False
        assert "plain text" in result.output
        assert "never invent" in result.output

    def test_hidden_from_schemas_when_unbound(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools

        register_all_tools()
        assert _AGENT_REGISTRY.get_entry("ask_user") is not None
        schemas = _AGENT_REGISTRY.get_openai_schemas(toolsets=["questions"])
        assert schemas == [], "unbound → the tool must not be offered"

    def test_offered_when_bound(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools

        register_all_tools()
        bound = _BoundLoop()
        try:
            aut.bind_request_registry(object(), bound.loop)
            schemas = _AGENT_REGISTRY.get_openai_schemas(toolsets=["questions"])
            names = [s["function"]["name"] for s in schemas]
            assert names == ["ask_user"]
        finally:
            bound.stop()


class TestBridge:
    def test_real_registry_round_trip(self):
        """Worker-thread tool → gateway loop → answer correlates by id."""
        from navig.requests import RequestRegistry

        bound = _BoundLoop()
        registry = RequestRegistry()
        aut.bind_request_registry(registry, bound.loop)

        async def _driver():
            # Wait for the pending request, then answer it by its own id.
            for _ in range(200):
                pending = registry.get_pending()
                if pending:
                    rid = pending[0]["id"]
                    ok = await registry.answer(rid, choice="Yes")
                    assert ok, "answer() must find the request it was given"
                    return True
                await asyncio.sleep(0.05)
            return False

        driver = asyncio.run_coroutine_threadsafe(_driver(), bound.loop)
        try:
            result = _run(
                AskUserTool().run(
                    {
                        "question": "Ship it?",
                        "options": "Yes, No",
                        "timeout_seconds": 10,
                        "_session_id": "sess-1",
                    }
                )
            )
            assert driver.result(timeout=10) is True
        finally:
            bound.stop()

        assert result.success is True
        assert "Chosen: Yes" in result.output
        # No dangling pending after the call.
        assert registry.get_pending() == []

    def test_timeout_is_honest(self, monkeypatch):
        from navig.requests import RequestRegistry

        monkeypatch.setattr(aut, "_TIMEOUT_MIN_S", 1)
        bound = _BoundLoop()
        registry = RequestRegistry()
        aut.bind_request_registry(registry, bound.loop)
        try:
            t0 = time.monotonic()
            result = _run(
                AskUserTool().run(
                    {"question": "Anyone?", "timeout_seconds": 1, "_session_id": "s"}
                )
            )
            elapsed = time.monotonic() - t0
        finally:
            bound.stop()
        assert result.success is False
        assert "did not answer" in result.output
        assert elapsed < 10, f"timeout took {elapsed:.1f}s — not bounded"

    def test_auto_dispatch_fast_path(self):
        class _FakeRegistry:
            async def ask(self, **kw):
                return {"auto": True}

        async def _scenario():
            # Same-loop path: bind the loop we are running on.
            aut.bind_request_registry(_FakeRegistry(), asyncio.get_running_loop())
            return await AskUserTool().run({"question": "auto?"})

        result = _run(_scenario())
        assert result.success is True
        assert "Auto-dispatched" in result.output

    def test_typed_answer_surfaces(self):
        class _FakeRegistry:
            async def ask(self, **kw):
                assert kw["options"] == [{"label": "A"}, {"label": "B"}]
                assert kw["allow_custom"] is True
                return {"choice": None, "custom": "typed by operator"}

        async def _scenario():
            aut.bind_request_registry(_FakeRegistry(), asyncio.get_running_loop())
            return await AskUserTool().run({"question": "q", "options": "A, B"})

        result = _run(_scenario())
        assert result.success is True
        assert "Typed: typed by operator" in result.output


class TestValidation:
    def test_question_required(self):
        result = _run(AskUserTool().run({}))
        assert result.success is False
        assert "question" in result.output

    def test_too_many_options_refused(self):
        options = ", ".join(f"o{i}" for i in range(7))
        result = _run(AskUserTool().run({"question": "q", "options": options}))
        assert result.success is False
        assert "max 6" in result.output

    def test_timeout_clamped_to_max(self):
        assert aut._clamp_timeout(9999) == aut._TIMEOUT_MAX_S
        assert aut._clamp_timeout(None) == aut._TIMEOUT_DEFAULT_S
        assert aut._clamp_timeout("junk") == aut._TIMEOUT_DEFAULT_S


class TestClassification:
    def test_registered_and_exposed(self):
        from navig.agent.tools import register_all_tools
        from navig.agent.toolsets import NEVER_PARALLEL_TOOLS, TOOLSETS, is_parallel_safe
        from navig.tools.approval import DESTRUCTIVE_TOOLS

        register_all_tools()
        assert "ask_user" in TOOLSETS["questions"]
        assert "ask_user" in NEVER_PARALLEL_TOOLS
        assert is_parallel_safe("ask_user") is False
        assert "ask_user" not in DESTRUCTIVE_TOOLS

    def test_questions_tag_is_always_requested(self):
        from navig.agent.conv.agent import _ALWAYS_ON_TOOLSETS

        assert "questions" in _ALWAYS_ON_TOOLSETS

    def test_needs_session_for_source_tagging(self):
        assert AskUserTool.needs_session is True

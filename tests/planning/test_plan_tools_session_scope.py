"""Session-scoped plan tools: isolation, write gate, exposure, wiring.

Phase 3 of NAVIG Native Agent Capability Completion (Mission B).  These tests
pin the three properties the module-global `_interceptor_ref` could not give:

1. **Isolation** — session A's plan never answers for session B (two live
   sessions, plus a distinct default slot for key-less callers).
2. **Enforcement** — ``plan_block_reason`` refuses mutating tools during
   PLANNING, and the dispatch loop ACTUALLY calls it (source-pinned at the
   call site, before the approval gate).
3. **Exposure** — the ``plan`` toolset reaches only sessions with a live plan,
   while the tools themselves register unconditionally (parity-guard safe).
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

import navig.agent.tools.plan_tools as pt
from navig.agent.plan_mode import PlanInterceptor, PlanState


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_store():
    """Every test starts (and ends) with an empty binding store."""
    pt._interceptors.clear()
    yield
    pt._interceptors.clear()


def _active_interceptor() -> PlanInterceptor:
    it = PlanInterceptor()
    it.start()
    return it


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    def test_two_sessions_do_not_share_plan_state(self):
        it_a = _active_interceptor()
        pt.set_interceptor(it_a, "session-A")
        # session B has NO binding.

        result = _run(
            pt.PlanAddStepTool().run(
                {"description": "step for B", "_session_id": "session-B"}
            )
        )
        assert result.success is False
        assert "not active" in result.output
        assert it_a.session.step_count == 0, "B's call must not touch A's plan"

        result_a = _run(
            pt.PlanAddStepTool().run(
                {"description": "step for A", "_session_id": "session-A"}
            )
        )
        assert result_a.success is True
        assert it_a.session.step_count == 1

    def test_real_session_id_never_falls_back_to_default_slot(self):
        pt.set_interceptor(_active_interceptor())  # default slot only
        result = _run(
            pt.PlanShowTool().run({"_session_id": "some-other-session"})
        )
        assert result.success is False, "a real key must NOT resolve the default slot"
        assert "not active" in result.output

    def test_keyless_caller_uses_default_slot(self):
        it = _active_interceptor()
        pt.set_interceptor(it)  # historical signature
        result = _run(pt.PlanShowTool().run({}))
        assert result.success is True

    def test_release_and_lru_cap(self):
        it = _active_interceptor()
        pt.set_interceptor(it, "s0")
        pt.release_interceptor("s0")
        assert pt.get_interceptor("s0") is None

        for i in range(pt._MAX_BOUND_SESSIONS + 10):
            pt.set_interceptor(_active_interceptor(), f"s{i}")
        assert len(pt._interceptors) <= pt._MAX_BOUND_SESSIONS
        assert pt.get_interceptor("s0") is None, "oldest binding evicted"

    def test_ensure_interceptor_is_idempotent(self):
        first = pt.ensure_interceptor("roomy")
        again = pt.ensure_interceptor("roomy")
        assert first is again
        assert first.state is PlanState.PLANNING


# ---------------------------------------------------------------------------
# Write gate
# ---------------------------------------------------------------------------


class TestWriteGate:
    def test_planning_blocks_mutators_and_allows_reads(self):
        pt.set_interceptor(_active_interceptor(), "s")
        assert pt.plan_block_reason("write_file", "s") is not None
        assert pt.plan_block_reason("edit_file", "s") is not None
        assert pt.plan_block_reason("bash_exec", "s") is not None
        assert pt.plan_block_reason("read_file", "s") is None
        assert pt.plan_block_reason("file_search", "s") is None

    def test_no_binding_fails_open(self):
        assert pt.plan_block_reason("write_file", "ghost-session") is None

    def test_non_planning_states_block_nothing(self):
        it = _active_interceptor()
        from navig.agent.plan_mode import PlanStep

        it.add_step(PlanStep(description="x"))
        it.review()
        it.approve()
        pt.set_interceptor(it, "s")
        assert pt.plan_block_reason("write_file", "s") is None


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------


class TestExposure:
    def test_plan_toolset_offered_only_to_live_plan_sessions(self):
        ts = pt.apply_plan_toolsets(["core"], "planned")
        assert ts == ["core"], "no binding → no plan tools"

        pt.set_interceptor(_active_interceptor(), "planned")
        ts = pt.apply_plan_toolsets(["core"], "planned")
        assert ts == ["core", "plan"]

    def test_plan_not_duplicated(self):
        pt.set_interceptor(_active_interceptor(), "s")
        ts = pt.apply_plan_toolsets(["core", "plan"], "s")
        assert ts.count("plan") == 1


# ---------------------------------------------------------------------------
# Registration & classification
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_tools_register_unconditionally_with_session_declaration(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools import register_all_tools

        register_all_tools()
        names = _AGENT_REGISTRY.available_names() or []
        for tool_name in ("plan_add_step", "plan_show", "plan_approve"):
            assert tool_name in names
            entry = _AGENT_REGISTRY.get_entry(tool_name)
            assert entry is not None
            assert getattr(entry.tool_ref, "needs_session", False) is True
            assert entry.toolset == "plan"

    def test_plan_toolset_table_is_real(self):
        from navig.agent.toolsets import TOOLSETS

        assert TOOLSETS["plan"] == ["plan_add_step", "plan_show", "plan_approve"]

    def test_plan_approve_is_human_gated(self):
        from navig.tools.approval import DESTRUCTIVE_TOOLS, is_destructive_tool

        assert "plan_approve" in DESTRUCTIVE_TOOLS
        assert is_destructive_tool("plan_approve") is True
        assert pt.PlanApproveTool.owner_only is True

    def test_register_plan_tools_back_compat_signature(self):
        it = PlanInterceptor()
        pt.register_plan_tools(it)  # historical positional interceptor
        assert pt.get_interceptor() is it
        pt.register_plan_tools()  # new no-arg wiring (register_all_tools path)
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        assert "plan_add_step" in (_AGENT_REGISTRY.available_names() or [])


# ---------------------------------------------------------------------------
# Wiring pins — the loop must actually call what these tests define
# ---------------------------------------------------------------------------


class TestLoopWiring:
    def _dispatch_single_source(self) -> str:
        import navig.agent.conv.agent as conv_mod

        src = inspect.getsource(conv_mod)
        assert "async def _dispatch_single(" in src, "dispatch helper renamed — re-pin the hook"
        chunk = src.split("async def _dispatch_single(", 1)[1]
        # cut at the next method at the same indentation level
        for marker in "\n        async def ", "\n    async def ", "\n    def ":
            if marker in chunk:
                chunk = chunk.split(marker, 1)[0]
        return chunk

    def test_dispatch_calls_plan_gate_before_approval_gate(self):
        chunk = self._dispatch_single_source()
        assert "plan_block_reason(" in chunk, "write gate not wired into _dispatch_single"
        assert "gate_agent_tool_call(" in chunk, "approval gate missing — pin is stale"
        assert chunk.index("plan_block_reason(") < chunk.index(
            "gate_agent_tool_call("
        ), "plan gate must run before the approval gate"

    def test_run_agentic_offers_plan_mode_entry(self):
        from navig.agent.conv import ConversationalAgent

        sig = inspect.signature(ConversationalAgent.run_agentic)
        assert "plan_mode" in sig.parameters
        assert sig.parameters["plan_mode"].default is False

    def test_run_agentic_exposes_plan_toolset_to_live_sessions(self):
        import navig.agent.conv.agent as conv_mod

        src = inspect.getsource(conv_mod)
        assert "apply_plan_toolsets(" in src, "plan exposure not wired into the schema build"
        assert src.index("apply_plan_toolsets(") < src.index("get_openai_schemas(toolsets=toolsets)")

    def test_ensure_interceptor_called_on_plan_mode_entry(self):
        import navig.agent.conv.agent as conv_mod

        src = inspect.getsource(conv_mod)
        assert "ensure_interceptor(_session_key)" in src


# ---------------------------------------------------------------------------
# Honest tool behaviour without a binding
# ---------------------------------------------------------------------------


class TestHonestRefusal:
    @pytest.mark.parametrize(
        ("tool_cls", "args"),
        [
            (pt.PlanAddStepTool, {"description": "x"}),
            (pt.PlanShowTool, {}),
            (pt.PlanApproveTool, {}),
        ],
    )
    def test_tools_report_missing_plan_instead_of_raising(self, tool_cls, args):
        result = _run(tool_cls().run({**args, "_session_id": "unbound"}))
        assert result.success is False
        assert "not active" in result.output

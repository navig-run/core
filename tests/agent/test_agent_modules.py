"""
Tests for the new agent modules (MVP1–MVP3 features F-01 through F-21).

Covers: toolsets, usage_tracker, plan_execute, prompt_caching, profiles,
        approval, llm_router (suggest_toolsets).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ═════════════════════════════════════════════════════════════
# F-04 — Toolsets
# ═════════════════════════════════════════════════════════════
from navig.agent.toolsets import (
    NAVIG_CORE_TOOLS,
    NEVER_PARALLEL_TOOLS,
    PARALLEL_SAFE_TOOLS,
    TOOLSETS,
    is_parallel_safe,
    merge_toolsets,
    resolve_toolset_names,
    validate_toolset,
)


class TestToolsets:
    """Tests for navig.agent.toolsets module."""

    def test_toolsets_dict_has_expected_keys(self):
        expected = {
            "core",
            "search",
            "research",
            "code",
            "devops",
            "memory",
            "wiki",
            "delegation",
            "full",
            "git",
            "remote",
            "lsp",
        }
        assert expected == set(TOOLSETS.keys())

    def test_core_toolset_is_list(self):
        assert isinstance(TOOLSETS["core"], list)
        assert len(TOOLSETS["core"]) > 0

    def test_full_toolset_is_none(self):
        assert TOOLSETS["full"] is None

    def test_navig_core_tools_is_frozenset(self):
        assert isinstance(NAVIG_CORE_TOOLS, frozenset)
        assert len(NAVIG_CORE_TOOLS) > 0

    def test_parallel_safe_tools_disjoint_from_never_parallel(self):
        overlap = PARALLEL_SAFE_TOOLS & NEVER_PARALLEL_TOOLS
        assert overlap == frozenset(), f"Overlap found: {overlap}"

    def test_validate_toolset_known(self):
        validate_toolset("core")  # should not raise

    def test_validate_toolset_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown toolset"):
            validate_toolset("nonexistent_toolset")

    def test_resolve_toolset_names_core(self):
        names = resolve_toolset_names("core")
        assert isinstance(names, list)
        assert len(names) > 0

    def test_resolve_toolset_names_full_returns_none(self):
        result = resolve_toolset_names("full")
        assert result is None

    def test_resolve_toolset_names_unknown_raises(self):
        with pytest.raises(ValueError):
            resolve_toolset_names("bad_name")

    def test_merge_toolsets_deduplicates(self):
        merged = merge_toolsets(["core", "core"])
        assert isinstance(merged, list)
        assert len(merged) == len(set(merged))

    def test_merge_toolsets_with_full_returns_none(self):
        result = merge_toolsets(["core", "full"])
        assert result is None

    def test_merge_toolsets_combines(self):
        merged = merge_toolsets(["core", "memory"])
        core = set(resolve_toolset_names("core") or [])
        memory = set(resolve_toolset_names("memory") or [])
        assert core | memory == set(merged)

    def test_is_parallel_safe_true(self):
        assert is_parallel_safe("web_fetch") is True

    def test_is_parallel_safe_false_for_never_parallel(self):
        assert is_parallel_safe("write_file") is False

    def test_is_parallel_safe_unknown_tool(self):
        assert is_parallel_safe("unknown_tool_xyz") is False

    def test_devops_toolset_has_navig_tools(self):
        devops = TOOLSETS.get("devops")
        assert devops is not None
        navig_tools = [t for t in devops if t.startswith("navig_")]
        assert len(navig_tools) >= 10  # At least 10 navig DevOps tools


# ═════════════════════════════════════════════════════════════
# F-08 — Usage Tracker
# ═════════════════════════════════════════════════════════════

from navig.agent.usage_tracker import (
    CostTracker,
    IterationBudget,
    SessionCost,
    UsageEvent,
    _lookup_price,
)


class TestUsageEvent:
    """Tests for UsageEvent dataclass."""

    def test_total_tokens(self):
        ev = UsageEvent(
            turn=1, model="gpt-4o", provider="openai", prompt_tokens=1000, completion_tokens=200
        )
        assert ev.total_tokens == 1200

    def test_cost_usd_known_model(self):
        ev = UsageEvent(
            turn=1, model="gpt-4o", provider="openai", prompt_tokens=1_000_000, completion_tokens=0
        )
        cost = ev.cost_usd()
        assert cost > 0.0

    def test_cost_usd_with_cache_tokens(self):
        ev = UsageEvent(
            turn=1,
            model="gpt-4o",
            provider="openai",
            prompt_tokens=500,
            completion_tokens=100,
            cache_read_tokens=200,
            cache_write_tokens=50,
        )
        cost = ev.cost_usd()
        assert cost >= 0.0

    def test_cost_usd_unknown_model_zero(self):
        ev = UsageEvent(
            turn=1,
            model="totally-unknown-model-xyz",
            provider="unknown",
            prompt_tokens=100,
            completion_tokens=100,
        )
        assert ev.cost_usd() == 0.0


class TestLookupPrice:
    """Tests for the _lookup_price helper."""

    def test_exact_match(self):
        inp, out, cr, cw = _lookup_price("gpt-4o")
        assert inp > 0.0
        assert out > 0.0

    def test_prefix_match(self):
        # "gpt-4o-2024" should prefix-match "gpt-4o"
        inp, out, cr, cw = _lookup_price("gpt-4o-2024-something")
        assert inp > 0.0

    def test_unknown_model_returns_zeros(self):
        result = _lookup_price("completely-unknown-model")
        assert result == (0.0, 0.0, 0.0, 0.0)


class TestSessionCost:
    """Tests for SessionCost dataclass."""

    def test_summary_str_no_events(self):
        sc = SessionCost(total_usd=0.0, total_tokens=0, events=[])
        s = sc.summary_str()
        assert "0 turns" in s
        assert "0 tok" in s

    def test_summary_str_with_events(self):
        ev = UsageEvent(
            turn=1, model="gpt-4o", provider="openai", prompt_tokens=500, completion_tokens=100
        )
        sc = SessionCost(total_usd=0.005, total_tokens=600, events=[ev])
        s = sc.summary_str()
        assert "1 turn" in s
        assert "600 tok" in s
        assert "$" in s

    def test_detailed_str_empty(self):
        sc = SessionCost(total_usd=0.0, total_tokens=0)
        assert "No LLM calls" in sc.detailed_str()

    def test_detailed_str_with_events(self):
        ev = UsageEvent(
            turn=1, model="gpt-4o", provider="openai", prompt_tokens=500, completion_tokens=100
        )
        sc = SessionCost(total_usd=0.005, total_tokens=600, events=[ev])
        detail = sc.detailed_str()
        assert "Turn 1" in detail
        assert "gpt-4o" in detail


class TestCostTracker:
    """Tests for the CostTracker accumulator."""

    def test_empty_session(self):
        ct = CostTracker()
        sc = ct.session_cost()
        assert sc.total_usd == 0.0
        assert sc.total_tokens == 0
        assert len(sc.events) == 0

    def test_record_and_session_cost(self):
        ct = CostTracker()
        ev = UsageEvent(
            turn=1, model="gpt-4o", provider="openai", prompt_tokens=1000, completion_tokens=500
        )
        ct.record(ev)
        sc = ct.session_cost()
        assert len(sc.events) == 1
        assert sc.total_tokens == 1500

    def test_multiple_records(self):
        ct = CostTracker()
        for i in range(5):
            ct.record(
                UsageEvent(
                    turn=i + 1,
                    model="gpt-4o",
                    provider="openai",
                    prompt_tokens=100,
                    completion_tokens=50,
                )
            )
        assert len(ct) == 5
        sc = ct.session_cost()
        assert sc.total_tokens == 750

    def test_reset(self):
        ct = CostTracker()
        ct.record(
            UsageEvent(
                turn=1, model="gpt-4o", provider="openai", prompt_tokens=100, completion_tokens=50
            )
        )
        assert len(ct) == 1
        ct.reset()
        assert len(ct) == 0

    def test_thread_safety(self):
        ct = CostTracker()
        errors = []

        def worker(start_turn: int):
            try:
                for i in range(100):
                    ct.record(
                        UsageEvent(
                            turn=start_turn + i,
                            model="gpt-4o",
                            provider="openai",
                            prompt_tokens=10,
                            completion_tokens=5,
                        )
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i * 100,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(ct) == 400


class TestIterationBudget:
    """Tests for iteration budget tracking."""

    def test_initial_state(self):
        b = IterationBudget(max_iterations=50)
        assert b.remaining() == 50
        assert b.is_exhausted() is False
        assert b.budget_used_pct() == 0.0

    def test_consume(self):
        b = IterationBudget(max_iterations=10)
        b.consume(3)
        assert b.remaining() == 7
        assert not b.is_exhausted()

    def test_exhaust(self):
        b = IterationBudget(max_iterations=5)
        b.consume(5)
        assert b.is_exhausted() is True
        assert b.remaining() == 0

    def test_overconsume_caps(self):
        b = IterationBudget(max_iterations=5)
        b.consume(100)
        assert b.remaining() == 0
        assert b.is_exhausted()

    def test_budget_used_pct(self):
        b = IterationBudget(max_iterations=100)
        b.consume(25)
        assert abs(b.budget_used_pct() - 0.25) < 0.001

    def test_child_budget(self):
        parent = IterationBudget(max_iterations=90)
        child = parent.child(max_iterations=30)
        assert child.remaining() <= 30
        assert child.max_iterations <= 30

    def test_child_consumes_from_parent(self):
        parent = IterationBudget(max_iterations=90)
        child = parent.child(max_iterations=30)
        child.consume(5)
        assert parent.remaining() == 85  # parent also decreased

    def test_child_capped_by_parent(self):
        parent = IterationBudget(max_iterations=10)
        parent.consume(8)  # 2 left
        child = parent.child(max_iterations=30)
        assert child.remaining() <= 2

    def test_zero_budget(self):
        b = IterationBudget(max_iterations=0)
        assert b.is_exhausted()
        assert b.budget_used_pct() == 1.0


# ═════════════════════════════════════════════════════════════
# F-21 — Plan-Execute Mode
# ═════════════════════════════════════════════════════════════

from navig.agent.plan_execute import (
    ExecutionPlan,
    PlanExecuteAgent,
    PlanStep,
    format_plan_report,
)


class TestPlanStep:
    """Tests for the PlanStep dataclass."""

    def test_default_status_is_pending(self):
        step = PlanStep(tool="read_file", args={"path": "/etc/hosts"})
        assert step.status == "pending"

    def test_to_dict(self):
        step = PlanStep(tool="bash_exec", args={"cmd": "ls"}, reason="List files")
        d = step.to_dict()
        assert d["tool"] == "bash_exec"
        assert d["status"] == "pending"
        assert d["reason"] == "List files"

    def test_to_dict_truncates_output(self):
        step = PlanStep(tool="t", output="x" * 1000)
        d = step.to_dict()
        assert len(d["output"]) == 500  # truncated to 500 chars


class TestExecutionPlan:
    """Tests for the ExecutionPlan dataclass."""

    def test_empty_plan(self):
        plan = ExecutionPlan(task="do nothing")
        assert plan.task == "do nothing"
        assert plan.succeeded == []
        assert plan.failed == []

    def test_created_at_auto(self):
        plan = ExecutionPlan(task="test")
        assert plan.created_at != ""
        assert "T" in plan.created_at  # ISO format

    def test_succeeded_and_failed_properties(self):
        steps = [
            PlanStep(tool="a", status="success"),
            PlanStep(tool="b", status="failed"),
            PlanStep(tool="c", status="success"),
            PlanStep(tool="d", status="skipped"),
        ]
        plan = ExecutionPlan(task="multi", steps=steps)
        assert len(plan.succeeded) == 2
        assert len(plan.failed) == 1

    def test_to_dict_summary(self):
        steps = [
            PlanStep(tool="a", status="success"),
            PlanStep(tool="b", status="failed"),
            PlanStep(tool="c", status="skipped"),
        ]
        plan = ExecutionPlan(task="test", steps=steps)
        d = plan.to_dict()
        assert d["summary"]["total"] == 3
        assert d["summary"]["succeeded"] == 1
        assert d["summary"]["failed"] == 1
        assert d["summary"]["skipped"] == 1


class TestParsePlanJson:
    """Tests for PlanExecuteAgent._parse_plan_json (internal)."""

    def _make_agent(self):
        mock_ca = MagicMock()
        return PlanExecuteAgent(mock_ca)

    def test_valid_json(self):
        agent = self._make_agent()
        raw = json.dumps(
            {
                "steps": [
                    {"tool": "read_file", "args": {"path": "/tmp"}, "reason": "Check"},
                    {"tool": "bash_exec", "args": {"cmd": "ls"}, "reason": "List"},
                ]
            }
        )
        steps = agent._parse_plan_json(raw)
        assert len(steps) == 2
        assert steps[0].tool == "read_file"
        assert steps[1].tool == "bash_exec"

    def test_fenced_json(self):
        agent = self._make_agent()
        raw = '```json\n{"steps": [{"tool": "x", "args": {}, "reason": "y"}]}\n```'
        steps = agent._parse_plan_json(raw)
        assert len(steps) == 1
        assert steps[0].tool == "x"

    def test_invalid_json_returns_empty(self):
        agent = self._make_agent()
        steps = agent._parse_plan_json("this is not json at all")
        assert steps == []

    def test_empty_steps(self):
        agent = self._make_agent()
        steps = agent._parse_plan_json('{"steps": []}')
        assert steps == []

    def test_non_dict_items_skipped(self):
        agent = self._make_agent()
        raw = json.dumps({"steps": ["not_a_dict", {"tool": "a", "args": {}, "reason": "r"}]})
        steps = agent._parse_plan_json(raw)
        assert len(steps) == 1
        assert steps[0].tool == "a"


class TestFormatPlanReport:
    """Tests for the format_plan_report function."""

    def test_report_contains_task(self):
        plan = ExecutionPlan(task="restart server", total_elapsed_ms=5000)
        plan.steps = [
            PlanStep(
                tool="bash_exec", status="success", reason="Restart", elapsed_ms=3000, output="ok"
            ),
        ]
        report = format_plan_report(plan)
        assert "restart server" in report
        assert "✅" in report
        assert "Step 1" in report

    def test_report_with_failures(self):
        plan = ExecutionPlan(task="deploy", total_elapsed_ms=2000)
        plan.steps = [
            PlanStep(
                tool="a",
                status="failed",
                error="conn refused",
                elapsed_ms=1000,
                reason="Try deploy",
            ),
        ]
        report = format_plan_report(plan)
        assert "❌" in report
        assert "conn refused" in report

    def test_report_summary_line(self):
        plan = ExecutionPlan(task="test", total_elapsed_ms=1000)
        plan.steps = [
            PlanStep(tool="a", status="success", elapsed_ms=500),
            PlanStep(tool="b", status="skipped", elapsed_ms=0),
        ]
        report = format_plan_report(plan)
        assert "1/2 succeeded" in report
        assert "1 skipped" in report


# ═════════════════════════════════════════════════════════════
# F-12 — Prompt Caching
# ═════════════════════════════════════════════════════════════

from navig.agent.prompt_caching import (
    apply_anthropic_cache_control,
    supports_caching,
)


class TestPromptCaching:
    """Tests for prompt cache injection."""

    def test_supports_caching_known_model(self):
        assert supports_caching("claude-sonnet-4") is True

    def test_supports_caching_unknown_model(self):
        assert supports_caching("gpt-4o") is False

    def test_supports_caching_prefix_match(self):
        assert supports_caching("claude-3-5-sonnet-20241022-v2") is True

    def test_apply_empty_messages(self):
        result = apply_anthropic_cache_control([])
        assert result == []

    def test_apply_unsupported_strategy(self):
        msgs = [{"role": "system", "content": "Hello"}]
        result = apply_anthropic_cache_control(msgs, strategy="unknown")
        assert result == msgs

    def test_apply_injects_cache_control(self):
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        result = apply_anthropic_cache_control(msgs)
        assert len(result) == 2
        # System message should have cache_control somewhere in content
        sys_content = result[0]["content"]
        assert isinstance(sys_content, list)
        cache_blocks = [b for b in sys_content if "cache_control" in b]
        assert len(cache_blocks) >= 1

    def test_apply_does_not_mutate_original(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        original_copy = json.dumps(msgs)
        apply_anthropic_cache_control(msgs)
        assert json.dumps(msgs) == original_copy

    def test_apply_with_ttl(self):
        msgs = [
            {"role": "system", "content": "sys"},
        ]
        result = apply_anthropic_cache_control(msgs, ttl="1h")
        sys_content = result[0]["content"]
        # Find the cache block
        cache_blocks = [b for b in sys_content if "cache_control" in b]
        assert cache_blocks[0]["cache_control"]["ttl"] == 3600

    def test_apply_tags_max_3_user_messages(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
            {"role": "user", "content": "u4"},
        ]
        result = apply_anthropic_cache_control(msgs)
        # u4 (4th user message) should NOT have cache_control
        u4 = result[7]
        assert isinstance(u4["content"], str)  # not converted to list


# ═════════════════════════════════════════════════════════════
# F-15 — Profiles
# ═════════════════════════════════════════════════════════════

from navig.agent.profiles import (
    Profile,
    resolve_profile_name,
)


class TestProfiles:
    """Tests for agent profile management."""

    def test_profile_memory_dir(self, tmp_path):
        p = Profile(name="test", home_dir=tmp_path / "profiles" / "test")
        assert p.memory_dir == tmp_path / "profiles" / "test" / "memory"

    def test_profile_wiki_dir(self, tmp_path):
        p = Profile(name="test", home_dir=tmp_path / "profiles" / "test")
        assert p.wiki_dir == tmp_path / "profiles" / "test" / "wiki"

    def test_profile_config_path(self, tmp_path):
        p = Profile(name="test", home_dir=tmp_path / "profiles" / "test")
        assert p.config_path == tmp_path / "profiles" / "test" / "config.yaml"

    def test_default_profile_is_default(self, tmp_path):
        p = Profile(name="default", home_dir=tmp_path)
        assert p.is_default is True

    def test_non_default_profile(self, tmp_path):
        p = Profile(name="work", home_dir=tmp_path)
        assert p.is_default is False

    def test_ensure_dirs(self, tmp_path):
        p = Profile(name="test", home_dir=tmp_path / "profiles" / "test")
        p.ensure_dirs()
        assert p.home_dir.exists()
        assert p.memory_dir.exists()
        assert p.wiki_dir.exists()

    def test_resolve_profile_name_env_override(self, monkeypatch):
        monkeypatch.setenv("NAVIG_PROFILE", "custom_profile")
        assert resolve_profile_name() == "custom_profile"

    def test_resolve_profile_name_default(self, monkeypatch):
        monkeypatch.delenv("NAVIG_PROFILE", raising=False)
        # Without an active_profile file, should fall back to "default"
        monkeypatch.setenv("NAVIG_HOME", str(Path(__file__).parent / "__nonexistent__"))
        assert resolve_profile_name() == "default"

    def test_default_active_toolsets(self):
        p = Profile(name="x", home_dir=Path("/tmp"))
        assert p.active_toolsets == ["core"]


# ═════════════════════════════════════════════════════════════
# F-14 / F-16 — Approval
# ═════════════════════════════════════════════════════════════

from navig.tools.approval import (
    DESTRUCTIVE_TOOLS,
    ApprovalDecision,
    ApprovalPolicy,
    needs_approval,
    reset_approval_gate,
    set_approval_policy,
)


class TestApproval:
    """Tests for the approval gate and policy system."""

    def setup_method(self):
        # Reset state between tests
        reset_approval_gate()
        set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)

    def test_destructive_tools_contains_core(self):
        assert "write_file" in DESTRUCTIVE_TOOLS
        assert "bash_exec" in DESTRUCTIVE_TOOLS

    def test_destructive_tools_contains_devops(self):
        assert "navig_run" in DESTRUCTIVE_TOOLS
        assert "navig_db_query" in DESTRUCTIVE_TOOLS
        assert "navig_docker_exec" in DESTRUCTIVE_TOOLS

    def test_needs_approval_destructive_tool(self):
        assert needs_approval("bash_exec") is True

    def test_needs_approval_safe_tool(self):
        assert needs_approval("read_file", safety_level="safe") is False

    def test_needs_approval_dangerous_level(self):
        assert needs_approval("some_unknown_tool", safety_level="dangerous") is True

    def test_needs_approval_yolo_policy(self):
        assert needs_approval("bash_exec", policy=ApprovalPolicy.YOLO) is False

    def test_needs_approval_confirm_all(self):
        assert (
            needs_approval("read_file", safety_level="safe", policy=ApprovalPolicy.CONFIRM_ALL)
            is True
        )

    def test_needs_approval_env_bypass(self, monkeypatch):
        monkeypatch.setenv("NAVIG_ALLOW_ALL_COMMANDS", "1")
        assert needs_approval("bash_exec", safety_level="dangerous") is False

    def test_approval_decision_values(self):
        assert ApprovalDecision.APPROVED.value == "approved"
        assert ApprovalDecision.DENIED.value == "denied"
        assert ApprovalDecision.TIMEOUT.value == "timeout"


# ═════════════════════════════════════════════════════════════
# F-20 — Semantic Routing → suggest_toolsets
# ═════════════════════════════════════════════════════════════

from navig.llm_router import MODE_TOOLSET_HINTS, suggest_toolsets

pytestmark = pytest.mark.integration


class TestSuggestToolsets:
    """Tests for mode→toolset routing hints."""

    def test_hints_dict_has_canonical_modes(self):
        expected_modes = {"big_tasks", "coding", "research", "small_talk", "summarize"}
        assert expected_modes == set(MODE_TOOLSET_HINTS.keys())

    def test_small_talk_returns_empty(self):
        result = suggest_toolsets(mode="small_talk")
        assert result == []

    def test_coding_mode_includes_core(self):
        result = suggest_toolsets(mode="coding")
        assert "core" in result

    def test_research_mode_includes_search(self):
        result = suggest_toolsets(mode="research")
        assert "search" in result

    def test_big_tasks_includes_devops(self):
        result = suggest_toolsets(mode="big_tasks")
        assert "devops" in result

    def test_unknown_mode_fallback(self):
        # Non-canonical mode strings are resolved via LLMModeRouter.resolve_mode,
        # which maps unknown values to "big_tasks" (the default canonical mode).
        result = suggest_toolsets(mode="nonexistent_mode_xyz")
        assert "core" in result  # big_tasks always includes core


# ═════════════════════════════════════════════════════════════
# Import smoke tests — every new module
# ═════════════════════════════════════════════════════════════


class TestModuleImports:
    """Ensure all new agent modules import without errors."""

    def test_import_toolsets(self):
        from navig.agent import toolsets

        assert hasattr(toolsets, "TOOLSETS")

    def test_import_usage_tracker(self):
        from navig.agent import usage_tracker

        assert hasattr(usage_tracker, "CostTracker")

    def test_import_plan_execute(self):
        from navig.agent import plan_execute

        assert hasattr(plan_execute, "PlanExecuteAgent")

    def test_import_prompt_caching(self):
        from navig.agent import prompt_caching

        assert hasattr(prompt_caching, "apply_anthropic_cache_control")

    def test_import_profiles(self):
        from navig.agent import profiles

        assert hasattr(profiles, "Profile")

    def test_import_context_compressor(self):
        from navig.agent import context_compressor

        assert hasattr(context_compressor, "ContextCompressor")

    def test_import_mcp_client(self):
        from navig.agent import mcp_client

        assert hasattr(mcp_client, "MCPClient")

    def test_import_delegate(self):
        from navig.agent import delegate

        assert hasattr(delegate, "DelegateTool")

    def test_import_agent_tool_registry(self):
        from navig.agent import agent_tool_registry

        assert hasattr(agent_tool_registry, "AgentToolRegistry")

    def test_import_tools_init(self):
        from navig.agent import tools

        assert hasattr(tools, "register_all_tools")

    def test_import_approval(self):
        from navig.tools import approval

        assert hasattr(approval, "ApprovalGate")


class TestDelegateImportPath:
    """Guard delegate/conv migration wiring."""

    def test_delegate_run_child_imports_conv_conversational(self):
        from inspect import getsource

        from navig.agent.delegate import DelegateTool

        source = getsource(DelegateTool._run_child)
        assert "from navig.agent.conv import ConversationalAgent" in source


class TestConvRunAgenticCompatibility:
    """Ensure conv agentic compatibility path runs natively."""

    async def test_conv_run_agentic_runs_native_loop(self, monkeypatch):
        from types import SimpleNamespace

        from navig.agent.conv import ConversationalAgent as ConvAgent

        monkeypatch.setattr("navig.agent.tools.register_all_tools", lambda: None)

        class _FakeRegistry:
            def get_openai_schemas(self, toolsets):
                return []

            def available_names(self, toolsets):
                return []

            def dispatch(self, name, args):
                return "ok"

        monkeypatch.setattr("navig.agent.agent_tool_registry._AGENT_REGISTRY", _FakeRegistry())

        monkeypatch.setattr("navig.llm_router.suggest_toolsets", lambda user_input: [])
        monkeypatch.setattr(
            "navig.llm_router.resolve_llm",
            lambda mode="coding": SimpleNamespace(
                provider="openrouter",
                model="openai/gpt-4o",
                temperature=0.2,
                max_tokens=512,
                base_url=None,
            ),
        )

        monkeypatch.setattr("navig.providers.get_builtin_provider", lambda name: object())

        class _FakeAuthProfileManager:
            def resolve_auth(self, provider):
                return ("fake-key", "default")

        monkeypatch.setattr("navig.providers.auth.AuthProfileManager", _FakeAuthProfileManager)

        class _FakeResponse:
            def __init__(self):
                self.content = "native-final"
                self.tool_calls = None
                self.usage = {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                }

        class _FakeClient:
            async def complete(self, request):
                return _FakeResponse()

        monkeypatch.setattr(
            "navig.providers.create_client",
            lambda provider_cfg, api_key=None, timeout=120.0: _FakeClient(),
        )

        agent = ConvAgent()
        result = await agent.run_agentic(
            message="hello",
            max_iterations=7,
            toolset="research",
            cost_tracker="ct",
            approval_policy="ap",
        )

        assert result == "native-final"
        assert agent.conversation_history[-2:] == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "native-final"},
        ]

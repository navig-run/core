"""Tests for agent/component.py, agent/pattern_observer.py, agent/toolsets.py."""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# pattern_observer
# ──────────────────────────────────────────────────────────────────────────────
from navig.agent.pattern_observer import PatternObserver, PatternRecord


class TestPatternRecord:
    def test_dataclass_fields(self):
        names = {f.name for f in fields(PatternRecord)}
        assert "command" in names

    def test_creation(self):
        rec = PatternRecord(command="ls -la")
        assert rec.command == "ls -la"


class TestPatternObserver:
    def test_get_recent_missing_db_returns_empty(self, tmp_path):
        obs = PatternObserver(db_path=tmp_path / "nonexistent.db")
        result = obs.get_recent()
        assert result == []

    def test_get_recent_default_limit(self, tmp_path):
        obs = PatternObserver(db_path=tmp_path / "missing.db")
        result = obs.get_recent(limit=10)
        assert isinstance(result, list)

    def test_get_recent_returns_list(self, tmp_path):
        obs = PatternObserver(db_path=tmp_path / "x.db")
        assert isinstance(obs.get_recent(), list)


# ──────────────────────────────────────────────────────────────────────────────
# component — enums & HealthStatus dataclass
# ──────────────────────────────────────────────────────────────────────────────
from navig.agent.component import ComponentState, HealthStatus


class TestComponentState:
    def test_all_members(self):
        names = {s.name for s in ComponentState}
        assert names == {
            "CREATED",
            "STARTING",
            "RUNNING",
            "STOPPING",
            "STOPPED",
            "ERROR",
            "DEGRADED",
        }

    def test_running_is_distinct_from_degraded(self):
        assert ComponentState.RUNNING != ComponentState.DEGRADED

    def test_created_is_initial(self):
        assert ComponentState.CREATED is not None

    def test_error_state_exists(self):
        assert ComponentState.ERROR is not None


class TestHealthStatus:
    def test_defaults(self):
        hs = HealthStatus(healthy=True, state=ComponentState.RUNNING)
        assert hs.healthy is True
        assert hs.state == ComponentState.RUNNING
        assert hs.message == ""
        assert isinstance(hs.last_check, datetime)
        assert hs.details == {}

    def test_unhealthy(self):
        hs = HealthStatus(healthy=False, state=ComponentState.ERROR, message="boom")
        assert hs.healthy is False
        assert hs.message == "boom"

    def test_to_dict_keys(self):
        hs = HealthStatus(healthy=True, state=ComponentState.RUNNING)
        d = hs.to_dict()
        assert "healthy" in d
        assert "state" in d

    def test_to_dict_state_is_string(self):
        hs = HealthStatus(healthy=True, state=ComponentState.RUNNING)
        d = hs.to_dict()
        assert isinstance(d["state"], str)
        assert d["state"] == "RUNNING"

    def test_to_dict_healthy_bool(self):
        hs = HealthStatus(healthy=False, state=ComponentState.ERROR)
        d = hs.to_dict()
        assert d["healthy"] is False

    def test_to_dict_with_last_check(self):
        now = datetime.now()
        hs = HealthStatus(healthy=True, state=ComponentState.RUNNING, last_check=now)
        d = hs.to_dict()
        # last_check should be ISO string when present
        assert isinstance(d.get("last_check"), str)

    def test_to_dict_details_propagated(self):
        hs = HealthStatus(
            healthy=False,
            state=ComponentState.DEGRADED,
            details={"disk": "90%"},
        )
        d = hs.to_dict()
        assert d.get("details") == {"disk": "90%"}

    def test_degraded_state_name(self):
        hs = HealthStatus(healthy=True, state=ComponentState.DEGRADED)
        d = hs.to_dict()
        assert d["state"] == "DEGRADED"


# ──────────────────────────────────────────────────────────────────────────────
# toolsets
# ──────────────────────────────────────────────────────────────────────────────
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


class TestNavigCoreTools:
    def test_is_frozenset(self):
        assert isinstance(NAVIG_CORE_TOOLS, frozenset)

    def test_contains_bash_exec(self):
        assert "bash_exec" in NAVIG_CORE_TOOLS

    def test_contains_read_file(self):
        assert "read_file" in NAVIG_CORE_TOOLS

    def test_contains_write_file(self):
        assert "write_file" in NAVIG_CORE_TOOLS

    def test_contains_list_files(self):
        assert "list_files" in NAVIG_CORE_TOOLS


class TestToolsetsRegistry:
    def test_required_keys_present(self):
        for key in ("core", "search", "research", "code", "git", "devops", "memory", "wiki"):
            assert key in TOOLSETS, f"Missing toolset key: {key}"

    def test_full_is_none(self):
        assert TOOLSETS["full"] is None

    def test_core_contains_tools(self):
        assert isinstance(TOOLSETS["core"], list)
        assert len(TOOLSETS["core"]) > 0

    def test_remote_toolset_present(self):
        assert "remote" in TOOLSETS

    def test_lsp_toolset_present(self):
        assert "lsp" in TOOLSETS

    def test_delegation_toolset_present(self):
        assert "delegation" in TOOLSETS


class TestValidateToolset:
    def test_valid_name_does_not_raise(self):
        validate_toolset("core")  # should not raise

    def test_invalid_name_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown toolset"):
            validate_toolset("nonexistent_toolset_xyz")

    def test_full_is_valid(self):
        validate_toolset("full")

    def test_error_message_lists_known(self):
        with pytest.raises(ValueError) as exc_info:
            validate_toolset("bogus")
        assert "core" in str(exc_info.value)


class TestResolveToolsetNames:
    def test_core_returns_list(self):
        result = resolve_toolset_names("core")
        assert isinstance(result, list)

    def test_full_returns_none(self):
        assert resolve_toolset_names("full") is None

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            resolve_toolset_names("does_not_exist")

    def test_search_returns_list(self):
        result = resolve_toolset_names("search")
        assert isinstance(result, list)
        assert len(result) > 0


class TestMergeToolsets:
    def test_single_set(self):
        result = merge_toolsets(["core"])
        assert isinstance(result, list)
        assert len(result) > 0

    def test_full_returns_none(self):
        assert merge_toolsets(["full"]) is None

    def test_full_trumps_other(self):
        assert merge_toolsets(["core", "full"]) is None

    def test_deduplication(self):
        # Merge the same set twice — length should equal single-set length
        single = merge_toolsets(["core"])
        double = merge_toolsets(["core", "core"])
        assert single == double

    def test_merge_two_sets(self):
        combined = merge_toolsets(["core", "search"])
        core = resolve_toolset_names("core") or []
        search = resolve_toolset_names("search") or []
        for t in core:
            assert t in combined
        for t in search:
            assert t in combined

    def test_order_stability(self):
        # Calling twice should produce the same order
        assert merge_toolsets(["code", "git"]) == merge_toolsets(["code", "git"])


class TestParallelSafety:
    def test_read_file_is_safe(self):
        assert is_parallel_safe("read_file") is True

    def test_bash_exec_is_not_safe(self):
        assert is_parallel_safe("bash_exec") is False

    def test_write_file_is_not_safe(self):
        assert is_parallel_safe("write_file") is False

    def test_unknown_tool_is_not_safe(self):
        # An unknown tool is not in PARALLEL_SAFE_TOOLS — safe default is False
        assert is_parallel_safe("totally_unknown_tool_abc123") is False

    def test_safe_set_is_frozenset(self):
        assert isinstance(PARALLEL_SAFE_TOOLS, frozenset)

    def test_never_set_is_frozenset(self):
        assert isinstance(NEVER_PARALLEL_TOOLS, frozenset)

    def test_safe_and_never_are_disjoint(self):
        assert PARALLEL_SAFE_TOOLS.isdisjoint(NEVER_PARALLEL_TOOLS)

    def test_git_log_is_safe(self):
        assert is_parallel_safe("git_log") is True

    def test_git_commit_is_not_safe(self):
        assert is_parallel_safe("git_commit") is False

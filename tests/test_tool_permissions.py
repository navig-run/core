"""
Tests for navig.agent.tool_permissions — ToolPermissionContext.

Covers:
  - blocks() predicate with deny_names, deny_prefixes, allow_only
  - filter_schemas() on OpenAI-format schema lists
  - merge_deny() immutability and additivity
  - intersect_allow() narrowing
  - UNRESTRICTED singleton
  - convenience constructors
"""

from __future__ import annotations

import pytest

from navig.agent.tool_permissions import (
    UNRESTRICTED,
    ToolPermissionContext,
    ToolPermissionDenied,
    allow_only,
    deny_names_ctx,
    deny_prefixes_ctx,
)

# ── Fixtures ─────────────────────────────────────────────────

SAMPLE_SCHEMAS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a file"}},
    {"type": "function", "function": {"name": "write_file", "description": "Write a file"}},
    {"type": "function", "function": {"name": "bash_exec", "description": "Execute bash"}},
    {"type": "function", "function": {"name": "navig_db_query", "description": "DB query"}},
    {"type": "function", "function": {"name": "navig_db_dump", "description": "DB dump"}},
    {"type": "function", "function": {"name": "git_status", "description": "Git status"}},
]


# ── UNRESTRICTED ─────────────────────────────────────────────


def test_unrestricted_blocks_nothing():
    assert not UNRESTRICTED.blocks("read_file")
    assert not UNRESTRICTED.blocks("any_tool")
    assert not UNRESTRICTED.blocks("")


def test_unrestricted_filter_schemas_passthrough():
    result = UNRESTRICTED.filter_schemas(SAMPLE_SCHEMAS)
    assert result == SAMPLE_SCHEMAS


# ── deny_names ───────────────────────────────────────────────


def test_deny_names_blocks_exact():
    ctx = ToolPermissionContext(deny_names=frozenset({"write_file", "bash_exec"}))
    assert ctx.blocks("write_file")
    assert ctx.blocks("bash_exec")
    assert not ctx.blocks("read_file")
    assert not ctx.blocks("git_status")


def test_deny_names_filter_schemas():
    ctx = ToolPermissionContext(deny_names=frozenset({"write_file", "bash_exec"}))
    filtered = ctx.filter_schemas(SAMPLE_SCHEMAS)
    names = [s["function"]["name"] for s in filtered]
    assert "write_file" not in names
    assert "bash_exec" not in names
    assert "read_file" in names
    assert "navig_db_query" in names


# ── deny_prefixes ────────────────────────────────────────────


def test_deny_prefixes_blocks_matching():
    ctx = ToolPermissionContext(deny_prefixes=("navig_db_",))
    assert ctx.blocks("navig_db_query")
    assert ctx.blocks("navig_db_dump")
    assert not ctx.blocks("navig_docker_ps")
    assert not ctx.blocks("read_file")


def test_deny_prefixes_and_names_combined():
    ctx = ToolPermissionContext(
        deny_names=frozenset({"bash_exec"}),
        deny_prefixes=("navig_db_",),
    )
    assert ctx.blocks("bash_exec")
    assert ctx.blocks("navig_db_query")
    assert not ctx.blocks("read_file")


# ── allow_only ───────────────────────────────────────────────


def test_allow_only_permits_listed():
    ctx = ToolPermissionContext(allow_only=frozenset({"read_file", "git_status"}))
    assert not ctx.blocks("read_file")
    assert not ctx.blocks("git_status")
    assert ctx.blocks("write_file")
    assert ctx.blocks("bash_exec")
    assert ctx.blocks("navig_db_query")


def test_allow_only_filter_schemas():
    ctx = ToolPermissionContext(allow_only=frozenset({"read_file", "git_status"}))
    filtered = ctx.filter_schemas(SAMPLE_SCHEMAS)
    names = [s["function"]["name"] for s in filtered]
    assert names == ["read_file", "git_status"]


def test_allow_only_supersedes_deny():
    """allow_only takes precedence — deny_names are effectively ignored."""
    ctx = ToolPermissionContext(
        deny_names=frozenset({"read_file"}),
        allow_only=frozenset({"read_file", "git_status"}),
    )
    # allow_only wins: read_file IS allowed
    assert not ctx.blocks("read_file")
    assert ctx.blocks("write_file")


# ── merge_deny ───────────────────────────────────────────────


def test_merge_deny_creates_new_instance():
    original = ToolPermissionContext(deny_names=frozenset({"bash_exec"}))
    merged = original.merge_deny(frozenset({"write_file"}))

    assert isinstance(merged, ToolPermissionContext)
    assert merged is not original
    # Original unchanged
    assert not original.blocks("write_file")
    # Merged has both
    assert merged.blocks("bash_exec")
    assert merged.blocks("write_file")


def test_merge_deny_preserves_prefixes():
    original = ToolPermissionContext(
        deny_names=frozenset({"bash_exec"}),
        deny_prefixes=("navig_db_",),
    )
    merged = original.merge_deny(frozenset({"write_file"}))
    assert merged.blocks("navig_db_query")
    assert merged.blocks("write_file")
    assert merged.blocks("bash_exec")


# ── intersect_allow ──────────────────────────────────────────


def test_intersect_allow_narrows_from_unrestricted():
    ctx = UNRESTRICTED.intersect_allow(frozenset({"read_file", "git_status"}))
    assert not ctx.blocks("read_file")
    assert not ctx.blocks("git_status")
    assert ctx.blocks("write_file")


def test_intersect_allow_narrows_existing_allow_only():
    parent = ToolPermissionContext(allow_only=frozenset({"read_file", "git_status", "write_file"}))
    child = parent.intersect_allow(frozenset({"read_file", "bash_exec"}))
    # Only read_file is in both sets
    assert not child.blocks("read_file")
    assert child.blocks("git_status")
    assert child.blocks("write_file")
    assert child.blocks("bash_exec")


# ── allowed_names ────────────────────────────────────────────


def test_allowed_names_with_deny():
    ctx = ToolPermissionContext(deny_names=frozenset({"bash_exec", "write_file"}))
    all_tools = ["bash_exec", "git_status", "read_file", "write_file"]
    result = ctx.allowed_names(all_tools)
    assert result == ["git_status", "read_file"]


def test_allowed_names_with_allow_only():
    ctx = ToolPermissionContext(allow_only=frozenset({"read_file"}))
    all_tools = ["bash_exec", "git_status", "read_file", "write_file"]
    result = ctx.allowed_names(all_tools)
    assert result == ["read_file"]


# ── ToolPermissionDenied ─────────────────────────────────────


def test_permission_denied_exception():
    exc = ToolPermissionDenied("bash_exec", "blocked by policy")
    assert exc.tool_name == "bash_exec"
    assert exc.reason == "blocked by policy"
    assert "bash_exec" in str(exc)
    assert "blocked by policy" in str(exc)


def test_permission_denied_default_reason():
    exc = ToolPermissionDenied("write_file")
    assert "not permitted" in str(exc)


# ── Convenience constructors ─────────────────────────────────


def test_allow_only_constructor():
    ctx = allow_only(["read_file", "list_files"])
    assert not ctx.blocks("read_file")
    assert not ctx.blocks("list_files")
    assert ctx.blocks("bash_exec")


def test_deny_names_ctx_constructor():
    ctx = deny_names_ctx(["bash_exec", "write_file"])
    assert ctx.blocks("bash_exec")
    assert ctx.blocks("write_file")
    assert not ctx.blocks("read_file")


def test_deny_prefixes_ctx_constructor():
    ctx = deny_prefixes_ctx("navig_db_", "navig_docker_")
    assert ctx.blocks("navig_db_query")
    assert ctx.blocks("navig_docker_ps")
    assert not ctx.blocks("navig_host_show")


# ── __str__ ──────────────────────────────────────────────────


def test_str_unrestricted():
    assert "unrestricted" in str(UNRESTRICTED)


def test_str_deny_names():
    ctx = ToolPermissionContext(deny_names=frozenset({"a", "b"}))
    s = str(ctx)
    assert "deny_names" in s


def test_str_allow_only():
    ctx = ToolPermissionContext(allow_only=frozenset({"x"}))
    s = str(ctx)
    assert "allow_only" in s

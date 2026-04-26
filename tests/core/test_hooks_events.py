"""Tests for navig.hooks.events — HookEvent, HookContext, HookResult."""
from __future__ import annotations

import json

import pytest

from navig.hooks.events import HookContext, HookEvent, HookResult


class TestHookEvent:
    def test_enum_values(self):
        assert HookEvent.PRE_TOOL_USE == "PreToolUse"
        assert HookEvent.POST_TOOL_USE == "PostToolUse"
        assert HookEvent.PERMISSION_DENIED == "PermissionDenied"
        assert HookEvent.SESSION_START == "SessionStart"
        assert HookEvent.NOTIFICATION == "Notification"


class TestHookContext:
    def test_basic_construction(self):
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="bash")
        assert ctx.event == HookEvent.PRE_TOOL_USE
        assert ctx.tool_name == "bash"

    def test_to_json_contains_event(self):
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE)
        data = json.loads(ctx.to_json())
        assert data["event"] == "PreToolUse"

    def test_to_json_contains_tool_name(self):
        ctx = HookContext(event=HookEvent.POST_TOOL_USE, tool_name="python")
        data = json.loads(ctx.to_json())
        assert data["tool_name"] == "python"

    def test_to_json_tool_result_included_when_set(self):
        ctx = HookContext(event=HookEvent.POST_TOOL_USE, tool_result={"output": "ok"})
        data = json.loads(ctx.to_json())
        assert "tool_result" in data
        assert data["tool_result"] == {"output": "ok"}

    def test_to_json_tool_result_omitted_when_none(self):
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE)
        data = json.loads(ctx.to_json())
        assert "tool_result" not in data

    def test_to_json_tool_error_included_when_set(self):
        ctx = HookContext(event=HookEvent.POST_TOOL_USE_FAILURE, tool_error="timeout")
        data = json.loads(ctx.to_json())
        assert data["tool_error"] == "timeout"

    def test_to_json_metadata_included_when_set(self):
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE, metadata={"risk": "high"})
        data = json.loads(ctx.to_json())
        assert data["metadata"] == {"risk": "high"}

    def test_to_json_metadata_omitted_when_empty(self):
        ctx = HookContext(event=HookEvent.PRE_TOOL_USE)
        data = json.loads(ctx.to_json())
        assert "metadata" not in data

    def test_default_fields_empty(self):
        ctx = HookContext(event=HookEvent.SESSION_START)
        assert ctx.tool_name == ""
        assert ctx.tool_input == {}
        assert ctx.session_id == ""
        assert ctx.turn_id == ""
        assert ctx.metadata == {}


class TestHookResult:
    def test_defaults(self):
        r = HookResult()
        assert r.block is False
        assert r.message == ""
        assert r.executed is False
        assert r.retry is False

    def test_blocked_result(self):
        r = HookResult(block=True, message="forbidden")
        assert r.block is True
        assert r.message == "forbidden"

    def test_executed_true(self):
        r = HookResult(executed=True)
        assert r.executed is True

    def test_retry_true(self):
        r = HookResult(retry=True)
        assert r.retry is True

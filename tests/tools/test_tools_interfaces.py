"""Tests for navig.tools.interfaces — stream events, execution types, specs."""
from __future__ import annotations

import asyncio

import pytest

from navig.tools.interfaces import (
    EndState,
    EventPhase,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    SkillSpec,
    StreamChunk,
    StreamError,
    StreamFinal,
    StreamStatus,
    ToolSpec,
)


class TestEventPhase:
    def test_all_values(self):
        assert EventPhase.STATUS == "status"
        assert EventPhase.CHUNK == "chunk"
        assert EventPhase.FINAL == "final"
        assert EventPhase.ERROR == "error"


class TestStreamStatus:
    def test_phase_is_status(self):
        s = StreamStatus(step="fetching", detail="page 1", progress=50)
        assert s.phase == EventPhase.STATUS

    def test_defaults(self):
        s = StreamStatus(step="init")
        assert s.detail == ""
        assert s.progress == 0

    def test_frozen(self):
        s = StreamStatus(step="x")
        with pytest.raises((AttributeError, TypeError)):
            s.step = "y"  # type: ignore[misc]


class TestStreamChunk:
    def test_phase_is_chunk(self):
        c = StreamChunk(chunk="hello")
        assert c.phase == EventPhase.CHUNK

    def test_content_preserved(self):
        c = StreamChunk(chunk="data")
        assert c.chunk == "data"


class TestStreamFinal:
    def test_phase_is_final(self):
        f = StreamFinal(output={"result": 42})
        assert f.phase == EventPhase.FINAL

    def test_output_any_type(self):
        assert StreamFinal(output=None).output is None
        assert StreamFinal(output=[1, 2]).output == [1, 2]


class TestStreamError:
    def test_phase_is_error(self):
        e = StreamError(message="timeout")
        assert e.phase == EventPhase.ERROR

    def test_default_code(self):
        e = StreamError(message="oops")
        assert e.code == "error"

    def test_custom_code(self):
        e = StreamError(message="oops", code="auth_error")
        assert e.code == "auth_error"


class TestExecutionContext:
    def test_defaults(self):
        ctx = ExecutionContext()
        assert ctx.session_id == ""
        assert ctx.owner_only is False
        assert ctx.env == {}

    def test_custom_values(self):
        ctx = ExecutionContext(session_id="abc", cwd="/tmp", owner_only=True)
        assert ctx.cwd == "/tmp"
        assert ctx.owner_only is True


class TestExecutionRequest:
    def test_defaults(self):
        req = ExecutionRequest(tool_name="run", args={"cmd": "ls"})
        assert req.timeout_s == 120.0
        assert req.lane == "main"
        assert req.is_cancelled is False

    def test_is_cancelled_with_set_event(self):
        evt = asyncio.Event()
        evt.set()
        req = ExecutionRequest(tool_name="run", args={}, cancellation_token=evt)
        assert req.is_cancelled is True

    def test_is_not_cancelled_unset_event(self):
        evt = asyncio.Event()
        req = ExecutionRequest(tool_name="run", args={}, cancellation_token=evt)
        assert req.is_cancelled is False


class TestEndState:
    def test_all_values(self):
        assert EndState.SUCCESS == "success"
        assert EndState.ERROR == "error"
        assert EndState.TIMEOUT == "timeout"
        assert EndState.CANCELLED == "cancelled"


class TestExecutionResult:
    def test_defaults(self):
        r = ExecutionResult(state=EndState.SUCCESS)
        assert r.output is None
        assert r.error is None
        assert r.elapsed_ms == 0.0

    def test_error_state(self):
        r = ExecutionResult(state=EndState.ERROR, error="bad input")
        assert r.state == EndState.ERROR
        assert r.error == "bad input"


class TestToolSpec:
    def test_get_meta_contains_id(self):
        t = ToolSpec(id="my_tool", name="My Tool", description="does stuff")
        meta = t.get_meta()
        assert meta["id"] == "my_tool"
        assert meta["name"] == "My Tool"

    def test_validate_args_returns_true(self):
        t = ToolSpec(id="x")
        assert t.validate_args({"a": 1}) is True

    def test_defaults(self):
        t = ToolSpec(id="t")
        assert t.requires_approval is False
        assert t.domain == "system"


class TestSkillSpec:
    def test_basic_fields(self):
        s = SkillSpec(id="skill1", name="Skill One", description="test")
        assert s.id == "skill1"
        assert s.version == "1.0.0"
        assert s.tools == []

    def test_with_tools(self):
        tool = ToolSpec(id="t1")
        s = SkillSpec(id="s", name="S", description="d", tools=[tool])
        assert len(s.tools) == 1

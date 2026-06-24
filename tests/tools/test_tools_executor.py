"""Tests for navig.tools.executor — ToolExecutor."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from navig.tools.executor import ToolExecutor
from navig.tools.interfaces import (
    ExecutionContext,
    ExecutionRequest,
    StreamError,
    StreamFinal,
    ToolSpec,
)


def _spec(owner_only: bool = False) -> ToolSpec:
    return ToolSpec(id="test_tool", name="Test Tool", owner_only=owner_only)


def _ctx(owner_only: bool = False) -> ExecutionContext:
    return ExecutionContext(owner_only=owner_only)


def _req(args: dict | None = None, *, owner_only: bool = False, timeout: float = 5.0,
         cancel_token: asyncio.Event | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        tool_name="test_tool",
        args=args or {},
        context=_ctx(owner_only=owner_only),
        timeout_s=timeout,
        cancellation_token=cancel_token,
    )


async def _collect(executor: ToolExecutor, request: ExecutionRequest) -> list:
    return [ev async for ev in executor.execute(request)]


class TestCancelledBeforeStart:
    async def test_pre_cancelled_yields_stream_error(self):
        token = asyncio.Event()
        token.set()  # already cancelled
        req = _req(cancel_token=token)
        ex = ToolExecutor(_spec(), lambda: None)
        events = await _collect(ex, req)
        assert len(events) == 1
        assert isinstance(events[0], StreamError)
        assert events[0].code == "cancelled"


class TestValidation:
    async def test_owner_only_violation_yields_validation_error(self):
        req = _req(owner_only=False)  # context is NOT owner_only
        spec = _spec(owner_only=True)  # tool requires owner
        ex = ToolExecutor(spec, lambda: None)
        events = await _collect(ex, req)
        assert isinstance(events[0], StreamError)
        assert events[0].code == "validation_error"

    async def test_invalid_args_yields_validation_error(self):
        req = _req()
        spec = _spec()
        with patch.object(spec, "validate_args", return_value=False):
            ex = ToolExecutor(spec, lambda: None)
            events = await _collect(ex, req)
        assert isinstance(events[0], StreamError)
        assert events[0].code == "validation_error"


class TestSuccessfulExecution:
    async def test_sync_handler_yields_stream_final(self):
        def handler(**kwargs):
            return {"result": 42}

        ex = ToolExecutor(_spec(), handler)
        events = await _collect(ex, _req())
        assert isinstance(events[0], StreamFinal)
        assert events[0].output == {"result": 42}

    async def test_async_handler_yields_stream_final(self):
        async def handler(**kwargs):
            return "async result"

        ex = ToolExecutor(_spec(), handler)
        events = await _collect(ex, _req())
        assert isinstance(events[0], StreamFinal)
        assert events[0].output == "async result"

    async def test_handler_receives_context_if_declared(self):
        received = {}

        async def handler(context=None):
            received["ctx"] = context
            return "ok"

        ctx = _ctx()
        req = ExecutionRequest(tool_name="t", args={}, context=ctx, timeout_s=5.0)
        ex = ToolExecutor(_spec(), handler)
        await _collect(ex, req)
        assert received.get("ctx") is ctx


class TestTimeout:
    async def test_timeout_without_cancel_token_yields_stream_error(self):
        async def slow(**kwargs):
            await asyncio.sleep(10)

        req = _req(timeout=0.01)
        ex = ToolExecutor(_spec(), slow)
        events = await _collect(ex, req)
        assert isinstance(events[0], StreamError)
        assert events[0].code == "timeout"

    async def test_timeout_message_includes_seconds(self):
        async def slow(**kwargs):
            await asyncio.sleep(10)

        req = _req(timeout=2.5)
        ex = ToolExecutor(_spec(), slow)
        events = await _collect(ex, req)
        assert "2.5" in events[0].message


class TestExceptionHandling:
    async def test_handler_exception_yields_execution_error(self):
        def bad_handler(**kwargs):
            raise ValueError("boom")

        ex = ToolExecutor(_spec(), bad_handler)
        events = await _collect(ex, _req())
        assert isinstance(events[0], StreamError)
        assert events[0].code == "execution_error"
        assert "boom" in events[0].message


class TestCancellationToken:
    async def test_cancellation_token_set_during_execution(self):
        token = asyncio.Event()

        async def slow_handler(**kwargs):
            token.set()  # signal cancellation from within
            await asyncio.sleep(5)

        req = _req(cancel_token=token, timeout=5.0)
        ex = ToolExecutor(_spec(), slow_handler)
        events = await _collect(ex, req)
        assert isinstance(events[0], StreamError)
        assert events[0].code == "cancelled"

    async def test_cancel_token_timeout_yields_timeout_error(self):
        token = asyncio.Event()  # never set

        async def slow_handler(**kwargs):
            await asyncio.sleep(10)

        req = _req(cancel_token=token, timeout=0.05)
        ex = ToolExecutor(_spec(), slow_handler)
        events = await _collect(ex, req)
        assert isinstance(events[0], StreamError)
        assert events[0].code == "timeout"

"""Hermetic unit tests for navig.tools.executor."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from navig.tools.executor import ToolExecutor
from navig.tools.interfaces import (
    ExecutionContext,
    ExecutionRequest,
    StreamError,
    StreamFinal,
    ToolSpec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(*, owner_only: bool = False) -> ToolSpec:
    return ToolSpec(
        id="test.tool",
        name="test_tool",
        description="test",
        domain="test",
        parameters={},
        requires_approval=False,
        owner_only=owner_only,
    )


def _make_ctx(*, owner_only: bool = False) -> ExecutionContext:
    return ExecutionContext(owner_only=owner_only)


def _make_request(ctx: ExecutionContext, *, timeout_s: float = 10.0, cancellation_token=None) -> ExecutionRequest:
    return ExecutionRequest(
        tool_name="test.tool",
        args={},
        context=ctx,
        timeout_s=timeout_s,
        cancellation_token=cancellation_token,
    )


async def _collect(gen) -> list:
    return [item async for item in gen]


def collect(gen) -> list:
    return asyncio.run(_collect(gen))


# ---------------------------------------------------------------------------
# Cancellation before start
# ---------------------------------------------------------------------------


class TestCancellationBeforeStart:
    def test_cancelled_request_yields_error(self):
        token = asyncio.Event()
        token.set()
        ctx = _make_ctx()
        req = _make_request(ctx, cancellation_token=token)
        spec = _make_spec()
        executor = ToolExecutor(spec, handler=lambda: "ok")
        events = collect(executor.execute(req))
        errors = [e for e in events if isinstance(e, StreamError)]
        assert errors
        assert errors[0].code == "cancelled"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_owner_only_tool_rejects_non_owner(self):
        """Tool is owner_only but context is not owner_only → StreamError."""
        spec = _make_spec(owner_only=True)
        ctx = _make_ctx(owner_only=False)
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=lambda: "ok")
        events = collect(executor.execute(req))
        errors = [e for e in events if isinstance(e, StreamError)]
        assert errors
        assert errors[0].code == "validation_error"

    def test_owner_only_tool_allows_owner(self):
        """Tool is owner_only and context is owner_only → success."""
        spec = _make_spec(owner_only=True)
        ctx = _make_ctx(owner_only=True)
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=lambda: "owner result")
        events = collect(executor.execute(req))
        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert finals
        assert finals[0].output == "owner result"


# ---------------------------------------------------------------------------
# Successful execution — sync handler
# ---------------------------------------------------------------------------


class TestSyncHandler:
    def test_sync_handler_yields_final(self):
        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=lambda: "hello")
        events = collect(executor.execute(req))
        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert finals
        assert finals[0].output == "hello"

    def test_sync_handler_none_output(self):
        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=lambda: None)
        events = collect(executor.execute(req))
        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert finals

    def test_sync_handler_dict_output(self):
        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=lambda: {"status": "ok"})
        events = collect(executor.execute(req))
        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert finals
        assert finals[0].output == {"status": "ok"}


# ---------------------------------------------------------------------------
# Successful execution — async handler
# ---------------------------------------------------------------------------


class TestAsyncHandler:
    def test_async_handler_yields_final(self):
        async def async_handler():
            return "async result"

        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=async_handler)
        events = collect(executor.execute(req))
        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert finals
        assert finals[0].output == "async result"

    def test_async_handler_with_context_kwarg(self):
        async def handler_with_ctx(context):
            return f"session={context.session_id}"

        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=handler_with_ctx)
        events = collect(executor.execute(req))
        finals = [e for e in events if isinstance(e, StreamFinal)]
        assert finals


# ---------------------------------------------------------------------------
# Handler raising an exception
# ---------------------------------------------------------------------------


class TestHandlerException:
    def test_handler_raising_yields_error(self):
        def bad_handler():
            raise RuntimeError("boom in handler")

        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=bad_handler)
        events = collect(executor.execute(req))
        errors = [e for e in events if isinstance(e, StreamError)]
        assert errors

    def test_async_handler_raising_yields_error(self):
        async def bad_handler():
            raise ValueError("async boom")

        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=bad_handler)
        events = collect(executor.execute(req))
        errors = [e for e in events if isinstance(e, StreamError)]
        assert errors


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_slow_handler_triggers_timeout(self):
        async def slow_handler():
            await asyncio.sleep(5)
            return "never"

        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx, timeout_s=0.05)
        executor = ToolExecutor(spec, handler=slow_handler)
        events = collect(executor.execute(req))
        errors = [e for e in events if isinstance(e, StreamError)]
        assert errors
        assert errors[0].code == "timeout"


# ---------------------------------------------------------------------------
# Last event invariant
# ---------------------------------------------------------------------------


class TestLastEventInvariant:
    def test_last_event_is_final_or_error_sync(self):
        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx)
        executor = ToolExecutor(spec, handler=lambda: "done")
        events = collect(executor.execute(req))
        assert events
        assert isinstance(events[-1], (StreamFinal, StreamError))

    def test_last_event_is_final_or_error_on_cancel(self):
        token = asyncio.Event()
        token.set()
        spec = _make_spec()
        ctx = _make_ctx()
        req = _make_request(ctx, cancellation_token=token)
        executor = ToolExecutor(spec, handler=lambda request: "done")
        events = collect(executor.execute(req))
        assert events
        assert isinstance(events[-1], (StreamFinal, StreamError))

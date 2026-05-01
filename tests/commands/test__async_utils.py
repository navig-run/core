"""Tests for navig/commands/_async_utils.py."""
from __future__ import annotations

import asyncio
import pytest

from navig.commands._async_utils import run_sync


async def _add(a: int, b: int) -> int:
    return a + b


async def _raise_value() -> None:
    raise ValueError("boom")


class TestRunSync:
    def test_runs_simple_coroutine(self):
        result = run_sync(_add(2, 3))
        assert result == 5

    def test_returns_value(self):
        async def _return_str() -> str:
            return "hello"

        assert run_sync(_return_str()) == "hello"

    def test_propagates_exception(self):
        with pytest.raises(ValueError, match="boom"):
            run_sync(_raise_value())

    def test_runs_none_returning_coroutine(self):
        async def _noop() -> None:
            pass

        result = run_sync(_noop())
        assert result is None

    def test_runs_with_await_inside(self):
        async def _two_awaits() -> int:
            a = await _add(1, 2)
            b = await _add(a, 4)
            return b

        assert run_sync(_two_awaits()) == 7

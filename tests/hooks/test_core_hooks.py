"""
Batch 73: hermetic unit tests for navig/core/hooks.py
Covers: HookEvent, HookRegistry, register_hook, unregister_hook,
        trigger_hook (async), trigger_hook_sync, create_hook_event,
        hook_stats, list_hook_types
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixture: fresh registry per test (avoid global state leak)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_hooks():
    from navig.core.hooks import clear_hooks
    clear_hooks()
    yield
    clear_hooks()


# ---------------------------------------------------------------------------
# HookEvent
# ---------------------------------------------------------------------------

class TestHookEvent:
    def test_event_key_property(self) -> None:
        from navig.core.hooks import HookEvent
        e = HookEvent(type="command", action="before_execute")
        assert e.event_key == "command:before_execute"

    def test_repr_includes_event_key(self) -> None:
        from navig.core.hooks import HookEvent
        e = HookEvent(type="session", action="start")
        assert "session:start" in repr(e)

    def test_defaults(self) -> None:
        from navig.core.hooks import HookEvent
        e = HookEvent(type="t", action="a")
        assert e.cancel is False
        assert e.messages == []
        assert e.data == {}
        assert e.context == {}

    def test_cancel_flag(self) -> None:
        from navig.core.hooks import HookEvent
        e = HookEvent(type="t", action="a")
        e.cancel = True
        assert e.cancel is True

    def test_messages_mutable(self) -> None:
        from navig.core.hooks import HookEvent
        e = HookEvent(type="t", action="a")
        e.messages.append("hello")
        assert e.messages == ["hello"]


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------

class TestHookRegistry:
    def _reg(self):
        from navig.core.hooks import HookRegistry
        return HookRegistry()

    def test_register_and_get_handlers(self) -> None:
        reg = self._reg()
        h = MagicMock()
        reg.register("cmd:start", h)
        assert reg.get_handlers("cmd:start") == [h]

    def test_priority_ordering(self) -> None:
        reg = self._reg()
        first = MagicMock()
        second = MagicMock()
        reg.register("evt", second, priority=200)
        reg.register("evt", first, priority=50)
        assert reg.get_handlers("evt") == [first, second]

    def test_unregister_removes_handler(self) -> None:
        reg = self._reg()
        h = MagicMock()
        reg.register("evt", h)
        removed = reg.unregister("evt", h)
        assert removed is True
        assert reg.get_handlers("evt") == []

    def test_unregister_nonexistent_returns_false(self) -> None:
        reg = self._reg()
        assert reg.unregister("missing", MagicMock()) is False

    def test_clear_specific_key(self) -> None:
        reg = self._reg()
        h = MagicMock()
        reg.register("evt", h)
        reg.clear("evt")
        assert reg.get_handlers("evt") == []

    def test_clear_all(self) -> None:
        reg = self._reg()
        reg.register("a", MagicMock())
        reg.register("b", MagicMock())
        reg.clear()
        assert reg.get_event_keys() == []

    def test_disable_suppresses_handlers(self) -> None:
        reg = self._reg()
        h = MagicMock()
        reg.register("evt", h)
        reg.disable("evt")
        assert reg.get_handlers("evt") == []

    def test_enable_restores_handlers(self) -> None:
        reg = self._reg()
        h = MagicMock()
        reg.register("evt", h)
        reg.disable("evt")
        reg.enable("evt")
        assert reg.get_handlers("evt") == [h]

    def test_handler_count_total(self) -> None:
        reg = self._reg()
        reg.register("a", MagicMock())
        reg.register("a", MagicMock())
        reg.register("b", MagicMock())
        assert reg.handler_count() == 3

    def test_handler_count_per_key(self) -> None:
        reg = self._reg()
        reg.register("a", MagicMock())
        reg.register("a", MagicMock())
        assert reg.handler_count("a") == 2
        assert reg.handler_count("b") == 0

    def test_get_event_keys(self) -> None:
        reg = self._reg()
        reg.register("x", MagicMock())
        reg.register("y", MagicMock())
        keys = reg.get_event_keys()
        assert "x" in keys and "y" in keys


# ---------------------------------------------------------------------------
# Module-level register_hook / unregister_hook
# ---------------------------------------------------------------------------

class TestRegisterHook:
    def test_direct_call(self) -> None:
        from navig.core.hooks import register_hook, clear_hooks, _registry
        h = MagicMock()
        register_hook("test:evt", h)
        assert h in _registry.get_handlers("test:evt")

    def test_decorator_usage(self) -> None:
        from navig.core.hooks import register_hook, _registry

        @register_hook("test:decorated")
        def handler(event):
            pass

        assert handler in _registry.get_handlers("test:decorated")

    def test_unregister_hook(self) -> None:
        from navig.core.hooks import register_hook, unregister_hook, _registry
        h = MagicMock()
        register_hook("test:unreg", h)
        result = unregister_hook("test:unreg", h)
        assert result is True
        assert h not in _registry.get_handlers("test:unreg")


# ---------------------------------------------------------------------------
# trigger_hook (async)
# ---------------------------------------------------------------------------

class TestTriggerHook:
    @pytest.mark.asyncio
    async def test_sync_handler_called(self) -> None:
        from navig.core.hooks import register_hook, trigger_hook
        calls = []
        def h(event):
            calls.append(event.event_key)
        register_hook("test:run", h)
        event = await trigger_hook("test", "run")
        assert event.event_key == "test:run"
        assert "test:run" in calls

    @pytest.mark.asyncio
    async def test_async_handler_called(self) -> None:
        from navig.core.hooks import register_hook, trigger_hook
        calls = []
        async def h(event):
            calls.append("async_called")
        register_hook("test:async", h)
        await trigger_hook("test", "async")
        assert "async_called" in calls

    @pytest.mark.asyncio
    async def test_handler_error_isolated(self) -> None:
        from navig.core.hooks import register_hook, trigger_hook
        def bad(event):
            raise RuntimeError("boom")
        good_calls = []
        def good(event):
            good_calls.append(True)
        register_hook("test:err", bad, priority=50)
        register_hook("test:err", good, priority=150)
        event = await trigger_hook("test", "err")
        # good handler still ran after bad one
        assert good_calls == [True]

    @pytest.mark.asyncio
    async def test_returns_hook_event(self) -> None:
        from navig.core.hooks import trigger_hook, HookEvent
        event = await trigger_hook("x", "y")
        assert isinstance(event, HookEvent)

    @pytest.mark.asyncio
    async def test_handler_can_set_cancel(self) -> None:
        from navig.core.hooks import register_hook, trigger_hook
        def h(event):
            event.cancel = True
        register_hook("test:cancel", h)
        event = await trigger_hook("test", "cancel")
        assert event.cancel is True


# ---------------------------------------------------------------------------
# trigger_hook_sync
# ---------------------------------------------------------------------------

class TestTriggerHookSync:
    def test_sync_handler_called(self) -> None:
        from navig.core.hooks import register_hook, trigger_hook_sync
        calls = []
        def h(event):
            calls.append(True)
        register_hook("sync:evt", h)
        trigger_hook_sync("sync", "evt")
        assert calls == [True]

    def test_async_handler_skipped_with_warning(self) -> None:
        from navig.core.hooks import register_hook, trigger_hook_sync
        import logging
        async def h(event):
            pass
        register_hook("sync:async_skip", h)
        # Should not raise
        trigger_hook_sync("sync", "async_skip")


# ---------------------------------------------------------------------------
# create_hook_event / hook_stats / list_hook_types
# ---------------------------------------------------------------------------

class TestUtilities:
    def test_create_hook_event(self) -> None:
        from navig.core.hooks import create_hook_event, HookEvent
        e = create_hook_event("memory", "search", context={"q": "test"})
        assert isinstance(e, HookEvent)
        assert e.event_key == "memory:search"
        assert e.context == {"q": "test"}

    def test_hook_stats_counts(self) -> None:
        from navig.core.hooks import register_hook, hook_stats
        h1 = MagicMock()
        h2 = MagicMock()
        register_hook("stats:test", h1)
        register_hook("stats:test", h2)
        stats = hook_stats()
        assert stats["total_handlers"] >= 2

    def test_list_hook_types(self) -> None:
        from navig.core.hooks import list_hook_types
        types = list_hook_types()
        assert "command:before_execute" in types
        assert "session:start" in types
        assert isinstance(types["command:before_execute"], str)

"""tests/test_hooks.py — Unit tests for navig.tools.hooks."""

from __future__ import annotations

import pytest

from navig.tools.hooks import (
    HookRegistry,
    ToolEvent,
    ToolExecutionEvent,
    get_hook_registry,
    reset_hook_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_registry():
    """Each test gets a fresh HookRegistry singleton."""
    reset_hook_registry()
    yield
    reset_hook_registry()


# ---------------------------------------------------------------------------
# HookRegistry.register / .on decorator
# ---------------------------------------------------------------------------


def test_register_and_fire():
    reg = HookRegistry()
    log = []
    reg.register(ToolEvent.AFTER_EXECUTE, lambda ev: log.append(ev.tool))
    reg.fire(ToolEvent.AFTER_EXECUTE, tool="web_search", status="success")
    assert log == ["web_search"]


def test_on_decorator():
    reg = HookRegistry()
    calls = []

    @reg.on(ToolEvent.ERROR)
    def my_hook(ev: ToolExecutionEvent):
        calls.append((ev.tool, ev.error))

    reg.fire(ToolEvent.ERROR, tool="bash_exec", error="Boom")
    assert calls == [("bash_exec", "Boom")]


def test_multiple_hooks_same_event():
    reg = HookRegistry()
    order = []
    reg.register(ToolEvent.BEFORE_EXECUTE, lambda ev: order.append("a"))
    reg.register(ToolEvent.BEFORE_EXECUTE, lambda ev: order.append("b"))
    reg.fire(ToolEvent.BEFORE_EXECUTE, tool="t")
    assert order == ["a", "b"]


def test_fire_different_event_no_cross_trigger():
    reg = HookRegistry()
    fired = []
    reg.register(ToolEvent.DENIED, lambda ev: fired.append("denied"))
    reg.fire(ToolEvent.ERROR, tool="x")
    assert fired == []


# ---------------------------------------------------------------------------
# ToolExecutionEvent payload construction
# ---------------------------------------------------------------------------


def test_event_payload_known_fields():
    reg = HookRegistry()
    captured: list[ToolExecutionEvent] = []
    reg.register(ToolEvent.AFTER_EXECUTE, captured.append)
    reg.fire(
        ToolEvent.AFTER_EXECUTE,
        tool="my_tool",
        parameters={"q": "hello"},
        status="success",
        output={"result": 42},
        elapsed_ms=12.5,
    )
    ev = captured[0]
    assert ev.tool == "my_tool"
    assert ev.parameters == {"q": "hello"}
    assert ev.output == {"result": 42}
    assert ev.elapsed_ms == 12.5


def test_event_payload_unknown_fields_go_to_metadata():
    reg = HookRegistry()
    captured: list[ToolExecutionEvent] = []
    reg.register(ToolEvent.BEFORE_EXECUTE, captured.append)
    reg.fire(ToolEvent.BEFORE_EXECUTE, tool="t", custom_key="custom_val")
    ev = captured[0]
    assert ev.metadata["custom_key"] == "custom_val"


def test_event_payload_metadata_merged():
    reg = HookRegistry()
    captured: list[ToolExecutionEvent] = []
    reg.register(ToolEvent.ERROR, captured.append)
    reg.fire(ToolEvent.ERROR, tool="t", metadata={"existing": 1}, extra_field="x")
    ev = captured[0]
    assert ev.metadata["existing"] == 1
    assert ev.metadata["extra_field"] == "x"


# ---------------------------------------------------------------------------
# Error isolation — failing callbacks must not propagate
# ---------------------------------------------------------------------------


def test_failing_hook_does_not_propagate():
    reg = HookRegistry()
    good = []

    def bad_hook(ev):
        raise RuntimeError("Hook intentionally broken")

    reg.register(ToolEvent.NOT_FOUND, bad_hook)
    reg.register(ToolEvent.NOT_FOUND, lambda ev: good.append(True))

    # Must not raise; good hook still fires after bad one
    reg.fire(ToolEvent.NOT_FOUND, tool="unknown")
    assert good == [True]


# ---------------------------------------------------------------------------
# Unregister
# ---------------------------------------------------------------------------


def test_unregister_returns_true_when_present():
    reg = HookRegistry()
    cb = lambda ev: None
    reg.register(ToolEvent.DENIED, cb)
    assert reg.unregister(ToolEvent.DENIED, cb) is True


def test_unregister_returns_false_when_absent():
    reg = HookRegistry()
    assert reg.unregister(ToolEvent.ERROR, lambda ev: None) is False


def test_unregister_stops_firing():
    reg = HookRegistry()
    calls = []
    cb = lambda ev: calls.append(1)
    reg.register(ToolEvent.AFTER_EXECUTE, cb)
    reg.fire(ToolEvent.AFTER_EXECUTE, tool="t")
    reg.unregister(ToolEvent.AFTER_EXECUTE, cb)
    reg.fire(ToolEvent.AFTER_EXECUTE, tool="t")
    assert calls == [1]  # fired once before unregister, silent after


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


def test_clear_all():
    reg = HookRegistry()
    calls = []
    reg.register(ToolEvent.AFTER_EXECUTE, lambda ev: calls.append("a"))
    reg.register(ToolEvent.ERROR, lambda ev: calls.append("e"))
    reg.clear()
    reg.fire(ToolEvent.AFTER_EXECUTE, tool="t")
    reg.fire(ToolEvent.ERROR, tool="t")
    assert calls == []


def test_clear_single_event():
    reg = HookRegistry()
    calls = []
    reg.register(ToolEvent.AFTER_EXECUTE, lambda ev: calls.append("a"))
    reg.register(ToolEvent.ERROR, lambda ev: calls.append("e"))
    reg.clear(ToolEvent.AFTER_EXECUTE)
    reg.fire(ToolEvent.AFTER_EXECUTE, tool="t")
    reg.fire(ToolEvent.ERROR, tool="t")
    assert calls == ["e"]


# ---------------------------------------------------------------------------
# hook_count
# ---------------------------------------------------------------------------


def test_hook_count_all():
    reg = HookRegistry()
    reg.register(ToolEvent.BEFORE_EXECUTE, lambda ev: None)
    reg.register(ToolEvent.AFTER_EXECUTE, lambda ev: None)
    assert reg.hook_count() == 2


def test_hook_count_per_event():
    reg = HookRegistry()
    reg.register(ToolEvent.ERROR, lambda ev: None)
    reg.register(ToolEvent.ERROR, lambda ev: None)
    assert reg.hook_count(ToolEvent.ERROR) == 2
    assert reg.hook_count(ToolEvent.DENIED) == 0


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


def test_get_hook_registry_singleton():
    a = get_hook_registry()
    b = get_hook_registry()
    assert a is b


def test_reset_hook_registry_gives_fresh_instance():
    a = get_hook_registry()
    reset_hook_registry()
    b = get_hook_registry()
    assert a is not b


# ---------------------------------------------------------------------------
# ToolRouter integration — hooks fire during execution
# ---------------------------------------------------------------------------


def test_hooks_fire_on_router_execute():
    """ToolRouter fires BEFORE_EXECUTE and AFTER_EXECUTE for known tools."""
    from navig.tools.router import ToolCallAction, get_tool_router, reset_globals

    reset_globals()
    reset_hook_registry()
    reg = get_hook_registry()
    events: list[tuple[ToolEvent, str]] = []

    @reg.on(ToolEvent.BEFORE_EXECUTE)
    def before(ev: ToolExecutionEvent):
        events.append((ToolEvent.BEFORE_EXECUTE, ev.tool))

    @reg.on(ToolEvent.AFTER_EXECUTE)
    def after(ev: ToolExecutionEvent):
        events.append((ToolEvent.AFTER_EXECUTE, ev.tool))

    router = get_tool_router()
    router.execute(ToolCallAction(tool="system_info"))

    assert (ToolEvent.BEFORE_EXECUTE, "system_info") in events
    assert (ToolEvent.AFTER_EXECUTE, "system_info") in events


def test_hooks_fire_not_found():
    from navig.tools.router import ToolCallAction, get_tool_router, reset_globals

    reset_globals()
    reset_hook_registry()
    reg = get_hook_registry()
    nf = []

    reg.register(ToolEvent.NOT_FOUND, lambda ev: nf.append(ev.tool))
    router = get_tool_router()
    router.execute(ToolCallAction(tool="nonexistent_tool_xyz"))
    assert "nonexistent_tool_xyz" in nf


@pytest.mark.asyncio
async def test_hooks_fire_on_async_execute():
    """async_execute path also fires hooks."""
    from navig.tools.router import ToolCallAction, get_tool_router, reset_globals

    reset_globals()
    reset_hook_registry()
    reg = get_hook_registry()
    events: list[ToolEvent] = []
    reg.register(
        ToolEvent.BEFORE_EXECUTE, lambda ev: events.append(ToolEvent.BEFORE_EXECUTE)
    )
    reg.register(
        ToolEvent.AFTER_EXECUTE, lambda ev: events.append(ToolEvent.AFTER_EXECUTE)
    )

    router = get_tool_router()
    await router.async_execute(ToolCallAction(tool="system_info"))

    assert ToolEvent.BEFORE_EXECUTE in events
    assert ToolEvent.AFTER_EXECUTE in events

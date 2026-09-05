"""An async tool handler must never be reported as a successful tool call.

`ToolRouter.execute()` invoked the handler and wrapped whatever came back as
`output`. An `async def` handler returns a **coroutine** — work that has not
happened yet — so the router reported:

    status : ToolResultStatus.SUCCESS
    output : <coroutine object bash_exec_async_handler at 0x...>

for a shell command that never ran. `bash_exec` is async and `llm/generate.py`
`_maybe_execute_tools` uses the sync path, so the model was handed a formatted
"success" containing a coroutine repr.

The async path had the mirror defect: `async_execute` awaited `result.output`
*after* `execute()`'s try/except had already returned, so an exception raised
while the handler ran escaped the router raw instead of becoming
`ToolResult(status=ERROR)` — the one thing every caller is written against.
"""
from __future__ import annotations

import asyncio

import pytest

from navig.tools.router import (
    SafetyLevel,
    ToolDomain,
    ToolMeta,
    ToolResultStatus,
    ToolRouter,
    ToolStatus,
    get_tool_registry,
)
from navig.tools.schemas import ToolCallAction


@pytest.fixture
def registry_with(monkeypatch):
    """Register a probe tool on the real registry and remove it afterwards."""
    registered: list[str] = []

    def _register(name: str, handler, safety: SafetyLevel = SafetyLevel.SAFE):
        reg = get_tool_registry()
        reg.register(
            ToolMeta(
                name=name,
                domain=ToolDomain.SYSTEM,
                description="probe tool",
                safety=safety,
            ),
            handler=handler,
        )
        registered.append(name)
        return reg

    yield _register

    reg = get_tool_registry()
    for name in registered:
        reg._tools.pop(name, None)
        reg._handlers.pop(name, None)


def test_an_async_handler_actually_runs_on_the_sync_path(registry_with) -> None:
    ran: list[str] = []

    async def handler(**kwargs):
        await asyncio.sleep(0)
        ran.append("yes")
        return {"echo": kwargs.get("value")}

    registry_with("probe_async_ok", handler)
    result = ToolRouter().execute(ToolCallAction(tool="probe_async_ok", parameters={"value": 7}))

    # The behavioural assertion first: the work happened.
    assert ran == ["yes"], "async handler was never awaited — the tool did not run"
    assert result.output == {"echo": 7}
    assert result.status is ToolResultStatus.SUCCESS
    assert not asyncio.iscoroutine(result.output)


def test_a_failing_async_handler_is_an_error_not_a_success(registry_with) -> None:
    async def handler(**kwargs):
        raise RuntimeError("handler blew up mid-await")

    registry_with("probe_async_boom", handler)
    result = ToolRouter().execute(ToolCallAction(tool="probe_async_boom", parameters={}))

    assert result.status is ToolResultStatus.ERROR
    assert "handler blew up mid-await" in (result.error or "")


async def test_async_execute_returns_an_error_result_instead_of_raising(registry_with) -> None:
    async def handler(**kwargs):
        raise RuntimeError("handler blew up mid-await")

    registry_with("probe_async_boom2", handler)

    # Must NOT raise: every caller (agent/conv/executor.py) branches on
    # result.status and turns a non-SUCCESS into its own error message.
    result = await ToolRouter().async_execute(
        ToolCallAction(tool="probe_async_boom2", parameters={})
    )

    assert result.status is ToolResultStatus.ERROR
    assert "handler blew up mid-await" in (result.error or "")


async def test_async_execute_still_returns_the_awaited_value(registry_with) -> None:
    """Anti-vacuity partner: the success path still works end to end."""

    async def handler(**kwargs):
        return "awaited-value"

    registry_with("probe_async_ok2", handler)
    result = await ToolRouter().async_execute(
        ToolCallAction(tool="probe_async_ok2", parameters={})
    )

    assert result.status is ToolResultStatus.SUCCESS
    assert result.output == "awaited-value"


async def test_the_sync_path_inside_a_running_loop_refuses_instead_of_lying(
    registry_with,
) -> None:
    """`execute()` cannot block a loop that is already running in this thread.
    The honest answer is an ERROR that names the fix — never SUCCESS over a
    coroutine that will be garbage-collected un-awaited."""
    ran: list[str] = []

    async def handler(**kwargs):
        ran.append("yes")
        return "should not happen"

    registry_with("probe_async_inloop", handler)
    result = ToolRouter().execute(ToolCallAction(tool="probe_async_inloop", parameters={}))

    assert result.status is ToolResultStatus.ERROR
    assert "async_execute" in (result.error or ""), (
        f"the error must point at the working call, got: {result.error!r}"
    )
    assert ran == [], "handler must not have run"


def test_a_sync_handler_is_unaffected(registry_with) -> None:
    """The common case must not have changed shape."""

    def handler(**kwargs):
        return {"plain": True}

    registry_with("probe_sync", handler)
    result = ToolRouter().execute(ToolCallAction(tool="probe_sync", parameters={}))

    assert result.status is ToolResultStatus.SUCCESS
    assert result.output == {"plain": True}
    assert result.latency_ms is not None


def test_guard_results_still_short_circuit_before_the_handler(registry_with) -> None:
    """`_prepare` carries every guard; a blocked tool must never be invoked."""
    ran: list[str] = []

    async def handler(**kwargs):
        ran.append("yes")

    registry_with("probe_blocked", handler)
    router = ToolRouter(safety_policy={"blocked_tools": ["probe_blocked"]})
    result = router.execute(ToolCallAction(tool="probe_blocked", parameters={}))

    assert result.status is ToolResultStatus.DENIED
    assert ran == []


def test_no_mcp_tool_handler_is_async_while_its_dispatcher_is_sync() -> None:
    """The second dispatcher with the same shape — kept honest, not fixed.

    `mcp_server.MCPProtocolHandler._execute_tool` is synchronous and does
    `return handler(self, arguments)`. An `async def` MCP tool would hand the
    client a coroutine as its result, exactly as `bash_exec` did through the
    ToolRouter. Measured when this landed: 122 registered tools, 0 async — so
    the surface is clean today and this pins it there. If a tool genuinely needs
    to be async, `_execute_tool` has to learn to drive it first (see
    `_drive_awaitable_to_completion` in navig/tools/router.py); making the
    handler async alone would ship the phantom.
    """
    import inspect
    from unittest.mock import MagicMock

    from navig.mcp.tools import register_all_tools

    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}
    del server._tool_safety
    register_all_tools(server)

    handlers = server._tool_handlers
    assert handlers, "no MCP tools registered; this guard is inert"

    async_tools = sorted(n for n, fn in handlers.items() if inspect.iscoroutinefunction(fn))
    assert not async_tools, (
        "these MCP tool handlers are async but `_execute_tool` is synchronous, so the "
        f"client would receive a coroutine object as the tool result: {async_tools}"
    )


def test_code_sandbox_advertises_itself_honestly() -> None:
    """It declared `handler_name="execute"`, which navig.tools.sandbox has never
    defined, so the LLM was offered a tool that could only answer "No handler
    loaded". Until an adapter exists it must report UNAVAILABLE."""
    reg = get_tool_registry()
    reg.initialize()

    meta = reg.get_tool("code_sandbox")
    assert meta is not None
    assert meta.status is ToolStatus.UNAVAILABLE
    assert meta.status_message, "an unavailable tool must say why"
    assert not meta.is_available()

    available = {m.name for m in reg.list_tools(available_only=True)}
    assert "code_sandbox" not in available

    result = ToolRouter().execute(ToolCallAction(tool="code_sandbox", parameters={"code": "1"}))
    assert result.status is ToolResultStatus.ERROR
    assert "unavailable" in (result.error or "").lower()

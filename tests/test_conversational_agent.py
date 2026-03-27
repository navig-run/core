"""Tests for the StatusEvent system, ConversationalAgent refactor, and ConsoleStatusRenderer."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.agent.conv.agent import ConversationalAgent
from navig.agent.conv.console_renderer import ConsoleStatusRenderer
from navig.agent.conv.executor import ExecutionStep, Task, TaskExecutor
from navig.agent.conv.status_event import StatusEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(**kwargs) -> ConversationalAgent:
    """Build a ConversationalAgent with minimal real dependencies."""
    history_stub = MagicMock()
    history_stub.get_messages.return_value = []
    history_stub.messages.return_value = []
    history_stub.add = MagicMock()
    history_stub.append = MagicMock()
    history_stub.clear_cjk_for_lang = MagicMock()
    return ConversationalAgent(soul_content="test soul", history=history_stub, **kwargs)


def _make_executor(events: list[StatusEvent]) -> TaskExecutor:
    async def cb(e: StatusEvent) -> None:
        events.append(e)

    return TaskExecutor(on_status_update=cb, max_attempts=1)


def _make_task(*descriptions: str) -> Task:
    steps = [ExecutionStep(action="wait", description=d) for d in descriptions]
    return Task(id="test01", goal="Test goal", plan=steps)


# ---------------------------------------------------------------------------
# 1. StatusEvent dataclass
# ---------------------------------------------------------------------------


def test_status_event_required_fields() -> None:
    ts = datetime.now()
    e = StatusEvent(type="thinking", task_id="abc", message="hello", timestamp=ts)
    assert e.type == "thinking"
    assert e.task_id == "abc"
    assert e.message == "hello"
    assert e.timestamp is ts


def test_status_event_optional_defaults() -> None:
    e = StatusEvent(
        type="task_start", task_id="x", message="m", timestamp=datetime.now()
    )
    assert e.step_index is None
    assert e.total_steps is None
    assert e.metadata == {}


def test_status_event_metadata_instances_are_independent() -> None:
    e1 = StatusEvent(
        type="task_start", task_id="a", message="m", timestamp=datetime.now()
    )
    e2 = StatusEvent(
        type="task_done", task_id="b", message="n", timestamp=datetime.now()
    )
    e1.metadata["key"] = "value"
    assert "key" not in e2.metadata


def test_status_event_with_all_fields() -> None:
    ts = datetime.now()
    e = StatusEvent(
        type="step_failed",
        task_id="t1",
        message="step blew up",
        timestamp=ts,
        step_index=2,
        total_steps=5,
        metadata={"error": "oops"},
    )
    assert e.step_index == 2
    assert e.total_steps == 5
    assert e.metadata["error"] == "oops"


# ---------------------------------------------------------------------------
# 2 & 3. _emit_event on ConversationalAgent
# ---------------------------------------------------------------------------


async def test_emit_event_sync_callback() -> None:
    received: list[StatusEvent] = []

    def cb(event: StatusEvent) -> None:
        received.append(event)

    agent = _make_agent(on_status_update=cb)
    event = StatusEvent(
        type="thinking", task_id="1", message="hi", timestamp=datetime.now()
    )
    await agent._emit_event(event)
    assert received == [event]


async def test_emit_event_async_callback() -> None:
    received: list[StatusEvent] = []

    async def cb(event: StatusEvent) -> None:
        received.append(event)

    agent = _make_agent(on_status_update=cb)
    event = StatusEvent(
        type="thinking", task_id="1", message="hi", timestamp=datetime.now()
    )
    await agent._emit_event(event)
    assert received == [event]


async def test_emit_event_none_callback_is_noop() -> None:
    agent = _make_agent()  # no callback
    # Must not raise
    await agent._emit_event(
        StatusEvent(
            type="task_start", task_id="1", message="m", timestamp=datetime.now()
        )
    )


async def test_emit_event_swallows_callback_exception() -> None:
    def bad_cb(event: StatusEvent) -> None:
        raise RuntimeError("boom")

    agent = _make_agent(on_status_update=bad_cb)
    # Must not propagate
    await agent._emit_event(
        StatusEvent(type="thinking", task_id="1", message="m", timestamp=datetime.now())
    )


# ---------------------------------------------------------------------------
# 5. Backward-compatibility shim
# ---------------------------------------------------------------------------


async def test_backward_compat_sync_str_callback() -> None:
    """Legacy callback with ``str`` annotation gets event.message."""
    messages: list[str] = []

    def legacy(msg: str) -> None:
        messages.append(msg)

    agent = _make_agent(on_status_update=legacy)
    event = StatusEvent(
        type="step_done", task_id="1", message="Step complete", timestamp=datetime.now()
    )
    await agent._emit_event(event)
    assert messages == ["Step complete"]


async def test_backward_compat_async_str_callback() -> None:
    """Legacy async callback with ``str`` annotation is wrapped into a coroutine."""
    messages: list[str] = []

    async def legacy(msg: str) -> None:
        messages.append(msg)

    agent = _make_agent(on_status_update=legacy)
    event = StatusEvent(
        type="step_done", task_id="1", message="Async done", timestamp=datetime.now()
    )
    await agent._emit_event(event)
    assert messages == ["Async done"]


async def test_backward_compat_no_annotation_single_param() -> None:
    """Callback with no annotation and single param is treated as legacy."""
    messages: list[str] = []

    async def send_status(msg) -> None:  # channel_router pattern — no annotation
        messages.append(msg)

    agent = _make_agent(on_status_update=send_status)
    event = StatusEvent(
        type="task_done", task_id="1", message="Done!", timestamp=datetime.now()
    )
    await agent._emit_event(event)
    assert messages == ["Done!"]


async def test_post_init_callback_assignment_is_shimmed() -> None:
    """on_status_update assigned after __init__ (channel_router pattern) still gets shim."""
    messages: list[str] = []

    async def send_status(msg) -> None:
        messages.append(msg)

    agent = _make_agent()
    agent.on_status_update = send_status  # post-init, like channel_router does it

    event = StatusEvent(
        type="task_done", task_id="1", message="Post-init", timestamp=datetime.now()
    )
    await agent._emit_event(event)
    assert messages == ["Post-init"]


async def test_on_status_update_syncs_to_executor() -> None:
    """Setting on_status_update after init propagates to executor._notify_cb."""

    async def new_cb(event: StatusEvent) -> None:
        pass

    agent = _make_agent()
    agent.on_status_update = new_cb
    assert agent._executor._notify_cb is agent._on_status_update


# ---------------------------------------------------------------------------
# 4. Lifecycle emissions from TaskExecutor
# ---------------------------------------------------------------------------


async def test_lifecycle_task_start_emitted_by_execute_plan() -> None:
    events: list[StatusEvent] = []
    executor = _make_executor(events)
    with (
        patch.object(executor, "_execute_step", new=AsyncMock(return_value="ok")),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute_plan(
            {
                "understanding": "Do something",
                "plan": [{"action": "wait", "description": "Wait"}],
                "message": "Working",
                "confirmation_needed": False,
            }
        )
    types = [e.type for e in events]
    assert "task_start" in types
    task_start_evt = next(e for e in events if e.type == "task_start")
    assert task_start_evt.task_id != ""
    assert task_start_evt.message == "Do something"


async def test_lifecycle_step_start_and_step_done() -> None:
    events: list[StatusEvent] = []
    executor = _make_executor(events)
    task = _make_task("My step")
    with (
        patch.object(executor, "_execute_step", new=AsyncMock(return_value="done")),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    types = [e.type for e in events]
    assert "step_start" in types
    assert "step_done" in types

    step_start = next(e for e in events if e.type == "step_start")
    assert step_start.step_index == 0
    assert step_start.total_steps == 1
    assert step_start.message == "My step"

    step_done = next(e for e in events if e.type == "step_done")
    assert step_done.step_index == 0
    assert step_done.message == "My step"


async def test_lifecycle_step_failed_with_error_metadata() -> None:
    events: list[StatusEvent] = []
    executor = _make_executor(events)
    task = _make_task("Failing step")
    with (
        patch.object(
            executor, "_execute_step", new=AsyncMock(side_effect=RuntimeError("boom"))
        ),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    failed = [e for e in events if e.type == "step_failed"]
    assert len(failed) >= 1
    assert failed[0].metadata["error"] == "boom"
    assert failed[0].message == "Failing step"


async def test_lifecycle_task_done_emitted() -> None:
    events: list[StatusEvent] = []
    executor = _make_executor(events)
    task = _make_task("A step")
    with (
        patch.object(executor, "_execute_step", new=AsyncMock(return_value="ok")),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    types = [e.type for e in events]
    assert "task_done" in types
    task_done = next(e for e in events if e.type == "task_done")
    assert task_done.task_id == "test01"


async def test_lifecycle_step_done_not_emitted_on_failure() -> None:
    """When a step fails, step_done must not be emitted for that step."""
    events: list[StatusEvent] = []
    executor = _make_executor(events)
    task = _make_task("Bad step")
    with (
        patch.object(
            executor, "_execute_step", new=AsyncMock(side_effect=RuntimeError("kaboom"))
        ),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    step_done_events = [e for e in events if e.type == "step_done"]
    assert len(step_done_events) == 0


async def test_lifecycle_ordering() -> None:
    """task_start → step_start → step_done → task_done ordering is respected."""
    events: list[StatusEvent] = []
    executor = _make_executor(events)
    task = _make_task("Step one")
    with (
        patch.object(executor, "_execute_step", new=AsyncMock(return_value="ok")),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    types = [e.type for e in events]
    # task_start must precede step_start; step_start precede step_done; step_done precede task_done
    assert types.index("task_start") < types.index("step_start")
    assert types.index("step_start") < types.index("step_done")
    assert types.index("step_done") < types.index("task_done")


# ---------------------------------------------------------------------------
# thinking + streaming_token emissions from ConversationalAgent
# ---------------------------------------------------------------------------


async def test_thinking_emitted_before_llm_call() -> None:
    events: list[StatusEvent] = []

    async def cb(e: StatusEvent) -> None:
        events.append(e)

    mock_client = MagicMock()
    mock_client.chat_routed = AsyncMock(return_value="Hello!")
    del mock_client.chat_stream  # ensure no stream path

    agent = _make_agent(ai_client=mock_client, on_status_update=cb)

    with (
        patch.object(agent, "_build_system_prompt", return_value="sys"),
        patch(
            "navig.agent.conv.agent.ConversationalAgent._get_ai_response",
            wraps=agent._get_ai_response,
        ),
        patch("navig.routing.router.get_router", side_effect=ImportError("no router")),
    ):
        await agent._get_ai_response("hi there")

    thinking_events = [e for e in events if e.type == "thinking"]
    assert len(thinking_events) >= 1
    assert thinking_events[0].task_id == agent._session_id
    assert thinking_events[0].message == "Thinking\u2026"


async def test_streaming_token_emitted_when_chat_stream_present() -> None:
    events: list[StatusEvent] = []

    async def cb(e: StatusEvent) -> None:
        events.append(e)

    async def _fake_stream(*args, **kwargs):
        for token in ["Hello", " ", "world"]:
            yield token

    mock_client = MagicMock()
    mock_client.chat_stream = _fake_stream

    agent = _make_agent(ai_client=mock_client, on_status_update=cb)

    with patch.object(agent, "_build_system_prompt", return_value="sys"):
        result = await agent._get_ai_response("hi")

    stream_events = [e for e in events if e.type == "streaming_token"]
    assert len(stream_events) == 3
    tokens = [e.metadata["token"] for e in stream_events]
    assert tokens == ["Hello", " ", "world"]
    assert result == "Hello world"


# ---------------------------------------------------------------------------
# 6. ConsoleStatusRenderer
# ---------------------------------------------------------------------------


def test_console_renderer_registers_as_callback() -> None:
    agent = _make_agent()
    renderer = ConsoleStatusRenderer(agent)
    assert agent.on_status_update is renderer


def test_console_renderer_syncs_executor_notify_cb() -> None:
    agent = _make_agent()
    ConsoleStatusRenderer(agent)
    assert agent._executor._notify_cb is agent._on_status_update


async def test_console_renderer_task_start_does_not_raise() -> None:
    agent = _make_agent()
    renderer = ConsoleStatusRenderer(agent)
    event = StatusEvent(
        type="task_start", task_id="abc", message="Starting", timestamp=datetime.now()
    )
    await renderer(event)  # must not raise


async def test_console_renderer_step_failed_renders_error() -> None:
    agent = _make_agent()
    renderer = ConsoleStatusRenderer(agent)
    with patch.object(renderer._console, "print") as mock_print:
        event = StatusEvent(
            type="step_failed",
            task_id="abc",
            message="fail",
            timestamp=datetime.now(),
            metadata={"error": "Something went wrong"},
        )
        await renderer(event)
    mock_print.assert_called_once()
    rendered = mock_print.call_args[0][0]
    assert "Something went wrong" in rendered


async def test_console_renderer_task_done_prints_elapsed() -> None:
    agent = _make_agent()
    renderer = ConsoleStatusRenderer(agent)
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t1 = datetime(2026, 1, 1, 12, 0, 5)
    renderer._task_start_time = t0

    with patch.object(renderer._console, "print") as mock_print:
        await renderer(
            StatusEvent(type="task_done", task_id="abc", message="done", timestamp=t1)
        )
    output: str = mock_print.call_args[0][0]
    assert "5.00s" in output


async def test_console_renderer_streaming_token_updates_live() -> None:
    agent = _make_agent()
    renderer = ConsoleStatusRenderer(agent)

    for token in ["Tok", "en", "s"]:
        await renderer(
            StatusEvent(
                type="streaming_token",
                task_id="s",
                message="",
                timestamp=datetime.now(),
                metadata={"token": token},
            )
        )

    assert renderer._token_buffer == "Tokens"
    assert renderer._live is not None
    # Cleanup
    renderer._stop_live()


# ---------------------------------------------------------------------------
# Bug-fix coverage: callable-instance detection (B1)
# ---------------------------------------------------------------------------


async def test_emit_event_callable_class_instance() -> None:
    """ConsoleStatusRenderer (callable class) must be called — not skipped because
    asyncio.iscoroutinefunction(instance) would return False."""
    received: list[StatusEvent] = []

    class AsyncCallable:
        async def __call__(self, event: StatusEvent) -> None:
            received.append(event)

    agent = _make_agent(on_status_update=AsyncCallable())
    event = StatusEvent(
        type="thinking", task_id="1", message="hi", timestamp=datetime.now()
    )
    await agent._emit_event(event)
    assert received == [event]


async def test_executor_emit_event_callable_class_instance() -> None:
    """Same detection must work in TaskExecutor._emit_event."""
    received: list[StatusEvent] = []

    class SyncCallable:
        def __call__(self, event: StatusEvent) -> None:
            received.append(event)

    executor = TaskExecutor(on_status_update=SyncCallable(), max_attempts=1)
    task = _make_task("step")
    with (
        patch.object(executor, "_execute_step", new=AsyncMock(return_value="ok")),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    assert any(e.type == "task_start" for e in received)


# ---------------------------------------------------------------------------
# Bug-fix coverage: task_done emitted BEFORE current_task = None (B2)
# ---------------------------------------------------------------------------


async def test_task_done_emitted_before_current_task_cleared() -> None:
    """task_done callback must see current_task still populated, not None."""
    seen_current_task_at_task_done: list = []
    executor = TaskExecutor(on_status_update=None, max_attempts=1)

    async def cb(event: StatusEvent) -> None:
        if event.type == "task_done":
            seen_current_task_at_task_done.append(executor.current_task)

    executor._notify_cb = cb
    task = _make_task("step")
    with (
        patch.object(executor, "_execute_step", new=AsyncMock(return_value="ok")),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    assert len(seen_current_task_at_task_done) == 1
    assert seen_current_task_at_task_done[0] is not None  # still set during emission
    assert executor.current_task is None  # cleared after emission


# ---------------------------------------------------------------------------
# Bug-fix coverage: step_failed carries attempt + is_final metadata (B3)
# ---------------------------------------------------------------------------


async def test_step_failed_final_attempt_is_final_true() -> None:
    """With max_attempts=1 the only failure must have is_final=True, attempt=0."""
    events: list[StatusEvent] = []
    executor = _make_executor(events)  # max_attempts=1
    task = _make_task("fail step")
    with (
        patch.object(
            executor, "_execute_step", new=AsyncMock(side_effect=RuntimeError("x"))
        ),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
    ):
        await executor.execute(task)

    failed = [e for e in events if e.type == "step_failed"]
    assert len(failed) == 1
    assert failed[0].metadata["is_final"] is True
    assert failed[0].metadata["attempt"] == 0


async def test_step_failed_retry_attempt_is_final_false() -> None:
    """With max_attempts=3 the first two failures must have is_final=False."""
    events: list[StatusEvent] = []
    executor = TaskExecutor(on_status_update=None, max_attempts=3)

    async def cb(e: StatusEvent) -> None:
        events.append(e)

    executor._notify_cb = cb

    call_count = 0

    async def flaky_step(step, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError(f"attempt {call_count}")
        return "ok"

    task = _make_task("flaky")
    with (
        patch.object(executor, "_execute_step", side_effect=flaky_step),
        patch(
            "navig.agent.conv.executor.asyncio.to_thread",
            new=AsyncMock(
                return_value=MagicMock(content='{"achieved": true, "confidence": 100}')
            ),
        ),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await executor.execute(task)

    failed = [e for e in events if e.type == "step_failed"]
    assert len(failed) == 2
    assert failed[0].metadata["is_final"] is False
    assert failed[0].metadata["attempt"] == 0
    assert failed[1].metadata["is_final"] is False
    assert failed[1].metadata["attempt"] == 1


# ---------------------------------------------------------------------------
# Bug-fix coverage: no deprecation warning for asyncio.iscoroutinefunction (B6)
# ---------------------------------------------------------------------------


async def test_no_asyncio_iscoroutinefunction_deprecation_warning() -> None:
    """Neither agent nor executor _emit_event should trigger DeprecationWarning
    for asyncio.iscoroutinefunction (deprecated in 3.14)."""
    import warnings

    async def cb(event: StatusEvent) -> None:
        pass

    agent = _make_agent(on_status_update=cb)
    event = StatusEvent(
        type="thinking", task_id="1", message="hi", timestamp=datetime.now()
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await agent._emit_event(event)  # must not raise DeprecationWarning

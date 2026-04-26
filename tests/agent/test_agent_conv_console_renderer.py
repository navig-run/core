"""Tests for navig.agent.conv.console_renderer — ConsoleStatusRenderer."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from rich.console import Console

from navig.agent.conv.status_event import StatusEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    type_: str,
    task_id: str = "task-1",
    message: str = "Test message",
    step_index: int | None = None,
    total_steps: int | None = None,
    metadata: dict | None = None,
) -> StatusEvent:
    return StatusEvent(
        type=type_,
        task_id=task_id,
        message=message,
        timestamp=_now(),
        step_index=step_index,
        total_steps=total_steps,
        metadata=metadata or {},
    )


def _mock_console() -> Console:
    """Return a rich Console backed by StringIO (no real terminal)."""
    return Console(file=io.StringIO(), highlight=False, markup=True)


def _make_renderer(mock_agent=None, console=None):
    """Create a ConsoleStatusRenderer with mocked dependencies."""
    from navig.agent.conv.console_renderer import ConsoleStatusRenderer

    if mock_agent is None:
        mock_agent = MagicMock()
        mock_agent.on_status_update = None  # settable

    if console is None:
        console = _mock_console()

    with patch("navig.agent.conv.console_renderer.get_console", return_value=console):
        renderer = ConsoleStatusRenderer(mock_agent)

    return renderer, console, mock_agent


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestConsoleStatusRendererInit:
    def test_init_sets_agent_callback(self):
        mock_agent = MagicMock()
        renderer, _, agent = _make_renderer(mock_agent)
        # agent.on_status_update should have been set to renderer
        assert agent.on_status_update is renderer

    def test_init_progress_is_none(self):
        renderer, _, _ = _make_renderer()
        assert renderer._progress is None

    def test_init_live_is_none(self):
        renderer, _, _ = _make_renderer()
        assert renderer._live is None

    def test_init_token_buffer_empty(self):
        renderer, _, _ = _make_renderer()
        assert renderer._token_buffer == ""

    def test_init_stores_console(self):
        console = _mock_console()
        renderer, _, _ = _make_renderer(console=console)
        assert renderer._console is console


# ---------------------------------------------------------------------------
# _stop_live
# ---------------------------------------------------------------------------

class TestStopLive:
    def test_stop_live_clears_live(self):
        renderer, _, _ = _make_renderer()
        mock_live = MagicMock()
        renderer._live = mock_live
        renderer._stop_live()
        mock_live.stop.assert_called_once()
        assert renderer._live is None

    def test_stop_live_clears_token_buffer(self):
        renderer, _, _ = _make_renderer()
        renderer._token_buffer = "buffered content"
        renderer._live = MagicMock()
        renderer._stop_live()
        assert renderer._token_buffer == ""

    def test_stop_live_noop_when_no_live(self):
        renderer, _, _ = _make_renderer()
        renderer._live = None
        renderer._stop_live()  # Should not raise


# ---------------------------------------------------------------------------
# _active_console
# ---------------------------------------------------------------------------

class TestActiveConsole:
    def test_active_console_without_progress_returns_root_console(self):
        renderer, console, _ = _make_renderer()
        assert renderer._active_console() is console

    def test_active_console_with_progress_returns_progress_console(self):
        renderer, _, _ = _make_renderer()
        mock_progress = MagicMock()
        mock_progress_console = _mock_console()
        mock_progress.console = mock_progress_console
        renderer._progress = mock_progress
        assert renderer._active_console() is mock_progress_console


# ---------------------------------------------------------------------------
# Event dispatch (__call__)
# ---------------------------------------------------------------------------

class TestEventDispatch:
    def test_call_dispatches_task_start(self):
        renderer, _, _ = _make_renderer()
        renderer._on_task_start = AsyncMock()
        event = _event("task_start")
        asyncio.run(renderer(event))
        renderer._on_task_start.assert_awaited_once_with(event)

    def test_call_dispatches_step_start(self):
        renderer, _, _ = _make_renderer()
        renderer._on_step_start = AsyncMock()
        event = _event("step_start")
        asyncio.run(renderer(event))
        renderer._on_step_start.assert_awaited_once_with(event)

    def test_call_dispatches_step_done(self):
        renderer, _, _ = _make_renderer()
        renderer._on_step_done = AsyncMock()
        event = _event("step_done")
        asyncio.run(renderer(event))
        renderer._on_step_done.assert_awaited_once_with(event)

    def test_call_dispatches_step_failed(self):
        renderer, _, _ = _make_renderer()
        renderer._on_step_failed = AsyncMock()
        event = _event("step_failed")
        asyncio.run(renderer(event))
        renderer._on_step_failed.assert_awaited_once_with(event)

    def test_call_dispatches_task_done(self):
        renderer, _, _ = _make_renderer()
        renderer._on_task_done = AsyncMock()
        event = _event("task_done")
        asyncio.run(renderer(event))
        renderer._on_task_done.assert_awaited_once_with(event)

    def test_call_dispatches_thinking(self):
        renderer, _, _ = _make_renderer()
        renderer._on_thinking = AsyncMock()
        event = _event("thinking")
        asyncio.run(renderer(event))
        renderer._on_thinking.assert_awaited_once_with(event)

    def test_call_dispatches_streaming_token(self):
        renderer, _, _ = _make_renderer()
        renderer._on_streaming_token = AsyncMock()
        event = _event("streaming_token", metadata={"token": "h"})
        asyncio.run(renderer(event))
        renderer._on_streaming_token.assert_awaited_once_with(event)


# ---------------------------------------------------------------------------
# _on_task_start
# ---------------------------------------------------------------------------

class TestOnTaskStart:
    def test_task_start_sets_start_time(self):
        renderer, _, _ = _make_renderer()
        ts = _now()
        event = StatusEvent(type="task_start", task_id="t1", message="Starting", timestamp=ts)
        asyncio.run(renderer._on_task_start(event))
        assert renderer._task_start_time == ts

    def test_task_start_creates_progress(self):
        renderer, _, _ = _make_renderer()
        event = _event("task_start")
        asyncio.run(renderer._on_task_start(event))
        assert renderer._progress is not None

    def test_task_start_stops_existing_progress(self):
        renderer, _, _ = _make_renderer()
        mock_progress = MagicMock()
        renderer._progress = mock_progress
        event = _event("task_start")
        asyncio.run(renderer._on_task_start(event))
        mock_progress.stop.assert_called()

    def test_task_start_stops_existing_live(self):
        renderer, _, _ = _make_renderer()
        mock_live = MagicMock()
        renderer._live = mock_live
        event = _event("task_start")
        asyncio.run(renderer._on_task_start(event))
        mock_live.stop.assert_called()


# ---------------------------------------------------------------------------
# _on_step_start
# ---------------------------------------------------------------------------

class TestOnStepStart:
    def test_step_start_noop_without_progress(self):
        renderer, _, _ = _make_renderer()
        event = _event("step_start", step_index=0, total_steps=3)
        asyncio.run(renderer._on_step_start(event))  # Should not raise

    def test_step_start_adds_task_when_none(self):
        renderer, _, _ = _make_renderer()
        # Create a real progress attached to a StringIO console
        from rich.progress import Progress, SpinnerColumn, TextColumn
        console = _mock_console()
        progress = Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console)
        progress.start()
        renderer._progress = progress
        renderer._progress_task_id = None

        event = _event("step_start", step_index=0, total_steps=5)
        asyncio.run(renderer._on_step_start(event))
        assert renderer._progress_task_id is not None
        progress.stop()

    def test_step_start_updates_existing_task(self):
        renderer, _, _ = _make_renderer()
        mock_progress = MagicMock()
        renderer._progress = mock_progress
        renderer._progress_task_id = MagicMock()

        event = _event("step_start", step_index=2, total_steps=5)
        asyncio.run(renderer._on_step_start(event))
        mock_progress.update.assert_called_once()

    def test_step_start_zero_index_is_valid(self):
        renderer, _, _ = _make_renderer()
        mock_progress = MagicMock()
        renderer._progress = mock_progress
        renderer._progress_task_id = None

        event = _event("step_start", step_index=0, total_steps=1)
        asyncio.run(renderer._on_step_start(event))
        mock_progress.add_task.assert_called()


# ---------------------------------------------------------------------------
# _on_step_done / _on_step_failed
# ---------------------------------------------------------------------------

class TestOnStepDoneAndFailed:
    def test_step_done_advances_progress(self):
        renderer, _, _ = _make_renderer()
        mock_progress = MagicMock()
        mock_task_id = MagicMock()
        renderer._progress = mock_progress
        renderer._progress_task_id = mock_task_id

        event = _event("step_done", message="Step finished")
        asyncio.run(renderer._on_step_done(event))
        mock_progress.advance.assert_called_once_with(mock_task_id, 1)

    def test_step_done_noop_without_progress(self):
        renderer, console, _ = _make_renderer()
        event = _event("step_done", message="OK")
        asyncio.run(renderer._on_step_done(event))  # Should not raise

    def test_step_failed_final_prints_error(self):
        renderer, console, _ = _make_renderer()
        event = _event("step_failed", message="Step broke", metadata={"error": "Timeout", "is_final": True})
        with patch.object(console, "print") as mock_print:
            renderer._console = console
            asyncio.run(renderer._on_step_failed(event))
        mock_print.assert_called()
        call_args = mock_print.call_args[0][0]
        assert "Timeout" in call_args or "broke" in call_args

    def test_step_failed_non_final_prints_retry(self):
        renderer, console, _ = _make_renderer()
        event = _event("step_failed", message="transient", metadata={"error": "Timeout", "is_final": False, "attempt": 1})
        with patch.object(console, "print") as mock_print:
            renderer._console = console
            asyncio.run(renderer._on_step_failed(event))
        mock_print.assert_called()


# ---------------------------------------------------------------------------
# _on_task_done
# ---------------------------------------------------------------------------

class TestOnTaskDone:
    def test_task_done_stops_progress(self):
        renderer, _, _ = _make_renderer()
        mock_progress = MagicMock()
        renderer._progress = mock_progress
        event = _event("task_done")
        asyncio.run(renderer._on_task_done(event))
        mock_progress.stop.assert_called()
        assert renderer._progress is None

    def test_task_done_clears_task_id(self):
        renderer, _, _ = _make_renderer()
        renderer._progress = MagicMock()
        renderer._progress_task_id = MagicMock()
        event = _event("task_done")
        asyncio.run(renderer._on_task_done(event))
        assert renderer._progress_task_id is None

    def test_task_done_computes_elapsed(self):
        renderer, console, _ = _make_renderer()
        ts_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts_end = datetime(2024, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
        renderer._task_start_time = ts_start
        event = StatusEvent(type="task_done", task_id="t1", message="Done", timestamp=ts_end)

        with patch.object(console, "print") as mock_print:
            renderer._console = console
            asyncio.run(renderer._on_task_done(event))
        printed = mock_print.call_args[0][0]
        assert "5.00s" in printed

    def test_task_done_no_start_time(self):
        renderer, _, _ = _make_renderer()
        renderer._task_start_time = None
        event = _event("task_done")
        asyncio.run(renderer._on_task_done(event))  # Should not raise


# ---------------------------------------------------------------------------
# _on_thinking
# ---------------------------------------------------------------------------

class TestOnThinking:
    def test_thinking_stops_live(self):
        renderer, _, _ = _make_renderer()
        mock_live = MagicMock()
        renderer._live = mock_live
        event = _event("thinking")
        asyncio.run(renderer._on_thinking(event))
        mock_live.stop.assert_called()

    def test_thinking_prints_dim_message(self):
        renderer, console, _ = _make_renderer()
        event = _event("thinking")
        with patch.object(console, "print") as mock_print:
            renderer._console = console
            asyncio.run(renderer._on_thinking(event))
        mock_print.assert_called_once()
        assert "Thinking" in mock_print.call_args[0][0] or "thinking" in mock_print.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# _on_streaming_token
# ---------------------------------------------------------------------------

class TestOnStreamingToken:
    def test_streaming_token_accumulates_buffer(self):
        renderer, _, _ = _make_renderer()
        # Mock Live to avoid real terminal
        with patch("navig.agent.conv.console_renderer.Live") as MockLive:
            mock_live_instance = MagicMock()
            MockLive.return_value = mock_live_instance
            asyncio.run(renderer._on_streaming_token(_event("streaming_token", metadata={"token": "He"})))
            asyncio.run(renderer._on_streaming_token(_event("streaming_token", metadata={"token": "llo"})))
        assert renderer._token_buffer == "Hello"

    def test_streaming_token_creates_live_on_first_token(self):
        renderer, _, _ = _make_renderer()
        with patch("navig.agent.conv.console_renderer.Live") as MockLive:
            mock_live_instance = MagicMock()
            MockLive.return_value = mock_live_instance
            asyncio.run(renderer._on_streaming_token(_event("streaming_token", metadata={"token": "H"})))
        MockLive.assert_called_once()
        mock_live_instance.start.assert_called_once()

    def test_streaming_token_reuses_live(self):
        renderer, _, _ = _make_renderer()
        with patch("navig.agent.conv.console_renderer.Live") as MockLive:
            mock_live_instance = MagicMock()
            MockLive.return_value = mock_live_instance
            asyncio.run(renderer._on_streaming_token(_event("streaming_token", metadata={"token": "a"})))
            # Second token — Live already created
            asyncio.run(renderer._on_streaming_token(_event("streaming_token", metadata={"token": "b"})))
        MockLive.assert_called_once()  # Still only one Live created

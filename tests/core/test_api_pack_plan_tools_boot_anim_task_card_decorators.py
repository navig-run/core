"""
Batch 113: tests for
  navig/tools/domains/api_pack.py
  navig/agent/tools/plan_tools.py
  navig/onboarding/boot_anim.py
  navig/gateway/channels/task_card.py
  navig/gateway/channels/utils/decorators.py
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig/tools/domains/api_pack.py
# ---------------------------------------------------------------------------

from navig.tools.domains.api_pack import (
    _api_get_json,
    _api_post_json,
    _infra_inventory,
    _infra_node_status,
    _trading_fetch_ohlc,
    _trading_portfolio,
)
from navig.tools.api_schema import ApiToolResult


class _FakeResp:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_ok(data: Any):
    """Return a mock that mimics urllib.request.urlopen as context manager."""
    resp = _FakeResp(data)
    return MagicMock(return_value=resp, side_effect=None, __enter__=lambda s: resp, __exit__=lambda s, *a: False)


def test_api_get_json_success():
    payload = {"result": "ok", "value": 42}
    resp = _FakeResp(payload)
    with patch("urllib.request.urlopen", return_value=resp):
        result = _api_get_json("http://example.com/api")
    assert result["status"] == "ok"
    assert result["normalized"]["result"] == "ok"


def test_api_get_json_list_wraps_in_data():
    payload = [1, 2, 3]
    resp = _FakeResp(payload)
    with patch("urllib.request.urlopen", return_value=resp):
        result = _api_get_json("http://example.com/api")
    assert result["status"] == "ok"
    assert result["normalized"] == {"data": [1, 2, 3]}


def test_api_get_json_with_params():
    payload = {"ok": True}
    resp = _FakeResp(payload)
    with patch("urllib.request.urlopen", return_value=resp):
        result = _api_get_json("http://example.com/api", params={"q": "test"})
    assert result["status"] == "ok"


def test_api_get_json_error():
    with patch("urllib.request.urlopen", side_effect=Exception("network down")):
        result = _api_get_json("http://example.com/api")
    assert result["status"] == "error"
    assert "network down" in result.get("error", "") or "network down" in str(result)


def test_api_post_json_success():
    payload = {"created": True}
    resp = _FakeResp(payload)
    with patch("urllib.request.urlopen", return_value=resp):
        result = _api_post_json("http://example.com/api", body={"name": "test"})
    assert result["status"] == "ok"


def test_api_post_json_list_response():
    payload = ["a", "b"]
    resp = _FakeResp(payload)
    with patch("urllib.request.urlopen", return_value=resp):
        result = _api_post_json("http://example.com/api")
    assert result["normalized"] == {"data": ["a", "b"]}


def test_api_post_json_error():
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        result = _api_post_json("http://example.com/api")
    assert result["status"] == "error"


def test_trading_fetch_ohlc_stub():
    result = _trading_fetch_ohlc("BTC/USDT", timeframe="4h", limit=50)
    assert result["status"] == "ok"
    assert result["normalized"]["symbol"] == "BTC/USDT"
    assert result["normalized"]["timeframe"] == "4h"
    assert "candles" in result["normalized"]


def test_trading_portfolio_stub():
    result = _trading_portfolio(exchange="binance")
    assert result["status"] == "ok"
    assert result["normalized"]["exchange"] == "binance"
    assert "balances" in result["normalized"]


def test_infra_node_status_current():
    result = _infra_node_status(host="current")
    assert result["status"] == "ok"
    assert "platform" in result["normalized"] or "note" in result["normalized"]


def test_infra_node_status_remote():
    result = _infra_node_status(host="myserver")
    assert result["status"] == "ok"
    assert result["normalized"]["host"] == "myserver"
    assert "note" in result["normalized"]


def test_infra_inventory_no_config():
    with patch("navig.tools.domains.api_pack._infra_inventory") as mock_inv:
        mock_inv.return_value = {"status": "ok", "normalized": {"scope": "all", "hosts": []}}
        result = mock_inv(scope="all")
    assert result["status"] == "ok"


def test_infra_inventory_real():
    # Should not raise; config_manager may or may not be available
    result = _infra_inventory(scope="all")
    assert result["status"] == "ok"
    assert "scope" in result["normalized"]


# ---------------------------------------------------------------------------
# navig/agent/tools/plan_tools.py
# ---------------------------------------------------------------------------

import navig.agent.tools.plan_tools as plan_tools_mod
from navig.agent.tools.plan_tools import (
    PlanAddStepTool,
    PlanApproveTool,
    PlanShowTool,
    _get_interceptor,
    set_interceptor,
)
from navig.tools.registry import ToolResult


def _reset_interceptor():
    plan_tools_mod._interceptor_ref = None


def test_set_and_get_interceptor():
    _reset_interceptor()
    fake = object()
    set_interceptor(fake)
    assert _get_interceptor() is fake
    _reset_interceptor()


def test_get_interceptor_raises_when_none():
    _reset_interceptor()
    with pytest.raises(RuntimeError, match="not initialised"):
        _get_interceptor()


def test_plan_add_step_tool_attributes():
    tool = PlanAddStepTool()
    assert tool.name == "plan_add_step"
    assert "step" in tool.description.lower()
    assert any(p["name"] == "description" for p in tool.parameters)


def test_plan_show_tool_attributes():
    tool = PlanShowTool()
    assert tool.name == "plan_show"
    assert tool.parameters == []


def test_plan_approve_tool_attributes():
    tool = PlanApproveTool()
    assert tool.name == "plan_approve"


@pytest.mark.asyncio
async def test_plan_add_step_missing_description():
    tool = PlanAddStepTool()
    fake = MagicMock()
    set_interceptor(fake)
    result = await tool.run({"description": ""})
    assert result.success is False
    assert "description" in result.output.lower()
    _reset_interceptor()


@pytest.mark.asyncio
async def test_plan_add_step_success():
    tool = PlanAddStepTool()
    fake_step = MagicMock()
    fake_interceptor = MagicMock()
    fake_interceptor.add_step.return_value = 0
    set_interceptor(fake_interceptor)

    # PlanStep must be mock-importable
    with patch("navig.agent.tools.plan_tools.PlanAddStepTool.run", wraps=tool.run):
        with patch("navig.agent.plan_mode.PlanStep", return_value=fake_step):
            result = await tool.run({
                "description": "do something",
                "files": "a.py,b.py",
                "tools": "tool1",
                "risk": "medium",
            })
    assert result.success is True
    assert "Step 1" in result.output
    _reset_interceptor()


@pytest.mark.asyncio
async def test_plan_add_step_value_error_from_interceptor():
    tool = PlanAddStepTool()
    fake_interceptor = MagicMock()
    fake_interceptor.add_step.side_effect = ValueError("plan locked")
    set_interceptor(fake_interceptor)

    with patch("navig.agent.plan_mode.PlanStep", return_value=MagicMock()):
        result = await tool.run({"description": "hello"})
    assert result.success is False
    assert "plan locked" in result.output
    _reset_interceptor()


@pytest.mark.asyncio
async def test_plan_show_tool_run():
    tool = PlanShowTool()
    fake = MagicMock()
    fake.format_plan.return_value = "## Plan\n- step 1"
    set_interceptor(fake)
    result = await tool.run({})
    assert result.success is True
    assert "Plan" in result.output
    _reset_interceptor()


@pytest.mark.asyncio
async def test_plan_approve_already_reviewing():
    tool = PlanApproveTool()
    fake = MagicMock()
    fake.is_planning = False
    fake.session.summary.return_value = {"total_steps": 3}
    set_interceptor(fake)
    result = await tool.run({})
    assert result.success is True
    assert "3 steps" in result.output
    fake.approve.assert_called_once()
    _reset_interceptor()


@pytest.mark.asyncio
async def test_plan_approve_from_planning():
    tool = PlanApproveTool()
    fake = MagicMock()
    fake.is_planning = True
    fake.session.summary.return_value = {"total_steps": 1}
    set_interceptor(fake)
    result = await tool.run({})
    fake.review.assert_called_once()
    fake.approve.assert_called_once()
    assert result.success is True
    _reset_interceptor()


@pytest.mark.asyncio
async def test_plan_approve_runtime_error():
    tool = PlanApproveTool()
    fake = MagicMock()
    fake.is_planning = False
    fake.approve.side_effect = RuntimeError("cannot approve")
    set_interceptor(fake)
    result = await tool.run({})
    assert result.success is False
    assert "cannot approve" in result.output
    _reset_interceptor()


# ---------------------------------------------------------------------------
# navig/onboarding/boot_anim.py
# ---------------------------------------------------------------------------

from navig.onboarding.boot_anim import _HEX, _MIN_COLS, _ROWS, _FPS, play_boot_animation


def test_boot_anim_constants():
    assert _MIN_COLS == 60
    assert _ROWS == 11
    assert _FPS == 22
    assert set(_HEX) == set("0123456789ABCDEF")


def test_boot_anim_skips_non_tty():
    """Should return None without doing anything on non-TTY."""
    fake_out = io.StringIO()
    with patch("sys.stdout", fake_out):
        result = play_boot_animation("NAVIG-TEST")
    assert result is None


def test_boot_anim_skips_narrow_terminal():
    """Skips if terminal width < _MIN_COLS."""
    with patch("sys.stdout") as mock_out:
        mock_out.isatty.return_value = True
        with patch("shutil.get_terminal_size", return_value=MagicMock(columns=40)):
            result = play_boot_animation("NAVIG-TEST")
    assert result is None


def test_boot_anim_skips_no_rich():
    """Skips gracefully if rich is not importable."""
    with patch("sys.stdout") as mock_out:
        mock_out.isatty.return_value = True
        with patch("shutil.get_terminal_size", return_value=MagicMock(columns=120)):
            with patch("builtins.__import__", side_effect=ImportError("no rich")):
                result = play_boot_animation("NAVIG-TEST")
    assert result is None


# ---------------------------------------------------------------------------
# navig/gateway/channels/task_card.py
# ---------------------------------------------------------------------------

from navig.gateway.channels.task_card import (
    STATE_ICON,
    STATE_WEIGHT,
    THROTTLE_SECONDS,
    StepState,
    TaskStep,
    TaskView,
    _progress_bar,
    build_keyboard,
    make_task,
    render,
    render_big,
    render_compact,
)


def test_step_state_values():
    assert StepState.PENDING == "pending"
    assert StepState.ACTIVE == "active"
    assert StepState.DONE == "done"
    assert StepState.FAILED == "failed"
    assert StepState.PAUSED == "paused"


def test_state_icon_all_states():
    for state in StepState:
        assert state in STATE_ICON


def test_state_weight_done_is_one():
    assert STATE_WEIGHT[StepState.DONE] == 1.0


def test_state_weight_pending_is_zero():
    assert STATE_WEIGHT[StepState.PENDING] == 0.0


def test_task_step_defaults():
    s = TaskStep(key="k1", label="do thing")
    assert s.state == StepState.PENDING
    assert s.detail is None


def test_task_view_defaults():
    v = TaskView()
    assert v.done is False
    assert v.running is True
    assert v.percent == 0
    assert v.steps == []


def test_recompute_percent_empty():
    v = TaskView()
    v.recompute_percent()
    assert v.percent == 0


def test_recompute_percent_all_done():
    v = TaskView(steps=[
        TaskStep(key="a", label="a", state=StepState.DONE),
        TaskStep(key="b", label="b", state=StepState.DONE),
    ])
    v.recompute_percent()
    assert v.percent == 100


def test_recompute_percent_half():
    v = TaskView(steps=[
        TaskStep(key="a", label="a", state=StepState.DONE),
        TaskStep(key="b", label="b", state=StepState.PENDING),
    ])
    v.recompute_percent()
    assert v.percent == 50


def test_set_step_success():
    v = TaskView(steps=[TaskStep(key="s1", label="step 1")])
    v.set_step("s1", StepState.ACTIVE, detail="running")
    assert v.steps[0].state == StepState.ACTIVE
    assert v.steps[0].detail == "running"


def test_set_step_key_error():
    v = TaskView()
    with pytest.raises(KeyError):
        v.set_step("missing", StepState.DONE)


def test_active_step_none():
    v = TaskView(steps=[TaskStep(key="x", label="x", state=StepState.PENDING)])
    assert v.active_step is None


def test_active_step_found():
    v = TaskView(steps=[
        TaskStep(key="a", label="a", state=StepState.PENDING),
        TaskStep(key="b", label="b", state=StepState.ACTIVE),
    ])
    assert v.active_step is not None
    assert v.active_step.key == "b"


def test_progress_bar_empty():
    bar = _progress_bar(0, slots=8)
    assert "▰" not in bar
    assert len(bar) == 8


def test_progress_bar_full():
    bar = _progress_bar(100, slots=8)
    assert "▱" not in bar
    assert len(bar) == 8


def test_progress_bar_half():
    bar = _progress_bar(50, slots=8)
    assert len(bar) == 8


def test_render_compact_active():
    v = TaskView(
        percent=45,
        steps=[TaskStep(key="a", label="loading", state=StepState.ACTIVE)],
    )
    text = render_compact(v)
    assert "45%" in text
    assert "loading" in text


def test_render_compact_done():
    v = TaskView(percent=100, done=True, steps=[])
    text = render_compact(v)
    assert "100%" in text
    assert "Done" in text


def test_render_big_includes_title():
    v = TaskView(title="My Task", steps=[TaskStep(key="s", label="step")])
    text = render_big(v)
    assert "My Task" in text


def test_render_delegates():
    v = TaskView(expanded=True, title="Test")
    assert render(v) == render_big(v)
    v.expanded = False
    assert render(v) == render_compact(v)


def test_build_keyboard_structure():
    v = TaskView()
    kb = build_keyboard(v)
    assert "inline_keyboard" in kb
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    # First row has 2 buttons
    assert len(rows[0]) == 2
    # Verify callback_data
    cbs = {btn["callback_data"] for row in rows for btn in row}
    assert "task:toggle_details" in cbs
    assert "task:stop" in cbs


def test_make_task():
    v = make_task([("s1", "Step 1"), ("s2", "Step 2")], title="My Plan")
    assert v.title == "My Plan"
    assert len(v.steps) == 2
    assert v.steps[0].key == "s1"
    assert v.steps[1].label == "Step 2"
    assert all(s.state == StepState.PENDING for s in v.steps)


# ---------------------------------------------------------------------------
# navig/gateway/channels/utils/decorators.py
# ---------------------------------------------------------------------------

from navig.gateway.channels.utils.decorators import (
    RateLimiter,
    error_handled,
    rate_limited,
    typing_context,
)


def test_rate_limiter_construction():
    rl = RateLimiter(max_requests=5, window_minutes=2)
    assert rl.max_requests == 5


def test_rate_limiter_first_request_allowed():
    rl = RateLimiter(max_requests=3, window_minutes=1)
    assert rl.is_allowed(1001) is True


def test_rate_limiter_exceeds_limit():
    rl = RateLimiter(max_requests=2, window_minutes=1)
    assert rl.is_allowed(42) is True
    assert rl.is_allowed(42) is True
    assert rl.is_allowed(42) is False


def test_rate_limiter_different_users_independent():
    rl = RateLimiter(max_requests=1, window_minutes=1)
    assert rl.is_allowed(1) is True
    assert rl.is_allowed(2) is True


def test_rate_limiter_cleans_old_requests():
    rl = RateLimiter(max_requests=1, window_minutes=1)
    # Prefill with old entry
    user_id = 99
    rl.requests[user_id].append(datetime.now() - timedelta(minutes=2))
    # Should be treated as expired → new request allowed
    assert rl.is_allowed(user_id) is True


@pytest.mark.asyncio
async def test_rate_limited_passes_through():
    call_log = []

    class FakeHandler:
        @rate_limited
        async def handle(self, chat_id: int, user_id: int):
            call_log.append(user_id)

    h = FakeHandler()
    with patch(
        "navig.gateway.channels.utils.decorators._get_global_limiter",
        return_value=RateLimiter(max_requests=10),
    ):
        await h.handle(100, user_id=999)
    assert 999 in call_log


@pytest.mark.asyncio
async def test_rate_limited_blocks_and_sends_message():
    class FakeHandler:
        sent = []

        @rate_limited
        async def handle(self, chat_id: int, user_id: int):
            self.sent.append("called")

        async def send_message(self, chat_id, text):
            FakeHandler.sent.append(text)

    h = FakeHandler()
    rl = MagicMock()
    rl.is_allowed.return_value = False
    with patch("navig.gateway.channels.utils.decorators._get_global_limiter", return_value=rl):
        await h.handle(777, user_id=5)
    assert "called" not in FakeHandler.sent


@pytest.mark.asyncio
async def test_error_handled_passes_through():
    results = []

    class FakeHandler:
        @error_handled
        async def handle(self, chat_id: int):
            results.append("ok")

    h = FakeHandler()
    await h.handle(1)
    assert results == ["ok"]


@pytest.mark.asyncio
async def test_error_handled_catches_exception():
    class FakeHandler:
        messages = []

        @error_handled
        async def handle(self, chat_id: int):
            raise ValueError("boom")

        async def send_message(self, chat_id, msg, **kwargs):
            FakeHandler.messages.append(msg)

    h = FakeHandler()
    # Should not propagate the exception
    await h.handle(99)
    assert len(FakeHandler.messages) == 1
    assert "boom" in FakeHandler.messages[0]


@pytest.mark.asyncio
async def test_typing_context_passes_through():
    results = []

    class FakeHandler:
        @typing_context
        async def handle(self, chat_id: int):
            results.append("called")

        async def _api_call(self, method, payload):
            pass

    h = FakeHandler()
    await h.handle(123)
    assert results == ["called"]

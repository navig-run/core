"""Unit tests for gateway/channels/task_card.py and gateway/channels/utils/decorators.py."""
from __future__ import annotations

import asyncio
import time

import pytest

from navig.gateway.channels.task_card import (
    STATE_ICON,
    STATE_WEIGHT,
    THROTTLE_SECONDS,
    TaskStep,
    TaskView,
    StepState,
    _progress_bar,
    build_keyboard,
    make_task,
    render,
    render_big,
    render_compact,
)
from navig.gateway.channels.utils.decorators import RateLimiter


# ---------------------------------------------------------------------------
# StepState enum
# ---------------------------------------------------------------------------

class TestStepState:
    def test_values(self):
        assert StepState.PENDING == "pending"
        assert StepState.ACTIVE == "active"
        assert StepState.DONE == "done"
        assert StepState.FAILED == "failed"
        assert StepState.PAUSED == "paused"


# ---------------------------------------------------------------------------
# STATE_ICON / STATE_WEIGHT
# ---------------------------------------------------------------------------

class TestStateConstants:
    def test_icon_keys_cover_all_states(self):
        for state in StepState:
            assert state in STATE_ICON

    def test_weight_keys_cover_all_states(self):
        for state in StepState:
            assert state in STATE_WEIGHT

    def test_done_weight_is_1(self):
        assert STATE_WEIGHT[StepState.DONE] == 1.0

    def test_pending_weight_is_0(self):
        assert STATE_WEIGHT[StepState.PENDING] == 0.0

    def test_active_weight_positive(self):
        assert STATE_WEIGHT[StepState.ACTIVE] > 0

    def test_throttle_seconds_positive(self):
        assert THROTTLE_SECONDS > 0


# ---------------------------------------------------------------------------
# TaskStep
# ---------------------------------------------------------------------------

class TestTaskStep:
    def test_defaults(self):
        s = TaskStep(key="s1", label="Step 1")
        assert s.state == StepState.PENDING
        assert s.detail is None

    def test_custom_state(self):
        s = TaskStep(key="s2", label="Step 2", state=StepState.ACTIVE)
        assert s.state == StepState.ACTIVE

    def test_detail_stored(self):
        s = TaskStep(key="s3", label="Step 3", detail="info")
        assert s.detail == "info"


# ---------------------------------------------------------------------------
# TaskView.recompute_percent
# ---------------------------------------------------------------------------

class TestRecomputePercent:
    def test_empty_steps_gives_zero(self):
        view = TaskView(steps=[])
        view.recompute_percent()
        assert view.percent == 0

    def test_all_done(self):
        view = make_task([("a", "A"), ("b", "B"), ("c", "C")])
        for s in view.steps:
            s.state = StepState.DONE
        view.recompute_percent()
        assert view.percent == 100

    def test_all_pending_gives_zero(self):
        view = make_task([("a", "A"), ("b", "B")])
        view.recompute_percent()
        assert view.percent == 0

    def test_half_done(self):
        view = make_task([("a", "A"), ("b", "B")])
        view.steps[0].state = StepState.DONE
        view.recompute_percent()
        assert view.percent == 50

    def test_percent_capped_at_100(self):
        """Ensure no overshoot."""
        view = make_task([("a", "A")])
        view.steps[0].state = StepState.DONE
        view.recompute_percent()
        assert view.percent <= 100


# ---------------------------------------------------------------------------
# TaskView.set_step
# ---------------------------------------------------------------------------

class TestSetStep:
    def test_set_existing_key(self):
        view = make_task([("k1", "Step 1"), ("k2", "Step 2")])
        view.set_step("k1", StepState.ACTIVE)
        assert view.steps[0].state == StepState.ACTIVE

    def test_set_detail(self):
        view = make_task([("k1", "Step 1")])
        view.set_step("k1", StepState.DONE, detail="finished OK")
        assert view.steps[0].detail == "finished OK"

    def test_set_nonexistent_key_raises(self):
        view = make_task([("k1", "Step 1")])
        with pytest.raises(KeyError):
            view.set_step("missing", StepState.DONE)

    def test_set_does_not_override_detail_when_none(self):
        view = make_task([("k1", "Step 1")])
        view.steps[0].detail = "original"
        view.set_step("k1", StepState.DONE, detail=None)
        # detail=None means don't update
        assert view.steps[0].detail == "original"


# ---------------------------------------------------------------------------
# TaskView.active_step
# ---------------------------------------------------------------------------

class TestActiveStep:
    def test_none_when_no_active(self):
        view = make_task([("a", "A"), ("b", "B")])
        assert view.active_step is None

    def test_returns_active_step(self):
        view = make_task([("a", "A"), ("b", "B")])
        view.steps[1].state = StepState.ACTIVE
        assert view.active_step is view.steps[1]

    def test_returns_first_active_if_multiple(self):
        view = make_task([("a", "A"), ("b", "B")])
        view.steps[0].state = StepState.ACTIVE
        view.steps[1].state = StepState.ACTIVE
        assert view.active_step is view.steps[0]


# ---------------------------------------------------------------------------
# TaskView.throttle_ready / mark_edited
# ---------------------------------------------------------------------------

class TestThrottle:
    def test_ready_after_init(self):
        """New view with _last_edit=0 is definitely throttle-ready."""
        view = TaskView()
        assert view.throttle_ready is True

    def test_not_ready_immediately_after_mark(self):
        view = TaskView()
        view.mark_edited()
        assert view.throttle_ready is False

    def test_ready_after_enough_time(self):
        view = TaskView()
        view._last_edit = time.monotonic() - THROTTLE_SECONDS - 0.1
        assert view.throttle_ready is True


# ---------------------------------------------------------------------------
# _progress_bar
# ---------------------------------------------------------------------------

class TestProgressBar:
    def test_zero_percent(self):
        bar = _progress_bar(0, slots=4)
        assert bar.count("▱") == 4
        assert bar.count("▰") == 0

    def test_full_percent(self):
        bar = _progress_bar(100, slots=4)
        assert bar.count("▰") == 4
        assert bar.count("▱") == 0

    def test_half_percent(self):
        bar = _progress_bar(50, slots=4)
        assert bar.count("▰") == 2
        assert bar.count("▱") == 2

    def test_default_slots_is_8(self):
        bar = _progress_bar(0)
        assert len(bar) == 8

    def test_total_length_equals_slots(self):
        bar = _progress_bar(75, slots=10)
        assert len(bar) == 10


# ---------------------------------------------------------------------------
# render_compact / render_big / render
# ---------------------------------------------------------------------------

class TestRenderCompact:
    def test_includes_percent(self):
        view = make_task([("a", "A")])
        view.steps[0].state = StepState.ACTIVE
        view.recompute_percent()
        result = render_compact(view)
        assert "%" in result

    def test_includes_active_label(self):
        view = make_task([("a", "My Step")])
        view.steps[0].state = StepState.ACTIVE
        result = render_compact(view)
        assert "My Step" in result

    def test_done_shows_done_when_no_active(self):
        view = make_task([("a", "A")])
        view.done = True
        view.steps[0].state = StepState.DONE
        result = render_compact(view)
        assert "Done" in result


class TestRenderBig:
    def test_includes_title(self):
        view = make_task([("a", "A")], title="My Task")
        result = render_big(view)
        assert "My Task" in result

    def test_includes_step_label(self):
        view = make_task([("a", "Important Step")])
        result = render_big(view)
        assert "Important Step" in result

    def test_html_escapes_title(self):
        view = make_task([("a", "A")], title="<b>Bold</b>")
        result = render_big(view)
        assert "<b>" in result  # outer bold tag is added by render
        assert "&lt;b&gt;" in result  # inner escape

    def test_html_escapes_step_detail(self):
        view = make_task([("a", "A")])
        view.steps[0].detail = "<script>"
        result = render_big(view)
        assert "&lt;script&gt;" in result


class TestRender:
    def test_expanded_uses_render_big(self):
        view = make_task([("a", "A")], title="T")
        view.expanded = True
        big = render_big(view)
        assert render(view) == big

    def test_collapsed_uses_render_compact(self):
        view = make_task([("a", "A")])
        view.expanded = False
        compact = render_compact(view)
        assert render(view) == compact


# ---------------------------------------------------------------------------
# build_keyboard
# ---------------------------------------------------------------------------

class TestBuildKeyboard:
    def test_has_inline_keyboard_key(self):
        view = make_task([("a", "A")])
        kb = build_keyboard(view)
        assert "inline_keyboard" in kb

    def test_keyboard_is_list_of_rows(self):
        view = make_task([("a", "A")])
        kb = build_keyboard(view)
        assert isinstance(kb["inline_keyboard"], list)

    def test_expanded_shows_hide_details(self):
        view = make_task([("a", "A")])
        view.expanded = True
        kb = build_keyboard(view)
        texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
        assert any("Hide" in t for t in texts)

    def test_collapsed_shows_show_details(self):
        view = make_task([("a", "A")])
        view.expanded = False
        kb = build_keyboard(view)
        texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
        assert any("Show" in t for t in texts)

    def test_running_shows_pause(self):
        view = make_task([("a", "A")])
        view.running = True
        kb = build_keyboard(view)
        texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
        assert any("Pause" in t for t in texts)

    def test_stopped_shows_resume(self):
        view = make_task([("a", "A")])
        view.running = False
        kb = build_keyboard(view)
        texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
        assert any("Resume" in t for t in texts)

    def test_stop_callback(self):
        view = make_task([("a", "A")])
        kb = build_keyboard(view)
        callbacks = [btn["callback_data"] for row in kb["inline_keyboard"] for btn in row]
        assert "task:stop" in callbacks


# ---------------------------------------------------------------------------
# make_task
# ---------------------------------------------------------------------------

class TestMakeTask:
    def test_creates_view(self):
        view = make_task([("a", "A"), ("b", "B")])
        assert isinstance(view, TaskView)

    def test_step_count(self):
        view = make_task([("a", "A"), ("b", "B"), ("c", "C")])
        assert len(view.steps) == 3

    def test_step_keys(self):
        view = make_task([("k1", "Label 1"), ("k2", "Label 2")])
        assert view.steps[0].key == "k1"
        assert view.steps[1].key == "k2"

    def test_custom_title(self):
        view = make_task([], title="Custom Title")
        assert view.title == "Custom Title"

    def test_empty_steps(self):
        view = make_task([])
        assert view.steps == []


# ---------------------------------------------------------------------------
# RateLimiter (decorators.py)
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_first_request(self):
        rl = RateLimiter(max_requests=5, window_minutes=1)
        assert rl.is_allowed(user_id=1) is True

    def test_allows_up_to_max(self):
        rl = RateLimiter(max_requests=3, window_minutes=1)
        for _ in range(3):
            result = rl.is_allowed(user_id=42)
        assert result is True

    def test_blocks_beyond_max(self):
        rl = RateLimiter(max_requests=2, window_minutes=1)
        rl.is_allowed(user_id=7)
        rl.is_allowed(user_id=7)
        assert rl.is_allowed(user_id=7) is False

    def test_different_users_are_independent(self):
        rl = RateLimiter(max_requests=1, window_minutes=1)
        rl.is_allowed(user_id=1)
        assert rl.is_allowed(user_id=2) is True

    def test_old_requests_are_pruned(self):
        """Simulate expired window — old requests should be pruned."""
        from datetime import datetime, timedelta

        rl = RateLimiter(max_requests=2, window_minutes=1)
        uid = 99
        past = datetime.now() - timedelta(minutes=2)
        rl.requests[uid] = [past, past]  # inject old, expired entries
        assert rl.is_allowed(uid) is True  # should now be allowed since old ones pruned

    def test_default_window_is_1_minute(self):
        from datetime import timedelta
        rl = RateLimiter()
        assert rl.window == timedelta(minutes=1)

    def test_default_max_requests_30(self):
        rl = RateLimiter()
        assert rl.max_requests == 30

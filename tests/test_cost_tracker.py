"""
Tests for navig.cost_tracker — per-session LLM cost accumulation.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from navig.cost_tracker import (
    ModelUsage,
    SessionCostTracker,
    SessionSnapshot,
    get_session_tracker,
    reset_session_tracker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker(
    session_id: str = "test-session",
    cfg: dict | None = None,
) -> SessionCostTracker:
    cfg = cfg or {
        "enabled": True,
        "persist": False,
        "history_keep": 10,
        "model_pricing": {
            "gpt-4o": {"input": 2.50, "output": 10.00, "cache_read": 1.25},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075},
            "default": {"input": 0.0, "output": 0.0, "cache_read": 0.0},
        },
    }
    return SessionCostTracker(session_id=session_id, config=cfg)


# ---------------------------------------------------------------------------
# ModelUsage
# ---------------------------------------------------------------------------

class TestModelUsage:
    def test_default_zeros(self):
        u = ModelUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cost_usd == 0.0
        assert u.request_count == 0

    def test_to_dict_roundtrip(self):
        u = ModelUsage(input_tokens=100, output_tokens=50, cost_usd=0.0025, request_count=1)
        d = u.to_dict()
        u2 = ModelUsage.from_dict(d)
        assert u2.input_tokens == 100
        assert u2.output_tokens == 50
        assert u2.request_count == 1


# ---------------------------------------------------------------------------
# SessionCostTracker.record
# ---------------------------------------------------------------------------

class TestSessionCostTrackerRecord:
    def test_record_accumulates(self):
        t = _make_tracker()
        t.record("gpt-4o", input_tokens=1000, output_tokens=200)
        t.record("gpt-4o", input_tokens=500, output_tokens=100)
        inp, out, crd = t.total_tokens()
        assert inp == 1500
        assert out == 300
        assert crd == 0

    def test_record_two_models(self):
        t = _make_tracker()
        t.record("gpt-4o", input_tokens=1000, output_tokens=200)
        t.record("gpt-4o-mini", input_tokens=500, output_tokens=100)
        inp, out, _ = t.total_tokens()
        assert inp == 1500
        assert out == 300

    def test_cost_non_zero_for_known_model(self):
        t = _make_tracker()
        # 1K input tokens at $2.50/K = $2.50; 1K output at $10.00/K = $10.00
        t.record("gpt-4o", input_tokens=1_000, output_tokens=1_000)
        cost = t.total_cost_usd()
        assert cost == pytest.approx(12.50, rel=1e-3)

    def test_cost_zero_for_unknown_model(self):
        t = _make_tracker()
        t.record("some-local-model", input_tokens=5000, output_tokens=1000)
        assert t.total_cost_usd() == 0.0

    def test_cache_read_tokens_accumulated(self):
        t = _make_tracker()
        t.record("gpt-4o", input_tokens=100, output_tokens=50, cache_read_tokens=200)
        _, _, crd = t.total_tokens()
        assert crd == 200

    def test_disabled_tracker_ignores_records(self):
        t = _make_tracker(cfg={"enabled": False, "persist": False})
        t.record("gpt-4o", input_tokens=1000, output_tokens=200)
        assert t.total_cost_usd() == 0.0
        inp, out, _ = t.total_tokens()
        assert inp == 0


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------

class TestFormatSummary:
    def test_no_calls_shows_placeholder(self):
        t = _make_tracker()
        summary = t.format_summary()
        assert "No LLM calls" in summary

    def test_summary_includes_tokens(self):
        t = _make_tracker()
        t.record("gpt-4o", input_tokens=1234, output_tokens=567)
        summary = t.format_summary()
        assert "1,234" in summary
        assert "567" in summary

    def test_per_model_breakdown_shown_for_multiple(self):
        t = _make_tracker()
        t.record("gpt-4o", input_tokens=100, output_tokens=50)
        t.record("gpt-4o-mini", input_tokens=200, output_tokens=80)
        summary = t.format_summary()
        assert "gpt-4o" in summary
        assert "gpt-4o-mini" in summary


# ---------------------------------------------------------------------------
# save / load_history (uses tmp_path fixture)
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_and_load_history(self, tmp_path: Path, monkeypatch):
        # Patch _history_path_static to use tmp dir
        monkeypatch.setattr(
            SessionCostTracker,
            "_history_path_static",
            staticmethod(lambda: tmp_path / "session_costs.jsonl"),
        )
        t = _make_tracker(cfg={
            "enabled": True,
            "persist": True,
            "history_keep": 100,
            "model_pricing": {},
        })
        t.record("gpt-4o-mini", input_tokens=100, output_tokens=50)
        t.save()

        history = SessionCostTracker.load_history(last_n=5)
        assert len(history) == 1
        snap = history[0]
        assert snap.session_id == "test-session"
        assert snap.total_input_tokens == 100
        assert snap.total_output_tokens == 50

    def test_rotation_keeps_last_n(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            SessionCostTracker,
            "_history_path_static",
            staticmethod(lambda: tmp_path / "session_costs.jsonl"),
        )
        # Write 5 sessions with keep=3
        for i in range(5):
            t = SessionCostTracker(
                session_id=f"sess-{i}",
                config={"enabled": True, "persist": True, "history_keep": 3, "model_pricing": {}},
            )
            t.save()

        history = SessionCostTracker.load_history(last_n=10)
        assert len(history) == 3


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_session_tracker_returns_same_instance(self):
        reset_session_tracker()
        t1 = get_session_tracker()
        t2 = get_session_tracker()
        assert t1 is t2

    def test_reset_session_tracker_gives_fresh_instance(self):
        reset_session_tracker()
        t1 = get_session_tracker()
        reset_session_tracker()
        t2 = get_session_tracker()
        assert t1 is not t2

    def teardown_method(self):
        reset_session_tracker()

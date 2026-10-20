"""
Tests for navig.cost_tracker — per-session LLM cost accumulation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from navig.cost_tracker import (
    ModelUsage,
    SessionCostTracker,
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
# Config-string coercion (regression)
# ---------------------------------------------------------------------------

class TestConfigStringCoercion:
    """`navig config set cost_tracker.<flag> false` stores the STRING "false", which is
    truthy — so a raw `cfg.get("enabled", True)` kept tracking/persisting despite the
    kill-switch. Every boolean gate now runs through navig.core.coerce.coerce_bool."""

    @pytest.mark.parametrize("falsey", ["false", "False", "off", "OFF", "no", "0", ""])
    def test_string_disabled_stops_recording(self, falsey):
        t = _make_tracker(cfg={"enabled": falsey, "persist": False})
        t.record("gpt-4o", input_tokens=1000, output_tokens=200)
        assert t.total_cost_usd() == 0.0
        assert t.total_tokens() == (0, 0, 0)

    @pytest.mark.parametrize("truthy", ["true", "on", "yes", "1"])
    def test_string_enabled_still_records(self, truthy):
        t = _make_tracker(cfg={
            "enabled": truthy,
            "persist": False,
            "model_pricing": {"default": {"input": 0.0, "output": 0.0, "cache_read": 0.0}},
        })
        t.record("gpt-4o", input_tokens=1000, output_tokens=200)
        inp, out, _ = t.total_tokens()
        assert (inp, out) == (1000, 200)

    def test_string_persist_false_skips_save(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            SessionCostTracker,
            "_history_path_static",
            staticmethod(lambda: tmp_path / "session_costs.jsonl"),
        )
        t = _make_tracker(cfg={
            "enabled": True,
            "persist": "false",  # the string footgun — must NOT persist
            "history_keep": 10,
            "model_pricing": {},
        })
        t.record("gpt-4o-mini", input_tokens=100, output_tokens=50)
        t.save()
        assert not (tmp_path / "session_costs.jsonl").exists()

    def test_get_session_tracker_honors_string_disabled(self, monkeypatch):
        import navig.cost_tracker as ct

        monkeypatch.setattr(ct, "_load_tracker_config", lambda: {"enabled": "false"})
        reset_session_tracker()
        tracker = get_session_tracker()
        assert tracker.session_id == "disabled"

    def teardown_method(self):
        reset_session_tracker()


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


class TestExitFlush:
    """`record()` ran on every LLM request but nothing ever called `save()`, so each
    session's cost vanished at exit and `navig cost history` read a file that was
    never written. get_session_tracker() now registers a one-shot exit flush."""

    def _capture_atexit(self, monkeypatch):
        import atexit

        hooks: list = []
        monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: hooks.append(fn) or fn)
        return hooks

    def test_a_session_with_usage_is_persisted_at_exit(self, tmp_path: Path, monkeypatch):
        import navig.cost_tracker as ct

        history = tmp_path / "session_costs.jsonl"
        monkeypatch.setattr(ct.SessionCostTracker, "_history_path_static", staticmethod(lambda: history))
        monkeypatch.setattr(ct, "_load_tracker_config", lambda: {})
        hooks = self._capture_atexit(monkeypatch)
        reset_session_tracker()

        tracker = get_session_tracker()
        tracker.record(model="gpt-4o", input_tokens=150, output_tokens=80)
        assert len(hooks) == 1, f"expected one exit flush registered, got {len(hooks)}"
        assert not history.exists(), "nothing should be written before exit"

        hooks[0]()  # simulate interpreter exit
        loaded = ct.SessionCostTracker.load_history(last_n=5)
        assert len(loaded) == 1, f"history after exit flush: {loaded}"
        assert loaded[0].session_id == tracker.session_id
        assert loaded[0].total_input_tokens == 150

    def test_a_session_with_no_usage_writes_nothing(self, tmp_path: Path, monkeypatch):
        import navig.cost_tracker as ct

        history = tmp_path / "session_costs.jsonl"
        monkeypatch.setattr(ct.SessionCostTracker, "_history_path_static", staticmethod(lambda: history))
        monkeypatch.setattr(ct, "_load_tracker_config", lambda: {})
        hooks = self._capture_atexit(monkeypatch)
        reset_session_tracker()

        get_session_tracker()
        assert len(hooks) == 1
        hooks[0]()
        assert not history.exists(), "an idle session must not append an empty snapshot"

    def test_the_flush_writes_where_the_path_pointed_at_creation(self, tmp_path: Path, monkeypatch):
        """atexit runs after a test session restores its environment; an exit-time
        lookup would append the suite's costs to the operator's REAL history."""
        import navig.cost_tracker as ct

        at_creation = tmp_path / "isolated" / "session_costs.jsonl"
        later = tmp_path / "real-home" / "session_costs.jsonl"
        current = {"path": at_creation}
        monkeypatch.setattr(
            ct.SessionCostTracker, "_history_path_static", staticmethod(lambda: current["path"])
        )
        monkeypatch.setattr(ct, "_load_tracker_config", lambda: {})
        hooks = self._capture_atexit(monkeypatch)
        reset_session_tracker()

        get_session_tracker().record(model="gpt-4o", input_tokens=10, output_tokens=5)
        current["path"] = later  # the environment changed before exit
        hooks[0]()

        assert at_creation.is_file(), "flush must use the path pinned at tracker creation"
        assert not later.exists(), "flush followed the exit-time path — would hit the real home"

    def test_a_disabled_tracker_registers_no_flush(self, monkeypatch):
        import navig.cost_tracker as ct

        monkeypatch.setattr(ct, "_load_tracker_config", lambda: {"enabled": False})
        hooks = self._capture_atexit(monkeypatch)
        reset_session_tracker()
        get_session_tracker()
        assert hooks == [], "a disabled tracker must not schedule any write"

    def teardown_method(self):
        reset_session_tracker()


class TestCanonicalPricing:
    """The pricing table in config/defaults.yaml never reached the tracker
    (`cm.get("cost_tracker")` returns None — package defaults are not merged), so
    with no operator table every session cost read $0.00. The tracker now falls
    back to the agent's maintained PRICE_TABLE; an operator table still wins."""

    def test_no_operator_table_uses_the_agent_price_table(self):
        from navig.agent.usage_tracker import _lookup_price
        from navig.cost_tracker import SessionCostTracker

        t = SessionCostTracker("canon", config={})
        t.record("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
        per_m_in, per_m_out, _, _ = _lookup_price("gpt-4o")
        assert per_m_in > 0, "precondition: the agent table prices gpt-4o"
        assert t.total_cost_usd() == pytest.approx(per_m_in + per_m_out, rel=1e-6), (
            f"session tracker priced 1M+1M gpt-4o at {t.total_cost_usd()}, "
            f"agent table says {per_m_in + per_m_out}"
        )

    def test_an_operator_table_still_wins(self):
        from navig.cost_tracker import SessionCostTracker

        cfg = {"model_pricing": {"gpt-4o": {"input": 1.0, "output": 1.0, "cache_read": 0.0}}}
        t = SessionCostTracker("op", config=cfg)
        t.record("gpt-4o", input_tokens=1_000, output_tokens=1_000)
        assert t.total_cost_usd() == pytest.approx(2.0)

    def test_an_unknown_model_is_still_free(self):
        from navig.cost_tracker import SessionCostTracker

        t = SessionCostTracker("unk", config={})
        t.record("some-local-model", input_tokens=5000, output_tokens=1000)
        assert t.total_cost_usd() == 0.0

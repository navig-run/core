"""
Hermetic unit tests for navig.agent.usage_tracker

Covers:
- PRICE_TABLE structure
- _lookup_price (exact, prefix, unknown)
- UsageEvent: total_tokens, cost_usd
- SessionCost: summary_str
- CostTracker: record, session_cost, clear
"""

import pytest

from navig.agent.usage_tracker import (
    PRICE_TABLE,
    CostTracker,
    SessionCost,
    UsageEvent,
    _lookup_price,
)


# ─────────────────────────────────────────────────────────────
# PRICE_TABLE
# ─────────────────────────────────────────────────────────────


class TestPriceTable:
    def test_contains_gpt4o(self):
        assert "gpt-4o" in PRICE_TABLE

    def test_contains_claude_sonnet(self):
        assert "claude-3-5-sonnet-20241022" in PRICE_TABLE

    def test_each_entry_has_four_values(self):
        for model, prices in PRICE_TABLE.items():
            assert len(prices) == 4, f"Expected 4 prices for {model}, got {len(prices)}"

    def test_prices_are_float_or_int(self):
        for model, prices in PRICE_TABLE.items():
            for p in prices:
                assert isinstance(p, (int, float)), f"Non-numeric price for {model}: {p}"


# ─────────────────────────────────────────────────────────────
# _lookup_price
# ─────────────────────────────────────────────────────────────


class TestLookupPrice:
    def test_exact_match_gpt4o(self):
        inp, out, cr, cw = _lookup_price("gpt-4o")
        assert inp == 2.50
        assert out == 10.00

    def test_exact_match_claude_haiku(self):
        inp, out, _, _ = _lookup_price("claude-3-haiku-20240307")
        assert inp == 0.25

    def test_unknown_model_returns_zeros(self):
        result = _lookup_price("mystery-model-9999")
        assert result == (0.0, 0.0, 0.0, 0.0)

    def test_returns_tuple_of_four(self):
        prices = _lookup_price("gpt-4o")
        assert len(prices) == 4


# ─────────────────────────────────────────────────────────────
# UsageEvent
# ─────────────────────────────────────────────────────────────


class TestUsageEvent:
    def _make(self, **kwargs):
        defaults = dict(turn=1, model="gpt-4o", provider="openai")
        defaults.update(kwargs)
        return UsageEvent(**defaults)

    def test_total_tokens_zero(self):
        assert self._make().total_tokens == 0

    def test_total_tokens_sum(self):
        e = self._make(prompt_tokens=1000, completion_tokens=200)
        assert e.total_tokens == 1200

    def test_cost_usd_zero_for_zero_tokens(self):
        assert self._make().cost_usd() == 0.0

    def test_cost_usd_positive(self):
        # gpt-4o: input=2.50/M, output=10/M
        # 1M input → $2.50
        e = self._make(prompt_tokens=1_000_000, completion_tokens=0)
        assert e.cost_usd() == pytest.approx(2.50, abs=0.001)

    def test_cost_usd_output_tokens(self):
        # gpt-4o: 1M output → $10.00
        e = self._make(prompt_tokens=0, completion_tokens=1_000_000)
        assert e.cost_usd() == pytest.approx(10.00, abs=0.001)

    def test_cost_usd_unknown_model_zero(self):
        e = self._make(model="unknown-model", prompt_tokens=999_999)
        assert e.cost_usd() == 0.0

    def test_metadata_defaults_empty(self):
        assert self._make().metadata == {}

    def test_cache_tokens_default_zero(self):
        e = self._make()
        assert e.cache_read_tokens == 0
        assert e.cache_write_tokens == 0


# ─────────────────────────────────────────────────────────────
# SessionCost
# ─────────────────────────────────────────────────────────────


class TestSessionCost:
    def _make_event(self, turn=1, model="gpt-4o"):
        return UsageEvent(turn=turn, model=model, provider="openai",
                          prompt_tokens=100, completion_tokens=50)

    def test_summary_str_single_turn(self):
        ev = self._make_event()
        sc = SessionCost(total_usd=0.001, total_tokens=150, events=[ev])
        s = sc.summary_str()
        assert "1 turn" in s
        assert "150" in s
        assert "$" in s

    def test_summary_str_plural_turns(self):
        events = [self._make_event(t) for t in range(1, 4)]
        sc = SessionCost(total_usd=0.005, total_tokens=450, events=events)
        assert "3 turns" in sc.summary_str()

    def test_summary_str_format(self):
        ev = self._make_event()
        sc = SessionCost(total_usd=0.0120, total_tokens=1800, events=[ev])
        s = sc.summary_str()
        assert "·" in s

    def test_detailed_str_no_events(self):
        sc = SessionCost(total_usd=0.0, total_tokens=0)
        assert "No LLM calls" in sc.detailed_str()

    def test_detailed_str_with_events(self):
        ev = self._make_event()
        sc = SessionCost(total_usd=0.001, total_tokens=150, events=[ev])
        text = sc.detailed_str()
        assert "Turn 1" in text
        assert "gpt-4o" in text


# ─────────────────────────────────────────────────────────────
# CostTracker
# ─────────────────────────────────────────────────────────────


class TestCostTracker:
    def _event(self, turn=1, model="gpt-4o", prompt=100, completion=50):
        return UsageEvent(
            turn=turn, model=model, provider="openai",
            prompt_tokens=prompt, completion_tokens=completion,
        )

    def test_empty_session_cost(self):
        tracker = CostTracker()
        sc = tracker.session_cost()
        assert sc.total_tokens == 0
        assert sc.total_usd == 0.0
        assert sc.events == []

    def test_record_accumulates(self):
        tracker = CostTracker()
        tracker.record(self._event(turn=1, prompt=1000, completion=200))
        sc = tracker.session_cost()
        assert sc.total_tokens == 1200
        assert len(sc.events) == 1

    def test_record_multiple_events(self):
        tracker = CostTracker()
        tracker.record(self._event(turn=1, prompt=500, completion=100))
        tracker.record(self._event(turn=2, prompt=500, completion=100))
        sc = tracker.session_cost()
        assert sc.total_tokens == 1200
        assert len(sc.events) == 2

    def test_session_cost_total_usd_sum(self):
        tracker = CostTracker()
        # gpt-4o: 1M input = $2.50, so 1000 input = $0.0025
        tracker.record(self._event(turn=1, prompt=1000, completion=0))
        tracker.record(self._event(turn=2, prompt=1000, completion=0))
        sc = tracker.session_cost()
        assert sc.total_usd == pytest.approx(0.005, abs=0.0001)

    def test_session_cost_returns_copy(self):
        tracker = CostTracker()
        tracker.record(self._event())
        sc1 = tracker.session_cost()
        sc2 = tracker.session_cost()
        assert sc1 is not sc2  # Different SessionCost objects

    def test_reset_clears(self):
        tracker = CostTracker()
        tracker.record(self._event())
        tracker.reset()
        assert tracker.session_cost().total_tokens == 0

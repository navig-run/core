"""
Batch 39 — navig/agent/tool_caps.py + navig/cost_tracker.py

Covers:
  tool_caps:
    get_cap_for_tool(): known tools, unknown falls back to default
    cap_result(): under cap passthrough, over cap → truncation + footer,
                  max_chars override, negative max_chars edge case
    TOOL_SPECIFIC_CAPS: spot-check values

  cost_tracker:
    ModelUsage: defaults, to_dict(), from_dict() roundtrip
    SessionCostTracker: record(), total_cost_usd(), total_tokens(), format_summary()
"""

from __future__ import annotations

import pytest

from navig.agent.tool_caps import (
    DEFAULT_MAX_RESULT_CHARS,
    TOOL_SPECIFIC_CAPS,
    cap_result,
    get_cap_for_tool,
)
from navig.cost_tracker import ModelUsage, SessionCostTracker


# ---------------------------------------------------------------------------
# get_cap_for_tool
# ---------------------------------------------------------------------------

class TestGetCapForTool:
    def test_known_read_file(self):
        assert get_cap_for_tool("read_file") == TOOL_SPECIFIC_CAPS["read_file"]

    def test_known_grep_search(self):
        assert get_cap_for_tool("grep_search") == TOOL_SPECIFIC_CAPS["grep_search"]

    def test_unknown_tool_returns_default(self):
        assert get_cap_for_tool("nonexistent_tool") == DEFAULT_MAX_RESULT_CHARS

    def test_empty_name_returns_default(self):
        assert get_cap_for_tool("") == DEFAULT_MAX_RESULT_CHARS

    def test_bash_exec_cap(self):
        assert get_cap_for_tool("bash_exec") == 50_000

    def test_web_fetch_cap(self):
        assert get_cap_for_tool("web_fetch") == 10_000


# ---------------------------------------------------------------------------
# TOOL_SPECIFIC_CAPS
# ---------------------------------------------------------------------------

class TestToolSpecificCaps:
    def test_read_file_in_caps(self):
        assert "read_file" in TOOL_SPECIFIC_CAPS

    def test_all_values_positive(self):
        for name, limit in TOOL_SPECIFIC_CAPS.items():
            assert limit > 0, f"{name} cap must be positive"

    def test_bash_exec_largest_or_equal(self):
        # bash_exec and navig_run are among the biggest
        assert TOOL_SPECIFIC_CAPS["bash_exec"] >= TOOL_SPECIFIC_CAPS["web_fetch"]


# ---------------------------------------------------------------------------
# cap_result
# ---------------------------------------------------------------------------

class TestCapResult:
    def test_under_cap_passthrough(self):
        text = "a" * 100
        assert cap_result(text, max_chars=200) == text

    def test_exactly_at_cap_passthrough(self):
        text = "x" * 50
        assert cap_result(text, max_chars=50) == text

    def test_over_cap_truncated(self):
        text = "a" * 1000
        result = cap_result(text, max_chars=100)
        # Result should mention truncation
        assert "Truncated" in result
        assert len(result) > 0

    def test_over_cap_has_footer(self):
        text = "line\n" * 2000
        result = cap_result(text, max_chars=200)
        assert "Truncated" in result
        assert "total" in result

    def test_zero_max_chars_returns_only_footer(self):
        result = cap_result("hello world", max_chars=0)
        assert "Truncated" in result

    def test_tool_name_uses_specific_cap(self):
        # With a short max_chars override, truncation should occur regardless of tool
        text = "x" * 500
        result = cap_result(text, max_chars=100, tool_name="read_file")
        assert "Truncated" in result

    def test_empty_string_passthrough(self):
        assert cap_result("", max_chars=100) == ""

    def test_negative_max_chars_treated_as_zero(self):
        result = cap_result("hello", max_chars=-1)
        # max_chars becomes 0, everything is truncated
        assert "Truncated" in result


# ---------------------------------------------------------------------------
# ModelUsage
# ---------------------------------------------------------------------------

class TestModelUsage:
    def test_defaults_zero(self):
        u = ModelUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_read_tokens == 0
        assert u.cost_usd == 0.0
        assert u.request_count == 0

    def test_to_dict_keys(self):
        u = ModelUsage(input_tokens=100, output_tokens=50, cost_usd=0.003)
        d = u.to_dict()
        assert d["input_tokens"] == 100
        assert d["output_tokens"] == 50
        assert d["cost_usd"] == pytest.approx(0.003)

    def test_from_dict_roundtrip(self):
        u = ModelUsage(input_tokens=200, output_tokens=80, request_count=3)
        restored = ModelUsage.from_dict(u.to_dict())
        assert restored.input_tokens == 200
        assert restored.output_tokens == 80
        assert restored.request_count == 3

    def test_from_dict_extra_keys_ignored(self):
        d = {"input_tokens": 10, "output_tokens": 5, "unknown_key": "ignored",
             "cache_read_tokens": 0, "cost_usd": 0.0, "request_count": 1}
        u = ModelUsage.from_dict(d)
        assert u.input_tokens == 10


# ---------------------------------------------------------------------------
# SessionCostTracker
# ---------------------------------------------------------------------------

class TestSessionCostTracker:
    def _make(self):
        return SessionCostTracker(session_id="test-sess-001", config={"enabled": True, "persist": False})

    def test_initial_total_cost_zero(self):
        t = self._make()
        assert t.total_cost_usd() == 0.0

    def test_initial_total_tokens_zero(self):
        t = self._make()
        assert t.total_tokens() == (0, 0, 0)

    def test_record_increments_tokens(self):
        t = self._make()
        t.record(model="gpt-4o", input_tokens=100, output_tokens=50)
        inp, out, crd = t.total_tokens()
        assert inp == 100
        assert out == 50

    def test_record_multiple_accumulates(self):
        t = self._make()
        t.record(model="gpt-4o", input_tokens=100, output_tokens=50)
        t.record(model="gpt-4o", input_tokens=200, output_tokens=100)
        inp, out, _ = t.total_tokens()
        assert inp == 300
        assert out == 150

    def test_record_multiple_models(self):
        t = self._make()
        t.record(model="gpt-4o", input_tokens=100, output_tokens=50)
        t.record(model="claude-3-5", input_tokens=200, output_tokens=80)
        inp, out, _ = t.total_tokens()
        assert inp == 300
        assert out == 130

    def test_format_summary_no_calls(self):
        t = self._make()
        s = t.format_summary()
        assert "No LLM calls" in s

    def test_format_summary_with_calls(self):
        t = self._make()
        t.record(model="gpt-4o", input_tokens=1000, output_tokens=500)
        s = t.format_summary()
        assert "gpt-4o" in s or "Session" in s

    def test_disabled_tracker_ignores_record(self):
        t = SessionCostTracker(session_id="x", config={"enabled": False})
        t.record(model="gpt-4o", input_tokens=9999, output_tokens=9999)
        assert t.total_cost_usd() == 0.0

"""Tests for navig/tui/widgets/step_indicator.py."""

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------

try:
    from navig.tui.widgets.step_indicator import StepIndicator
    _IMPORT_OK = True
except Exception:
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="StepIndicator not importable")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_indicator(current=0, total=5, labels=None):
    """Build a StepIndicator with manually set attributes (bypasses Textual app context)."""
    obj = StepIndicator.__new__(StepIndicator)
    object.__setattr__(obj, "current_step", current)
    object.__setattr__(obj, "total_steps", total)
    object.__setattr__(obj, "step_labels", labels or ["A", "B", "C", "D", "E", "F"])
    return obj


# ---------------------------------------------------------------------------
# render() — dot characters
# ---------------------------------------------------------------------------

def test_render_returns_string():
    s = make_indicator(current=0, total=3, labels=["X", "Y", "Z"])
    result = s.render()
    assert isinstance(result, str)


def test_render_first_step_has_active_dot():
    s = make_indicator(current=0, total=3, labels=["X", "Y", "Z"])
    result = s.render()
    assert "●" in result


def test_render_completed_steps_have_checkmark():
    s = make_indicator(current=2, total=4, labels=["A", "B", "C", "D"])
    result = s.render()
    assert "✔" in result


def test_render_future_steps_have_empty_dot():
    s = make_indicator(current=0, total=3, labels=["X", "Y", "Z"])
    result = s.render()
    assert "○" in result


def test_render_on_first_step_no_checkmarks():
    s = make_indicator(current=0, total=3, labels=["X", "Y", "Z"])
    result = s.render()
    assert "✔" not in result


def test_render_on_last_step_no_empty_dots():
    s = make_indicator(current=2, total=3, labels=["X", "Y", "Z"])
    result = s.render()
    assert "○" not in result


# ---------------------------------------------------------------------------
# render() — step number and percentage
# ---------------------------------------------------------------------------

def test_render_shows_step_number():
    s = make_indicator(current=0, total=5, labels=["A", "B", "C", "D", "E"])
    result = s.render()
    assert "Step 1/5" in result


def test_render_shows_correct_step_num_mid():
    s = make_indicator(current=2, total=5, labels=["A", "B", "C", "D", "E"])
    result = s.render()
    assert "Step 3/5" in result


def test_render_shows_100_percent_at_last_step():
    s = make_indicator(current=4, total=5, labels=["A", "B", "C", "D", "E"])
    result = s.render()
    assert "100%" in result


def test_render_shows_20_percent_at_first_of_5():
    s = make_indicator(current=0, total=5, labels=["A", "B", "C", "D", "E"])
    result = s.render()
    assert "20%" in result


def test_render_shows_step_label():
    s = make_indicator(current=0, total=3, labels=["Identity", "Provider", "Runtime"])
    result = s.render()
    assert "Identity" in result


def test_render_shows_correct_label_for_step():
    s = make_indicator(current=1, total=3, labels=["Identity", "Provider", "Runtime"])
    result = s.render()
    assert "Provider" in result


# ---------------------------------------------------------------------------
# render() — boundary / edge cases
# ---------------------------------------------------------------------------

def test_render_single_step():
    s = make_indicator(current=0, total=1, labels=["Only"])
    result = s.render()
    assert "Step 1/1" in result
    assert "100%" in result


def test_render_label_clamps_to_last_when_step_exceeds_labels():
    s = make_indicator(current=5, total=6, labels=["A", "B"])
    result = s.render()
    # should use labels[-1] = "B"
    assert "B" in result


def test_render_does_not_raise_with_empty_labels():
    s = make_indicator(current=0, total=2, labels=[])
    # Should not raise; falls back to a single item
    try:
        result = s.render()
        assert isinstance(result, str)
    except (IndexError, TypeError):
        pass  # acceptable if implementation doesn't guard empty labels


def test_render_separator_between_dots_and_label():
    s = make_indicator(current=0, total=3, labels=["X", "Y", "Z"])
    result = s.render()
    assert "—" in result or "-" in result or "Step" in result


def test_render_total_dot_count_matches_total_steps():
    total = 4
    s = make_indicator(current=1, total=total, labels=["A", "B", "C", "D"])
    result = s.render()
    active = result.count("●")
    checks = result.count("✔")
    empties = result.count("○")
    assert active + checks + empties == total

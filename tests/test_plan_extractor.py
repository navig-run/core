"""
Tests for PlanExtractor, PlanValidator, and related typed plan schema.

Covers the three-strategy extraction pipeline, typed validation rules,
coercion behaviour, and the quality scorer.
"""

import pytest

from navig.agent.conv.planner import (
    ACTIONS_REQUIRING_PARAMS,
    KNOWN_ACTIONS,
    PlanExtractor,
    PlanValidator,
    ValidatedPlan,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_valid_step(
    action: str = "auto.click",
    description: str = "Click the button",
    params: dict | None = None,
    confirmation_needed: bool = False,
) -> dict:
    """Return a minimal valid step dict."""
    return {
        "action": action,
        "description": description,
        "params": params if params is not None else {},
        "confirmation_needed": confirmation_needed,
    }


def _wrap_plan(steps: list) -> dict:
    """Wrap a list of steps into a plan envelope."""
    return {"plan": steps}


# ============================================================================
# TestPlanExtractor — three-strategy extraction pipeline
# ============================================================================


class TestPlanExtractor:
    """Tests for PlanExtractor.extract() and _extract_plan()."""

    @pytest.mark.unit
    def test_fence_valid_json_returns_validated_plan(self) -> None:
        """Strategy 1: valid JSON inside a ```json fence returns a ValidatedPlan."""
        raw = (
            "Here is your plan:\n"
            "```json\n"
            '{"plan": [{"action": "auto.open_app", "description": "Open Chrome",'
            ' "params": {"target": "chrome"}, "confirmation_needed": false}]}\n'
            "```\n"
        )
        extractor = PlanExtractor()
        result = extractor._extract_plan(raw)

        assert result is not None
        assert len(result["plan"]) == 1
        assert result["plan"][0]["action"] == "auto.open_app"
        assert result["plan"][0]["params"] == {"target": "chrome"}

    @pytest.mark.unit
    def test_brace_no_fence_returns_validated_plan(self) -> None:
        """Strategy 2: plain JSON with outer braces (no fence) returns a ValidatedPlan."""
        raw = (
            '{"plan": [{"action": "auto.type", "description": "Type hello",'
            ' "params": {"text": "hello"}, "confirmation_needed": false}]}'
        )
        extractor = PlanExtractor()
        result = extractor._extract_plan(raw)

        assert result is not None
        assert result["plan"][0]["action"] == "auto.type"
        assert result["plan"][0]["description"] == "Type hello"

    @pytest.mark.unit
    def test_repair_truncated_json_returns_validated_plan(self) -> None:
        """Strategy 3: truncated JSON repaired by json-repair returns a ValidatedPlan."""
        # No closing braces — simulates a truncated LLM response.
        raw = (
            '{"plan": [{"action": "auto.click", "description": "click at target",'
            ' "params": {}, "confirmation_needed": false'
        )
        extractor = PlanExtractor()
        result = extractor._extract_plan(raw)

        # json-repair should close the open structures; validation must succeed.
        assert result is not None
        assert result["plan"][0]["action"] == "auto.click"
        assert result["plan"][0]["confirmation_needed"] is False

    @pytest.mark.unit
    def test_non_json_string_returns_none(self) -> None:
        """Completely non-JSON prose returns None without raising."""
        raw = "please open chrome for me and then play some music"
        extractor = PlanExtractor()
        result = extractor._extract_plan(raw)

        assert result is None


# ============================================================================
# TestPlanValidator — structural validation and quality scoring
# ============================================================================


class TestPlanValidator:
    """Tests for PlanValidator.validate() and PlanValidator.score()."""

    @pytest.mark.unit
    def test_missing_plan_key_returns_none(self) -> None:
        """A dict without a top-level 'plan' key fails validation."""
        data = {"steps": [_make_valid_step()]}
        result = PlanValidator.validate(data)

        assert result is None

    @pytest.mark.unit
    def test_step_missing_action_returns_none(self) -> None:
        """A step that has no 'action' field fails validation."""
        step_without_action = {
            "description": "Do something",
            "params": {},
            "confirmation_needed": False,
        }
        data = _wrap_plan([step_without_action])
        result = PlanValidator.validate(data)

        assert result is None

    @pytest.mark.unit
    def test_step_missing_params_coerced_to_empty_dict(self) -> None:
        """A step without 'params' is coerced to {} — does NOT return None."""
        step_no_params = {
            "action": "auto.scroll",
            "description": "Scroll down",
            "confirmation_needed": False,
            # 'params' intentionally omitted
        }
        data = _wrap_plan([step_no_params])
        result = PlanValidator.validate(data)

        assert result is not None
        assert result["plan"][0]["params"] == {}

    @pytest.mark.unit
    def test_confirmation_needed_as_string_returns_none(self) -> None:
        """A step where confirmation_needed is the string 'true' fails validation."""
        step_bad_bool = {
            "action": "auto.click",
            "description": "Click OK",
            "params": {},
            "confirmation_needed": "true",  # string, not bool
        }
        data = _wrap_plan([step_bad_bool])
        result = PlanValidator.validate(data)

        assert result is None

    @pytest.mark.unit
    def test_score_perfect_plan_returns_one(self) -> None:
        """A plan with a known action, non-empty description, and params returns 1.0."""
        # 'auto.type' is in ACTIONS_REQUIRING_PARAMS — params must be non-empty.
        assert "auto.type" in KNOWN_ACTIONS
        assert "auto.type" in ACTIONS_REQUIRING_PARAMS

        plan: ValidatedPlan = {
            "plan": [
                {
                    "action": "auto.type",
                    "description": "Type the greeting text",
                    "params": {"text": "hello world"},
                    "confirmation_needed": False,
                }
            ]
        }
        assert PlanValidator.score(plan) == 1.0

    @pytest.mark.unit
    def test_score_penalized_plan_is_at_most_zero_point_three(self) -> None:
        """Unknown action (+0.4) and empty description (+0.3) → score == 0.3."""
        plan: ValidatedPlan = {
            "plan": [
                {
                    "action": "unknown_xyz_action_abc",  # not in KNOWN_ACTIONS
                    "description": "   ",  # whitespace-only → empty penalty
                    "params": {},
                    "confirmation_needed": False,
                }
            ]
        }
        # total_penalty = 0.4 + 0.3 = 0.7 for 1 step → score ≈ 0.3 (float arithmetic)
        score = PlanValidator.score(plan)
        assert score == pytest.approx(0.3)
        assert score <= 0.3 + 1e-9  # guard against float epsilon drift

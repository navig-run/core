"""Tests for navig.core.evolution.base — BaseEvolver, EvolutionResult."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from navig.core.evolution.base import BaseEvolver, EvolutionResult


# ---------------------------------------------------------------------------
# Concrete implementations for testing
# ---------------------------------------------------------------------------

class AlwaysSuccessEvolver(BaseEvolver):
    """Evolver that generates and validates successfully on first attempt."""

    def _generate(self, goal, previous_artifact, error, context):
        return f"artifact-for-{goal}"

    def _validate(self, artifact, context):
        return None  # No error


class AlwaysFailEvolver(BaseEvolver):
    """Evolver that always fails validation."""

    def _generate(self, goal, previous_artifact, error, context):
        return f"bad-artifact-for-{goal}"

    def _validate(self, artifact, context):
        return "always invalid"


class EmptyGeneratorEvolver(BaseEvolver):
    """Evolver whose generator returns falsy (empty string)."""

    def _generate(self, goal, previous_artifact, error, context):
        return ""

    def _validate(self, artifact, context):
        return None


class RaisingGeneratorEvolver(BaseEvolver):
    """Evolver whose generator raises on the first call, succeeds thereafter."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._calls = 0

    def _generate(self, goal, previous_artifact, error, context):
        self._calls += 1
        if self._calls == 1:
            raise ValueError("generation error")
        return "ok-artifact"

    def _validate(self, artifact, context):
        return None


class CountingEvolver(BaseEvolver):
    """Evolver that records validate call count before succeeding."""

    def __init__(self, fail_times=1, **kwargs):
        super().__init__(**kwargs)
        self.fail_times = fail_times
        self._attempts = 0

    def _generate(self, goal, previous_artifact, error, context):
        self._attempts += 1
        return f"artifact-v{self._attempts}"

    def _validate(self, artifact, context):
        if self._attempts <= self.fail_times:
            return f"fail#{self._attempts}"
        return None


# ---------------------------------------------------------------------------
# EvolutionResult dataclass
# ---------------------------------------------------------------------------

class TestEvolutionResult:
    def test_success_result(self):
        r = EvolutionResult(success=True, artifact="x", attempts=1)
        assert r.success is True
        assert r.artifact == "x"
        assert r.attempts == 1

    def test_failure_result(self):
        r = EvolutionResult(success=False, error="bad", attempts=3)
        assert r.success is False
        assert r.error == "bad"

    def test_defaults(self):
        r = EvolutionResult(success=True)
        assert r.artifact is None
        assert r.error == ""
        assert r.history is None
        assert r.attempts == 0

    def test_history_field(self):
        r = EvolutionResult(success=True, history=["a", "b"])
        assert r.history == ["a", "b"]


# ---------------------------------------------------------------------------
# BaseEvolver.evolve — happy path
# ---------------------------------------------------------------------------

class TestBaseEvolverEvolveSuccess:
    def test_success_result_has_success_true(self):
        evolver = AlwaysSuccessEvolver()
        result = evolver.evolve("my goal")
        assert result.success is True

    def test_success_result_has_artifact(self):
        evolver = AlwaysSuccessEvolver()
        result = evolver.evolve("my goal")
        assert result.artifact == "artifact-for-my goal"

    def test_success_attempts_is_one(self):
        evolver = AlwaysSuccessEvolver()
        result = evolver.evolve("my goal")
        assert result.attempts == 1

    def test_success_history_populated(self):
        evolver = AlwaysSuccessEvolver()
        result = evolver.evolve("my goal")
        assert result.history  # non-empty
        assert "artifact-for-my goal" in result.history[0]

    def test_history_reset_between_calls(self):
        evolver = AlwaysSuccessEvolver()
        evolver.evolve("first")
        result = evolver.evolve("second")
        assert all("second" in h for h in result.history)

    def test_save_called_on_success(self):
        evolver = AlwaysSuccessEvolver()
        evolver._save = MagicMock()
        evolver.evolve("goal")
        evolver._save.assert_called_once_with("goal", "artifact-for-goal")


# ---------------------------------------------------------------------------
# BaseEvolver.evolve — failure path
# ---------------------------------------------------------------------------

class TestBaseEvolverEvolveFailure:
    def test_always_fail_returns_false(self):
        evolver = AlwaysFailEvolver(max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is False

    def test_always_fail_uses_max_retries(self):
        evolver = AlwaysFailEvolver(max_retries=3)
        result = evolver.evolve("goal")
        assert result.attempts == 3

    def test_always_fail_error_from_validator(self):
        evolver = AlwaysFailEvolver(max_retries=2)
        result = evolver.evolve("goal")
        assert "always invalid" in result.error

    def test_always_fail_history_non_empty(self):
        evolver = AlwaysFailEvolver(max_retries=2)
        result = evolver.evolve("goal")
        assert result.history and len(result.history) == 2

    def test_empty_generator_returns_failure(self):
        evolver = EmptyGeneratorEvolver(max_retries=1)
        result = evolver.evolve("goal")
        assert result.success is False
        assert "empty" in result.error.lower()

    def test_raising_generator_returns_failure(self):
        evolver = RaisingGeneratorEvolver(max_retries=1)
        result = evolver.evolve("goal")
        assert result.success is False
        assert "Generation failed" in result.error

    def test_raising_generator_fails_immediately(self):
        # Generation exception causes immediate return (no retry)
        evolver = RaisingGeneratorEvolver(max_retries=2)
        result = evolver.evolve("goal")
        assert result.success is False
        assert result.attempts == 1

    def test_save_not_called_on_failure(self):
        evolver = AlwaysFailEvolver(max_retries=2)
        evolver._save = MagicMock()
        evolver.evolve("goal")
        evolver._save.assert_not_called()


# ---------------------------------------------------------------------------
# BaseEvolver.evolve — retry logic
# ---------------------------------------------------------------------------

class TestBaseEvolverRetry:
    def test_succeeds_after_N_failures(self):
        """Evolver failing once, succeeds on attempt 2."""
        evolver = CountingEvolver(fail_times=1, max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is True
        assert result.attempts == 2

    def test_max_retries_default_is_3(self):
        evolver = AlwaysFailEvolver()
        result = evolver.evolve("goal")
        assert result.attempts == 3

    def test_custom_max_retries(self):
        evolver = AlwaysFailEvolver(max_retries=5)
        result = evolver.evolve("goal")
        assert result.attempts == 5

    def test_history_length_matches_attempts(self):
        evolver = AlwaysFailEvolver(max_retries=4)
        result = evolver.evolve("goal")
        assert len(result.history) == 4

    def test_previous_artifact_passed_to_generate(self):
        calls = []

        class TrackingEvolver(BaseEvolver):
            def _generate(self, goal, previous_artifact, error, context):
                calls.append(previous_artifact)
                return "artifact"

            def _validate(self, artifact, context):
                if len(calls) < 2:
                    return "not yet"
                return None

        evolver = TrackingEvolver(max_retries=3)
        evolver.evolve("goal")
        # First call: previous_artifact is None
        assert calls[0] is None
        # Second call: previous_artifact is "artifact"
        assert calls[1] == "artifact"

    def test_error_message_passed_to_generate(self):
        errors_seen = []

        class ErrorTrackingEvolver(BaseEvolver):
            def _generate(self, goal, previous_artifact, error, context):
                errors_seen.append(error)
                return "artifact"

            def _validate(self, artifact, context):
                if len(errors_seen) < 2:
                    return "validation error msg"
                return None

        evolver = ErrorTrackingEvolver(max_retries=3)
        evolver.evolve("goal")
        assert errors_seen[0] == ""  # First call has no error
        assert "validation error msg" in errors_seen[1]


# ---------------------------------------------------------------------------
# BaseEvolver._check_cache
# ---------------------------------------------------------------------------

class TestBaseEvolverCache:
    def test_cache_hit_skips_generate(self):
        evolver = AlwaysSuccessEvolver()
        evolver._check_cache = MagicMock(return_value="cached-artifact")
        evolver._generate = MagicMock()
        result = evolver.evolve("goal")
        assert result.success is True
        assert result.artifact == "cached-artifact"
        evolver._generate.assert_not_called()

    def test_cache_hit_attempts_is_zero(self):
        evolver = AlwaysSuccessEvolver()
        evolver._check_cache = MagicMock(return_value="cached")
        result = evolver.evolve("goal")
        assert result.attempts == 0

    def test_cache_miss_falls_through(self):
        evolver = AlwaysSuccessEvolver()
        evolver._check_cache = MagicMock(return_value=None)
        result = evolver.evolve("goal")
        assert result.success is True
        assert result.artifact == "artifact-for-goal"

    def test_default_check_cache_returns_none(self):
        evolver = AlwaysSuccessEvolver()
        assert evolver._check_cache("anything") is None


# ---------------------------------------------------------------------------
# BaseEvolver._save default
# ---------------------------------------------------------------------------

class TestBaseEvolverSave:
    def test_default_save_returns_none(self):
        evolver = AlwaysSuccessEvolver()
        assert evolver._save("goal", "artifact") is None


# ---------------------------------------------------------------------------
# BaseEvolver abstract interface
# ---------------------------------------------------------------------------

class TestBaseEvolverInterface:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseEvolver()  # type: ignore

    def test_max_retries_stored(self):
        evolver = AlwaysSuccessEvolver(max_retries=7)
        assert evolver.max_retries == 7

    def test_history_starts_empty(self):
        evolver = AlwaysSuccessEvolver()
        assert evolver.history == []

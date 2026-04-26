"""Tests for navig.core.evolution.base — EvolutionResult, BaseEvolver."""
from __future__ import annotations

from navig.core.evolution.base import BaseEvolver, EvolutionResult


# ---------------------------------------------------------------------------
# EvolutionResult
# ---------------------------------------------------------------------------

class TestEvolutionResult:
    def test_defaults(self) -> None:
        r = EvolutionResult(success=True)
        assert r.artifact is None
        assert r.error == ""
        assert r.history is None
        assert r.attempts == 0

    def test_fields_set(self) -> None:
        r = EvolutionResult(success=False, artifact="x", error="oops", history=["a"], attempts=2)
        assert r.success is False
        assert r.artifact == "x"
        assert r.error == "oops"
        assert r.history == ["a"]
        assert r.attempts == 2


# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------

class _AlwaysPass(BaseEvolver):
    """Generates a string artifact, validates on first try."""

    def _generate(self, goal, previous_artifact, error, context):
        return f"artifact_for_{goal}"

    def _validate(self, artifact, context) -> str | None:
        return None  # always valid


class _AlwaysFail(BaseEvolver):
    """Always returns an artifact that fails validation."""

    def _generate(self, goal, previous_artifact, error, context):
        return "bad_artifact"

    def _validate(self, artifact, context) -> str | None:
        return "validation error"


class _FailOnFirstPass(BaseEvolver):
    """Fails validation once, then passes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._calls = 0

    def _generate(self, goal, previous_artifact, error, context):
        self._calls += 1
        return f"attempt_{self._calls}"

    def _validate(self, artifact, context) -> str | None:
        if self._calls < 2:
            return "not ready yet"
        return None


class _GenerationRaises(BaseEvolver):
    """Raises during _generate."""

    def _generate(self, goal, previous_artifact, error, context):
        raise RuntimeError("generator exploded")

    def _validate(self, artifact, context) -> str | None:
        return None


class _CacheHit(BaseEvolver):
    """Always returns a cached artifact."""

    def _check_cache(self, goal):
        return "cached_result"

    def _generate(self, goal, previous_artifact, error, context):
        return "not_this"

    def _validate(self, artifact, context) -> str | None:
        return None


# ---------------------------------------------------------------------------
# BaseEvolver.evolve
# ---------------------------------------------------------------------------

class TestBaseEvolverEvolve:
    def test_success_on_first_attempt(self) -> None:
        evolver = _AlwaysPass(max_retries=3)
        result = evolver.evolve("test_goal")
        assert result.success is True
        assert result.artifact == "artifact_for_test_goal"
        assert result.attempts == 1

    def test_history_populated_on_success(self) -> None:
        evolver = _AlwaysPass(max_retries=3)
        result = evolver.evolve("goal")
        assert result.history is not None
        assert len(result.history) >= 1

    def test_fails_after_max_retries(self) -> None:
        evolver = _AlwaysFail(max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is False
        assert result.attempts == 3
        assert result.error == "validation error"

    def test_succeeds_on_second_attempt(self) -> None:
        evolver = _FailOnFirstPass(max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is True
        assert result.attempts == 2

    def test_generation_exception_returns_failure(self) -> None:
        evolver = _GenerationRaises(max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is False
        assert "Generation failed" in result.error
        assert result.attempts == 1

    def test_cache_hit_returns_without_generate(self) -> None:
        evolver = _CacheHit(max_retries=3)
        result = evolver.evolve("goal")
        assert result.success is True
        assert result.artifact == "cached_result"
        assert result.attempts == 0

    def test_history_resets_between_calls(self) -> None:
        evolver = _AlwaysFail(max_retries=2)
        evolver.evolve("first")
        assert len(evolver.history) == 2  # 2 failed attempts

        evolver2 = _AlwaysPass(max_retries=3)
        evolver2.evolve("second")
        assert evolver2.history is not None

    def test_returns_failure_on_empty_artifact(self) -> None:
        class EmptyArtifact(BaseEvolver):
            def _generate(self, goal, prev, err, ctx):
                return ""  # falsy

            def _validate(self, artifact, ctx):
                return None

        evolver = EmptyArtifact(max_retries=2)
        result = evolver.evolve("goal")
        assert result.success is False
        assert "empty" in result.error.lower()

    def test_max_retries_respected(self) -> None:
        evolver = _AlwaysFail(max_retries=5)
        result = evolver.evolve("goal")
        assert result.attempts == 5

    def test_default_max_retries_is_three(self) -> None:
        evolver = _AlwaysFail()
        assert evolver.max_retries == 3

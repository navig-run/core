"""
Batch 129 — tests for navig.onboarding.engine and navig.onboarding.steps helpers

Coverage targets:
  engine.py:  _is_click_abort, StepResult, StepRecord, OnboardingStep,
              EngineConfig, EngineState, _verify_always_run
  steps.py:   _WEB_SEARCH_PROVIDER_CATALOG, _tty_check, _detected_sources
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.onboarding.engine import (
    EngineConfig,
    EngineState,
    OnboardingStep,
    StepRecord,
    StepResult,
    _is_click_abort,
    _verify_always_run,
)
from navig.onboarding.steps import (
    _WEB_SEARCH_PROVIDER_CATALOG,
    _detected_sources,
    _tty_check,
)


# ===========================================================================
# _is_click_abort
# ===========================================================================


class TestIsClickAbort:
    def test_returns_false_for_plain_exception(self):
        assert _is_click_abort(ValueError("test")) is False

    def test_returns_false_for_runtime_error(self):
        assert _is_click_abort(RuntimeError("err")) is False

    def test_returns_true_for_click_abort(self):
        try:
            from click.exceptions import Abort
            assert _is_click_abort(Abort()) is True
        except ImportError:
            pytest.skip("click not installed")

    def test_returns_false_when_import_error(self):
        with patch.dict("sys.modules", {"click": None, "click.exceptions": None}):
            # Without click, should return False
            result = _is_click_abort(Exception("x"))
            assert result is False


# ===========================================================================
# StepResult
# ===========================================================================


class TestStepResult:
    def test_completed_status(self):
        r = StepResult(status="completed", output={})
        assert r.status == "completed"

    def test_skipped_status(self):
        r = StepResult(status="skipped", output={"reason": "non-interactive"})
        assert r.status == "skipped"
        assert r.output["reason"] == "non-interactive"

    def test_failed_status(self):
        r = StepResult(status="failed", output={}, error="something failed")
        assert r.status == "failed"
        assert r.error == "something failed"

    def test_defaults(self):
        r = StepResult(status="completed", output={})
        assert r.duration_ms == 0
        assert r.error is None
        assert r.fix_hint is None

    def test_fix_hint_set(self):
        r = StepResult(status="failed", output={}, fix_hint="try again")
        assert r.fix_hint == "try again"

    def test_output_stored(self):
        r = StepResult(status="completed", output={"key": "val"})
        assert r.output["key"] == "val"


# ===========================================================================
# StepRecord
# ===========================================================================


class TestStepRecord:
    def test_basic_fields(self):
        rec = StepRecord(
            id="step-01",
            title="Step One",
            status="completed",
            completed_at="2025-01-01T00:00:00",
            duration_ms=100,
            output={},
        )
        assert rec.id == "step-01"
        assert rec.title == "Step One"
        assert rec.status == "completed"
        assert rec.duration_ms == 100

    def test_error_default_none(self):
        rec = StepRecord(
            id="s", title="t", status="completed",
            completed_at="2025", duration_ms=0, output={}
        )
        assert rec.error is None

    def test_error_set(self):
        rec = StepRecord(
            id="s", title="t", status="failed",
            completed_at="2025", duration_ms=10, output={},
            error="disk full",
        )
        assert rec.error == "disk full"

    def test_output_stored(self):
        rec = StepRecord(
            id="s", title="t", status="completed",
            completed_at="2025", duration_ms=0,
            output={"wrote": "/etc/navig"}
        )
        assert rec.output["wrote"] == "/etc/navig"


# ===========================================================================
# _verify_always_run
# ===========================================================================


class TestVerifyAlwaysRun:
    def test_returns_false(self):
        assert _verify_always_run() is False

    def test_return_type_bool(self):
        assert isinstance(_verify_always_run(), bool)


# ===========================================================================
# OnboardingStep
# ===========================================================================


class TestOnboardingStep:
    def test_basic_fields(self):
        step = OnboardingStep(
            id="test-step",
            title="Test Step",
            run=lambda: StepResult(status="completed", output={}),
        )
        assert step.id == "test-step"
        assert step.title == "Test Step"

    def test_default_phase_bootstrap(self):
        step = OnboardingStep(
            id="s", title="t", run=lambda: StepResult(status="completed", output={})
        )
        assert step.phase == "bootstrap"

    def test_default_tier_essential(self):
        step = OnboardingStep(
            id="s", title="t", run=lambda: StepResult(status="completed", output={})
        )
        assert step.tier == "essential"

    def test_default_on_failure_abort(self):
        step = OnwardingStep = OnboardingStep(
            id="s", title="t", run=lambda: StepResult(status="completed", output={})
        )
        assert step.on_failure == "abort"

    def test_default_independent_false(self):
        step = OnboardingStep(
            id="s", title="t", run=lambda: StepResult(status="completed", output={})
        )
        assert step.independent is False

    def test_run_callable(self):
        result = StepResult(status="completed", output={"done": True})
        step = OnboardingStep(id="s", title="t", run=lambda: result)
        assert step.run() is result

    def test_verify_default_is_callable(self):
        step = OnboardingStep(
            id="s", title="t", run=lambda: StepResult(status="completed", output={})
        )
        assert callable(step.verify)
        assert step.verify() is False


# ===========================================================================
# EngineConfig
# ===========================================================================


class TestEngineConfig:
    def test_required_fields(self, tmp_path):
        cfg = EngineConfig(navig_dir=tmp_path, node_name="mynode")
        assert cfg.navig_dir == tmp_path
        assert cfg.node_name == "mynode"

    def test_defaults(self, tmp_path):
        cfg = EngineConfig(navig_dir=tmp_path, node_name="n")
        assert cfg.dry_run is False
        assert cfg.no_genesis is False
        assert cfg.reset is False
        assert cfg.jump_to_step is None


# ===========================================================================
# EngineState
# ===========================================================================


class TestEngineState:
    def test_defaults(self):
        state = EngineState()
        assert state.node_id == ""
        assert state.started_at == ""
        assert state.completed_at == ""
        assert state.interrupted_at == ""
        assert isinstance(state.steps, list)
        assert len(state.steps) == 0


# ===========================================================================
# steps._WEB_SEARCH_PROVIDER_CATALOG
# ===========================================================================


class TestWebSearchProviderCatalog:
    def test_is_non_empty_tuple(self):
        assert len(_WEB_SEARCH_PROVIDER_CATALOG) > 0

    def test_each_entry_has_three_parts(self):
        for entry in _WEB_SEARCH_PROVIDER_CATALOG:
            assert len(entry) == 3, f"Bad entry: {entry}"

    def test_each_entry_name_is_string(self):
        for name, desc, keys in _WEB_SEARCH_PROVIDER_CATALOG:
            assert isinstance(name, str)

    def test_each_entry_desc_is_string(self):
        for name, desc, keys in _WEB_SEARCH_PROVIDER_CATALOG:
            assert isinstance(desc, str)

    def test_each_entry_keys_is_tuple(self):
        for name, desc, keys in _WEB_SEARCH_PROVIDER_CATALOG:
            assert isinstance(keys, tuple)

    def test_contains_brave(self):
        names = [e[0] for e in _WEB_SEARCH_PROVIDER_CATALOG]
        assert "brave" in names

    def test_contains_perplexity(self):
        names = [e[0] for e in _WEB_SEARCH_PROVIDER_CATALOG]
        assert "perplexity" in names


# ===========================================================================
# steps._tty_check
# ===========================================================================


class TestTtyCheck:
    def test_non_tty_returns_skipped_result(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = _tty_check()
        assert result is not None
        assert result.status == "skipped"

    def test_tty_returns_none(self):
        with patch.object(sys.stdin, "isatty", return_value=True):
            result = _tty_check()
        assert result is None

    def test_skipped_result_has_reason(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = _tty_check()
        assert "reason" in result.output


# ===========================================================================
# steps._detected_sources
# ===========================================================================


class TestDetectedSources:
    def test_returns_dict(self):
        result = _detected_sources()
        assert isinstance(result, dict)

    def test_returns_empty_by_default(self):
        result = _detected_sources()
        assert result == {}

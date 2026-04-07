"""
Tests for OnboardingEngine jump_to_step semantics.

Covers the fix for: [BUG] `navig init --provider` skips ai-provider step
(GitHub issue #79).

Root cause: `_already_completed()` fired for the jump target before the engine
had a chance to carve it out, causing the target step to be silently skipped
when the artifact (onboarding.json) already contained a "completed" record for it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.onboarding.engine import (
    EngineConfig,
    OnboardingEngine,
    OnboardingStep,
    StepResult,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_config(tmp_path: Path, **kwargs) -> EngineConfig:
    return EngineConfig(
        navig_dir=tmp_path,
        node_name="test-node",
        **kwargs,
    )


def _step(step_id: str, title: str = "") -> OnboardingStep:
    return OnboardingStep(
        id=step_id,
        title=title or step_id,
        run=lambda: StepResult(status="completed", output={"ran": step_id}),
        on_failure="skip",
    )


def _write_artifact(tmp_path: Path, completed_ids: list[str]) -> None:
    """Pre-populate onboarding.json as if those steps already ran."""
    records = [
        {
            "id": sid,
            "title": sid,
            "status": "completed",
            "completed_at": "2026-01-01T00:00:00",
            "duration_ms": 1,
            "output": {},
        }
        for sid in completed_ids
    ]
    artifact = {
        "version": 1,
        "node_id": "test-node",
        "started_at": "2026-01-01T00:00:00",
        "completed_at": "",
        "interrupted_at": "",
        "engine_version": "2.0.0",
        "steps": records,
    }
    (tmp_path / "onboarding.json").write_text(
        json.dumps(artifact), encoding="utf-8"
    )


# ── 1. Jump target is re-run even when artifact marks it completed ─────────────


def test_jump_target_reruns_when_artifact_marks_it_completed(tmp_path: Path) -> None:
    """
    If onboarding.json already has ai-provider=completed and we call
    jump_to_step="ai-provider", the step must execute again (not be skipped).
    This is the primary regression for issue #79.
    """
    _write_artifact(tmp_path, ["bootstrap", "ai-provider"])

    ran: list[str] = []

    def _run_target() -> StepResult:
        ran.append("ai-provider")
        return StepResult(status="completed", output={"reconfigured": True})

    steps = [
        _step("bootstrap"),
        OnboardingStep(
            id="ai-provider",
            title="AI Provider",
            run=_run_target,
            on_failure="skip",
        ),
        _step("web-search-provider"),
        _step("review"),
    ]

    config = _make_config(tmp_path, jump_to_step="ai-provider")
    engine = OnboardingEngine(config, steps)
    state = engine.run()

    # ai-provider must have run
    assert "ai-provider" in ran, (
        "`ai-provider` was not executed — engine skipped the jump target. "
        "Bug #79 regression: _already_completed guard must be bypassed for jump_to_step."
    )

    # Check the record reflects this run (not the stale artifact record)
    ai_records = [r for r in state.steps if r.id == "ai-provider"]
    assert ai_records, "No StepRecord written for ai-provider"
    assert ai_records[-1].status == "completed"


# ── 2. Steps BEFORE the jump target are still skipped ────────────────────────


def test_steps_before_jump_target_are_skipped(tmp_path: Path) -> None:
    """Steps listed before jump_to_step should NOT run."""
    _write_artifact(tmp_path, [])

    ran: list[str] = []

    def _track(sid: str):
        def _run() -> StepResult:
            ran.append(sid)
            return StepResult(status="completed", output={})
        return _run

    steps = [
        OnboardingStep(id="step-a", title="A", run=_track("step-a"), on_failure="skip"),
        OnboardingStep(id="step-b", title="B", run=_track("step-b"), on_failure="skip"),
        OnboardingStep(id="step-c", title="C", run=_track("step-c"), on_failure="skip"),
    ]

    config = _make_config(tmp_path, jump_to_step="step-b")
    engine = OnboardingEngine(config, steps)
    engine.run()

    assert "step-a" not in ran, "step-a should have been skipped (before jump target)"
    assert "step-b" in ran, "step-b (the jump target) must run"
    assert "step-c" in ran, "step-c (after jump target) must run"


# ── 3. Full run still honours _already_completed for non-target steps ─────────


def test_full_run_skips_already_completed_steps(tmp_path: Path) -> None:
    """
    When jump_to_step is None (normal full run), steps that are already
    completed in the artifact should still be skipped (regression guard).
    """
    _write_artifact(tmp_path, ["step-1", "step-2"])

    ran: list[str] = []

    def _track(sid: str):
        def _run() -> StepResult:
            ran.append(sid)
            return StepResult(status="completed", output={})
        return _run

    steps = [
        OnboardingStep(id="step-1", title="1", run=_track("step-1"), on_failure="skip"),
        OnboardingStep(id="step-2", title="2", run=_track("step-2"), on_failure="skip"),
        OnboardingStep(id="step-3", title="3", run=_track("step-3"), on_failure="skip"),
    ]

    config = _make_config(tmp_path)  # no jump_to_step
    engine = OnboardingEngine(config, steps)
    engine.run()

    assert "step-1" not in ran, "step-1 should be skipped (already completed in artifact)"
    assert "step-2" not in ran, "step-2 should be skipped (already completed in artifact)"
    assert "step-3" in ran, "step-3 (not completed) must run"


# ── 4. Runner uses ordinal-only progress format when jump_to_step is set ─────


def test_runner_progress_uses_ordinal_when_jump_set() -> None:
    """
    When run_engine_onboarding() is called with a jump_to_step, the progress
    line should read `[step N] · Title...` NOT `[N/18 27%] · Title (essential)...`.
    """
    from navig.onboarding.engine import EngineState, StepRecord

    fake_state = EngineState(
        steps=[
            StepRecord(
                id="ai-provider",
                title="AI Provider",
                status="completed",
                completed_at="2026-01-01T00:00:00",
                duration_ms=0,
                output={},
            )
        ],
        interrupted_at="",
    )

    # A fake step whose title we can match in output
    fake_step = OnboardingStep(
        id="ai-provider",
        title="AI Provider",
        run=lambda: StepResult(status="completed", output={}),
        on_failure="skip",
    )

    buf = io.StringIO()

    with (
        patch("navig.onboarding.runner.load_or_create") as mock_genesis,
        patch("navig.onboarding.runner.build_step_registry", return_value=[fake_step] * 18),
        patch("navig.onboarding.runner.OnboardingEngine") as mock_engine_cls,
        patch("sys.stdout", buf),
    ):
        mock_genesis.return_value = MagicMock()
        engine_instance = MagicMock()
        engine_instance.run.return_value = fake_state

        # Capture the on_step_start callback and call it with our fake step
        def _capture_engine(cfg, steps, *, on_step_start=None):
            if on_step_start is not None:
                on_step_start(fake_step)
            return engine_instance

        mock_engine_cls.side_effect = _capture_engine

        from navig.onboarding.runner import run_engine_onboarding

        run_engine_onboarding(
            force=False,
            jump_to_step="ai-provider",
            show_banner=False,
        )

    output = buf.getvalue()

    # Must use ordinal format
    assert "[step 1]" in output, (
        f"Expected '[step 1]' in progress output when jump_to_step is set.\nGot: {output!r}"
    )

    # Must NOT use the fraction / percentage format
    assert "/18" not in output, (
        f"Progress should not contain '/18' fraction when jump_to_step is set.\nGot: {output!r}"
    )
    assert "%" not in output, (
        f"Progress should not contain '%' when jump_to_step is set.\nGot: {output!r}"
    )


# ── 5. Normal run still uses fraction/percentage format ───────────────────────


def test_runner_progress_uses_fraction_when_no_jump() -> None:
    """
    Regression guard: without jump_to_step, progress must still be
    `[N/total pct%] · Title (tier)...`.
    """
    from navig.onboarding.engine import EngineState, StepRecord

    fake_state = EngineState(
        steps=[
            StepRecord(
                id="s1",
                title="Step One",
                status="completed",
                completed_at="2026-01-01T00:00:00",
                duration_ms=0,
                output={},
            )
        ],
        interrupted_at="",
    )

    fake_step = OnboardingStep(
        id="s1",
        title="Step One",
        run=lambda: StepResult(status="completed", output={}),
        on_failure="skip",
    )

    import pathlib

    buf = io.StringIO()

    with (
        patch.object(pathlib.Path, "exists", return_value=False),
        patch("navig.onboarding.runner.load_or_create") as mock_genesis,
        patch("navig.onboarding.runner.build_step_registry", return_value=[fake_step] * 5),
        patch("navig.onboarding.runner.OnboardingEngine") as mock_engine_cls,
        patch("sys.stdout", buf),
    ):
        mock_genesis.return_value = MagicMock()
        engine_instance = MagicMock()
        engine_instance.run.return_value = fake_state

        def _capture_engine(cfg, steps, *, on_step_start=None):
            if on_step_start is not None:
                on_step_start(fake_step)
            return engine_instance

        mock_engine_cls.side_effect = _capture_engine

        from navig.onboarding.runner import run_engine_onboarding

        run_engine_onboarding(force=False, show_banner=False)

    output = buf.getvalue()

    assert "/5" in output, (
        f"Expected '/5' fraction in progress output for normal run.\nGot: {output!r}"
    )
    assert "%" in output, (
        f"Expected '%' in progress output for normal run.\nGot: {output!r}"
    )

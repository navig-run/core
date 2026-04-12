"""
Tests for navig.onboarding.engine — OnboardingEngine core logic.
"""

from __future__ import annotations

import json
from pathlib import Path

from navig.onboarding.engine import (
    EngineConfig,
    OnboardingEngine,
    OnboardingStep,
    StepResult,
)
import pytest

pytestmark = pytest.mark.integration


def _make_config(tmp_path: Path, **kwargs) -> EngineConfig:
    return EngineConfig(
        navig_dir=tmp_path,
        node_name="test-node",
        **kwargs,
    )


def _success_step(step_id: str, title: str = "A Step", independent: bool = False) -> OnboardingStep:
    return OnboardingStep(
        id=step_id,
        title=title,
        run=lambda: StepResult(status="completed", output={"ok": True}),
        on_failure="skip",
        independent=independent,
    )


def _failing_step(
    step_id: str, on_failure: str = "skip", title: str = "Bad Step"
) -> OnboardingStep:
    return OnboardingStep(
        id=step_id,
        title=title,
        run=lambda: StepResult(status="failed", error="boom"),
        on_failure=on_failure,
    )


def _verified_step(step_id: str) -> OnboardingStep:
    """Step whose verify() always returns True — should be skipped."""
    return OnboardingStep(
        id=step_id,
        title="Pre-verified Step",
        run=lambda: StepResult(status="completed", output={}),
        verify=lambda: True,
        on_failure="skip",
    )


# ── 1. Happy path — all steps complete ───────────────────────────────────────


def test_run_all_steps_complete(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    steps = [_success_step("s1"), _success_step("s2"), _success_step("s3")]
    engine = OnboardingEngine(config, steps)
    state = engine.run()

    assert state.completed_at is not None
    completed = [s for s in state.steps if s.status == "completed"]
    assert len(completed) == 3

    # Artifact written
    artifact = tmp_path / "onboarding.json"
    assert artifact.exists()
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert len(data["steps"]) == 3


# ── 2. verify() → step skipped ───────────────────────────────────────────────


def test_verify_skips_step(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    steps = [_verified_step("s-verified"), _success_step("s-normal")]
    engine = OnboardingEngine(config, steps)
    state = engine.run()

    statuses = {s.id: s.status for s in state.steps}
    assert statuses["s-verified"] == "skipped"
    assert statuses["s-normal"] == "completed"


# ── 3. on_failure=abort stops the run ────────────────────────────────────────


def test_on_failure_abort_stops_run(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    steps = [
        _failing_step("fail-step", on_failure="abort"),
        _success_step("should-not-run"),
    ]
    engine = OnboardingEngine(config, steps)
    state = engine.run()

    ids = [s.id for s in state.steps]
    assert "should-not-run" not in ids or all(
        s.status != "completed" for s in state.steps if s.id == "should-not-run"
    )


# ── 4. on_failure=skip continues ─────────────────────────────────────────────


def test_on_failure_skip_continues(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    steps = [
        _failing_step("fail-step", on_failure="skip"),
        _success_step("after-fail"),
    ]
    engine = OnboardingEngine(config, steps)
    state = engine.run()

    statuses = {s.id: s.status for s in state.steps}
    assert statuses.get("after-fail") == "completed"


# ── 5. Resume skips already-completed steps ──────────────────────────────────


def test_resume_skips_completed_steps(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    step1 = _success_step("s1")
    step2 = _success_step("s2")
    engine = OnboardingEngine(config, [step1, step2])
    engine.run()

    # Second engine — s1 already done in artifact
    engine2 = OnboardingEngine(config, [step1, step2])
    assert engine2.already_completed("s1")
    assert engine2.already_completed("s2")


def test_jump_to_step_runs_target_and_following_steps(tmp_path: Path) -> None:
    config = _make_config(tmp_path, jump_to_step="s2")
    steps = [_success_step("s1"), _success_step("s2"), _success_step("s3")]
    engine = OnboardingEngine(config, steps)

    state = engine.run()

    assert [step.id for step in state.steps] == ["s2", "s3"]
    assert all(step.status == "completed" for step in state.steps)


# ── 6. --reset deletes artifact ───────────────────────────────────────────────


def test_reset_deletes_artifact(tmp_path: Path) -> None:
    # First run
    config = _make_config(tmp_path)
    engine = OnboardingEngine(config, [_success_step("s1")])
    engine.run()
    assert (tmp_path / "onboarding.json").exists()

    # Reset run
    config_reset = _make_config(tmp_path, reset=True)
    engine2 = OnboardingEngine(config_reset, [_success_step("s1")])
    assert not engine2.already_completed("s1")


# ── 7. dry_run marks all steps skipped, no side effects ──────────────────────


def test_dry_run_no_execution(tmp_path: Path) -> None:
    executed: list[str] = []

    def tracking_run(config):
        executed.append("ran")
        return StepResult(status="completed", output={})

    config = _make_config(tmp_path, dry_run=True)
    step = OnboardingStep(id="tracked", title="Track", run=tracking_run, on_failure="skip")
    engine = OnboardingEngine(config, [step])
    state = engine.run()

    assert len(executed) == 0
    assert all(s.status == "skipped" for s in state.steps)


# ── 8. execute_single returns correct (skipped, result) ──────────────────────


def test_execute_single_returns_tuple(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    step = _success_step("solo")
    engine = OnboardingEngine(config, [step])

    was_skipped, result = engine.execute_single(step)
    assert was_skipped is False
    assert result.status == "completed"

    # Running verified step returns was_skipped=True
    vstep = _verified_step("vsolo")
    was_skipped2, result2 = engine.execute_single(vstep)
    assert was_skipped2 is True


# ── 9. Retroactive step updates ───────────────────────────────────────────────


def test_retroactive_update_promotes_skipped_to_completed(tmp_path: Path) -> None:
    """A step that emits _retroactiveUpdates upgrades an earlier skipped record."""
    config = _make_config(tmp_path)

    early_step = OnboardingStep(
        id="early",
        title="Early Step",
        run=lambda: StepResult(status="skipped", output={"reason": "user skipped"}),
        on_failure="skip",
    )

    def _later_run() -> StepResult:
        return StepResult(
            status="completed",
            output={
                "done": True,
                "_retroactiveUpdates": [
                    {
                        "id": "early",
                        "status": "completed",
                        "output": {"note": "fixed by later step"},
                    }
                ],
            },
        )

    later_step = OnboardingStep(
        id="later",
        title="Later Step",
        run=_later_run,
        on_failure="skip",
    )

    engine = OnboardingEngine(config, [early_step, later_step])
    state = engine.run()

    statuses = {s.id: s.status for s in state.steps}
    assert statuses["early"] == "completed", "early step should be promoted to completed"
    assert statuses["later"] == "completed"

    # _retroactiveUpdates must NOT appear in the persisted artifact output
    artifact_data = json.loads((tmp_path / "onboarding.json").read_text(encoding="utf-8"))
    later_record = next(s for s in artifact_data["steps"] if s["id"] == "later")
    assert "_retroactiveUpdates" not in later_record["output"]

    # The promoted record should carry the extra output fields
    early_record = next(s for s in artifact_data["steps"] if s["id"] == "early")
    assert early_record["output"].get("note") == "fixed by later step"


def test_retroactive_update_does_not_downgrade_completed_step(tmp_path: Path) -> None:
    """A retroactive update must not change a step that already completed."""
    config = _make_config(tmp_path)

    completed_step = OnboardingStep(
        id="already-done",
        title="Already Done",
        run=lambda: StepResult(status="completed", output={"original": True}),
        on_failure="skip",
    )

    def _later_run() -> StepResult:
        return StepResult(
            status="completed",
            output={
                "_retroactiveUpdates": [
                    {
                        "id": "already-done",
                        "status": "skipped",  # attempted downgrade
                        "output": {"hijacked": True},
                    }
                ]
            },
        )

    later_step = OnboardingStep(
        id="later",
        title="Later Step",
        run=_later_run,
        on_failure="skip",
    )

    engine = OnboardingEngine(config, [completed_step, later_step])
    state = engine.run()

    statuses = {s.id: s.status for s in state.steps}
    assert statuses["already-done"] == "completed", "completed step must not be downgraded"


def test_execute_single_applies_retroactive_updates(tmp_path: Path) -> None:
    """execute_single() also processes retroactive updates."""
    config = _make_config(tmp_path)

    early_step = OnboardingStep(
        id="early",
        title="Early",
        run=lambda: StepResult(status="skipped", output={}),
        on_failure="skip",
    )

    def _later_run() -> StepResult:
        return StepResult(
            status="completed",
            output={
                "_retroactiveUpdates": [
                    {"id": "early", "status": "completed", "output": {"promoted": True}}
                ]
            },
        )

    later_step = OnboardingStep(
        id="later",
        title="Later",
        run=_later_run,
        on_failure="skip",
    )

    engine = OnboardingEngine(config, [early_step, later_step])
    engine.execute_single(early_step)
    engine.execute_single(later_step)

    statuses = {s.id: s.status for s in engine.finalize().steps}
    assert statuses["early"] == "completed"

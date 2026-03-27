"""
OnboardingEngine — sequential step executor with resume semantics.

Design decisions:
- Steps are plain dataclasses + callables. No class hierarchy.
- Artifact written after EVERY step — crash recovery is free.
- Parallel execution is opt-in, strictly guarded by independent=True on BOTH steps.
- Engine never touches stdout — all output goes to the renderer via StepResult.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional

PhaseLabel = Literal["bootstrap", "configuration"]

ARTIFACT_VERSION = 1
ARTIFACT_FILENAME = "onboarding.json"
ENGINE_VERSION = "2.0.0"

OnFailurePolicy = Literal["abort", "skip", "retry"]


@dataclass
class StepResult:
    status: Literal["completed", "skipped", "failed"]
    output: dict
    duration_ms: int = 0
    error: Optional[str] = None
    fix_hint: Optional[str] = None


@dataclass
class StepRecord:
    """Serialisable record — written into onboarding.json."""

    id: str
    title: str
    status: Literal["completed", "skipped", "failed"]
    completed_at: str
    duration_ms: int
    output: dict
    error: Optional[str] = None


def _verify_always_run() -> bool:
    """Default verify: step has no idempotency check — always execute."""
    return False


@dataclass
class OnboardingStep:
    """
    A single onboarding unit.

    `run` and `verify` are plain callables — trivially unit-testable.
    `verify` defaults to always-run (returns False = "not already done").
    `independent=True` permits parallel execution only when both steps in a
    pair declare it; the flag is explicit because parallelism bugs are silent.
    `phase` separates bootstrap (workspace/config/ssh) from configuration
    (ai-provider/vault/host/integrations) — Phase 2 steps are TTY-gated.
    """

    id: str
    title: str
    run: Callable[[], StepResult]
    verify: Callable[[], bool] = field(default_factory=lambda: _verify_always_run)
    on_failure: OnFailurePolicy = "abort"
    independent: bool = False
    phase: PhaseLabel = "bootstrap"


@dataclass
class EngineConfig:
    navig_dir: Path
    node_name: str
    dry_run: bool = False
    no_genesis: bool = False
    reset: bool = False
    jump_to_step: Optional[str] = None


@dataclass
class EngineState:
    """Full mutable state. Serialised to the artifact after each step."""

    version: int = ARTIFACT_VERSION
    node_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    engine_version: str = ENGINE_VERSION
    steps: list[StepRecord] = field(default_factory=list)


class OnboardingEngine:
    """
    Execute onboarding steps with resume semantics.

    The engine's only public method is run().
    All output is returned — never printed directly.
    The renderer decides how to display it.
    """

    def __init__(self, config: EngineConfig, steps: list[OnboardingStep]) -> None:
        self._config = config
        self._steps = steps
        self._artifact = config.navig_dir / ARTIFACT_FILENAME
        self._state = self._load_or_init_state()

    # ── Public ─────────────────────────────────────────────────────────────

    def run(self) -> EngineState:
        """
        Execute the step list. Returns final EngineState.

        verify() = True → skip silently (post-condition already holds).
        Artifact written after every step — crash mid-run is recoverable.
        """
        if self._config.dry_run:
            return self._dry_run()

        # True once we reach (or pass) the jump target; always True when no jump configured.
        reached_target = not bool(self._config.jump_to_step)

        for step in self._steps:
            if not reached_target:
                if step.id == self._config.jump_to_step:
                    reached_target = True
                else:
                    continue  # skip steps before the jump target

            if self._already_completed(step.id):
                continue

            t0 = time.monotonic()
            # On reset: bypass verify so every step re-executes.
            if self._config.reset:
                already_done = False
            else:
                try:
                    already_done = step.verify()
                except Exception:  # noqa: BLE001
                    already_done = False

            if already_done:
                record = StepRecord(
                    id=step.id,
                    title=step.title,
                    status="skipped",
                    completed_at=_now(),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    output={},
                )
                self._record(record)
                continue

            result = self._execute(step)
            record = StepRecord(
                id=step.id,
                title=step.title,
                status=result.status,
                completed_at=_now(),
                duration_ms=result.duration_ms,
                output=result.output,
                error=result.error,
            )
            self._record(record)

            if result.status == "failed" and step.on_failure == "abort":
                self._state.completed_at = _now()
                self._write_artifact()
                return self._state

        self._state.completed_at = _now()
        self._write_artifact()
        return self._state

    def execute_single(self, step: OnboardingStep) -> tuple[bool, StepResult]:
        """
        Execute one step and record it.  Returns (was_skipped_by_verify, result).
        Used by the live-output CLI driver.
        """
        t0 = time.monotonic()
        # On reset: always re-execute regardless of verify() result.
        if self._config.reset:
            already_done = False
        else:
            try:
                already_done = step.verify()
            except Exception:  # noqa: BLE001
                already_done = False

        if already_done:
            record = StepRecord(
                id=step.id,
                title=step.title,
                status="skipped",
                completed_at=_now(),
                duration_ms=int((time.monotonic() - t0) * 1000),
                output={},
            )
            self._record(record)
            result = StepResult(
                status="skipped", output={}, duration_ms=record.duration_ms
            )
            return True, result

        result = self._execute(step)
        result_duration = result.duration_ms
        record = StepRecord(
            id=step.id,
            title=step.title,
            status=result.status,
            completed_at=_now(),
            duration_ms=result_duration,
            output=result.output,
            error=result.error,
        )
        self._record(record)
        return False, result

    def already_completed(self, step_id: str) -> bool:
        return self._already_completed(step_id)

    def finalize(self) -> EngineState:
        self._state.completed_at = _now()
        self._write_artifact()
        return self._state

    # ── Private ────────────────────────────────────────────────────────────

    def _execute(self, step: OnboardingStep) -> StepResult:
        t0 = time.monotonic()
        try:
            result = step.run()
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            return result
        except Exception as exc:  # noqa: BLE001
            if step.on_failure == "retry":
                time.sleep(2)
                try:
                    result = step.run()
                    result.duration_ms = int((time.monotonic() - t0) * 1000)
                    return result
                except Exception as exc2:  # noqa: BLE001
                    return StepResult(
                        status="failed",
                        output={},
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        error=str(exc2),
                    )
            elif step.on_failure == "skip":
                return StepResult(
                    status="skipped",
                    output={},
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    error=str(exc),
                )
            else:
                return StepResult(
                    status="failed",
                    output={},
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    error=str(exc),
                )

    def _dry_run(self) -> EngineState:
        for step in self._steps:
            self._state.steps.append(
                StepRecord(
                    id=step.id,
                    title=step.title,
                    status="skipped",
                    completed_at="",
                    duration_ms=0,
                    output={"dry_run": "true"},
                )
            )
        return self._state

    def _already_completed(self, step_id: str) -> bool:
        return any(
            r.id == step_id and r.status == "completed" for r in self._state.steps
        )

    def _record(self, record: StepRecord) -> None:
        """Replace any existing record for this step ID, then flush immediately."""
        self._state.steps = [r for r in self._state.steps if r.id != record.id]
        self._state.steps.append(record)
        self._write_artifact()

    def _write_artifact(self) -> None:
        self._config.navig_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self._state.version,
            "nodeId": self._state.node_id,
            "startedAt": self._state.started_at,
            "completedAt": self._state.completed_at,
            "engineVersion": self._state.engine_version,
            "steps": [asdict(s) for s in self._state.steps],
        }
        self._artifact.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_or_init_state(self) -> EngineState:
        if self._config.reset and self._artifact.exists():
            self._artifact.unlink()

        if self._artifact.exists():
            try:
                raw = json.loads(self._artifact.read_text(encoding="utf-8"))
                state = EngineState(
                    version=raw.get("version", ARTIFACT_VERSION),
                    node_id=raw.get("nodeId", ""),
                    started_at=raw.get("startedAt", ""),
                    completed_at=raw.get("completedAt", ""),
                    engine_version=raw.get("engineVersion", ENGINE_VERSION),
                )
                state.steps = [StepRecord(**s) for s in raw.get("steps", [])]
                return state
            except Exception:
                pass  # Corrupt artifact — start fresh

        return EngineState(started_at=_now())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

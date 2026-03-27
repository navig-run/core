"""
Tests for the _check_first_run() hook in navig/main.py.

Covers:
- Skips when onboarding.json artifact exists
- Skips when NAVIG_SKIP_ONBOARDING=1
- Skips when shell-completion env vars are present
- Skips when a no-onboard subcommand is in argv
- Runs (calls engine.run) when artifact is absent
- Phase 2 steps are skipped (not hung) in a non-TTY environment
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import navig.main as _nm

# ---------------------------------------------------------------------------
# Env isolation — strip opt-out vars before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "NAVIG_SKIP_ONBOARDING",
        "_NAVIG_COMPLETE",
        "COMP_WORDS",
        "_TYPER_COMPLETE",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Skip-condition tests
# ---------------------------------------------------------------------------


class TestFirstRunSkipConditions:
    """_check_first_run() must return immediately without touching the engine."""

    def test_skips_when_artifact_exists(self, tmp_path):
        """onboarding.json present → no engine instantiation."""
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        (navig_dir / "onboarding.json").write_text(
            '{"completedAt":"2026-01-01T00:00:00+00:00"}', encoding="utf-8"
        )

        engine_cls = MagicMock()
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch("navig.onboarding.OnboardingEngine", engine_cls),
        ):
            _nm._check_first_run()

        engine_cls.assert_not_called()

    def test_skips_with_env_var(self, monkeypatch):
        """NAVIG_SKIP_ONBOARDING=1 → no engine instantiation."""
        monkeypatch.setenv("NAVIG_SKIP_ONBOARDING", "1")
        engine_cls = MagicMock()
        with patch("navig.onboarding.OnboardingEngine", engine_cls):
            _nm._check_first_run()
        engine_cls.assert_not_called()

    @pytest.mark.parametrize("var", ["_NAVIG_COMPLETE", "COMP_WORDS", "_TYPER_COMPLETE"])
    def test_skips_for_completion_probe(self, monkeypatch, var):
        """Shell-completion env vars → no engine instantiation."""
        monkeypatch.setenv(var, "1")
        engine_cls = MagicMock()
        with patch("navig.onboarding.OnboardingEngine", engine_cls):
            _nm._check_first_run()
        engine_cls.assert_not_called()

    @pytest.mark.parametrize("cmd", ["onboard", "quickstart", "service", "update", "version"])
    def test_skips_for_guarded_subcommands(self, tmp_path, cmd):
        """Commands listed in _SKIP_CMDS must not trigger onboarding."""
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)  # no artifact
        engine_cls = MagicMock()
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(sys, "argv", ["navig", cmd]),
            patch("navig.onboarding.OnboardingEngine", engine_cls),
        ):
            _nm._check_first_run()
        engine_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Trigger tests
# ---------------------------------------------------------------------------


class TestFirstRunTriggersEngine:
    """engine.run() must be called when no skip conditions apply."""

    def _call(self, tmp_path, argv=None):
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        engine_instance = MagicMock()
        engine_cls = MagicMock(return_value=engine_instance)

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(sys, "argv", argv or ["navig", "status"]),
            patch("navig.onboarding.OnboardingEngine", engine_cls),
            patch("navig.onboarding.EngineConfig", MagicMock()),
            patch("navig.onboarding.genesis.load_or_create", return_value=MagicMock()),
            patch("navig.onboarding.steps.build_step_registry", return_value=[]),
            patch("socket.gethostname", return_value="test-host"),
        ):
            _nm._check_first_run()

        return engine_instance

    def test_engine_run_called_when_artifact_absent(self, tmp_path):
        instance = self._call(tmp_path)
        instance.run.assert_called_once()

    def test_engine_run_called_for_any_normal_command(self, tmp_path):
        instance = self._call(tmp_path, argv=["navig", "host", "list"])
        instance.run.assert_called_once()

    def test_engine_exception_does_not_propagate(self, tmp_path):
        """A crash inside the engine must never propagate to the CLI caller."""
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(sys, "argv", ["navig", "status"]),
            patch(
                "navig.onboarding.EngineConfig",
                side_effect=RuntimeError("simulated crash"),
            ),
            patch("socket.gethostname", return_value="test-host"),
            patch("navig.main._eprint"),
        ):
            _nm._check_first_run()  # must not raise


# ---------------------------------------------------------------------------
# Non-TTY / CI safety
# ---------------------------------------------------------------------------


class TestFirstRunNonTTY:
    def test_phase2_skipped_not_hung_in_non_tty(self, tmp_path, monkeypatch):
        """In a non-TTY env the engine must complete: Phase 1 runs, Phase 2 skips."""
        from navig.onboarding.engine import EngineConfig, OnboardingEngine
        from navig.onboarding.genesis import load_or_create
        from navig.onboarding.steps import build_step_registry

        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        node_name = "ci-test-node"
        cfg = EngineConfig(navig_dir=navig_dir, node_name=node_name)
        genesis = load_or_create(navig_dir, name=node_name)
        steps = build_step_registry(cfg, genesis)
        engine = OnboardingEngine(cfg, steps)

        state = engine.run()

        # Phase 2 steps must be skipped (non-TTY → _tty_check returns skipped)
        phase2_ids = {
            "ai-provider",
            "vault-init",
            "first-host",
            "telegram-bot",
            "skills-activation",
        }
        for record in state.steps:
            if record.id in phase2_ids:
                assert record.status in ("skipped", "completed"), (
                    f"Phase 2 step {record.id!r} should be skipped in non-TTY, "
                    f"got {record.status!r}"
                )

        # Artifact written → engine completed without hanging
        assert (navig_dir / "onboarding.json").exists()

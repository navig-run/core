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

pytestmark = pytest.mark.integration

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

    @pytest.mark.parametrize("cmd", ["onboard", "quickstart", "service", "update", "version", "mcp"])
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

    def test_mcp_serve_never_runs_onboarding(self, tmp_path):
        """`navig mcp serve` is a JSON-RPC transport: stdout is the protocol stream.

        A client spawns this process, so nobody can answer a wizard. Measured in a clean
        container before this rule: the 13-step onboarding ran first, printing a welcome
        banner and a telemetry ping ahead of `initialize`.
        """
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        engine_cls = MagicMock()
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(sys, "argv", ["navig", "mcp", "serve", "--transport", "stdio"]),
            patch("navig.onboarding.OnboardingEngine", engine_cls),
        ):
            _nm._check_first_run()
        engine_cls.assert_not_called()

    def test_skips_for_help_topic_command(self, tmp_path):
        """Help-topic invocation must never trigger onboarding."""
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        engine_cls = MagicMock()
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(sys, "argv", ["navig", "help", "db"]),
            patch("navig.onboarding.OnboardingEngine", engine_cls),
        ):
            _nm._check_first_run()
        engine_cls.assert_not_called()

    def test_skips_for_prefixed_global_help_flag(self, tmp_path):
        """Global-flag-prefixed help should still skip onboarding."""
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        engine_cls = MagicMock()
        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(sys, "argv", ["navig", "--host", "prod", "--help"]),
            patch("navig.onboarding.OnboardingEngine", engine_cls),
        ):
            _nm._check_first_run()
        engine_cls.assert_not_called()

    @pytest.mark.parametrize(
        "argv",
        [
            ["navig", "doctor", "--json"],
            ["navig", "tiktok", "info", "--json"],
            ["navig", "--json", "status"],
        ],
    )
    def test_skips_for_json_invocations(self, tmp_path, argv):
        """`--json` anywhere in argv → programmatic caller → never onboard.

        stdout of a --json run must carry exactly one machine-readable
        document, and a script can never answer the wizard (some steps prompt
        even without a TTY and pause setup on EOF).
        """
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)  # no artifact
        with (
            patch("navig.onboarding.runner.config_dir", return_value=tmp_path / ".navig"),
            patch.object(sys, "argv", argv),
        ):
            from navig.onboarding.runner import should_auto_run_onboarding

            assert should_auto_run_onboarding(None) is False, (
                f"--json invocation {argv!r} must never trigger onboarding"
            )

    def test_explicit_empty_argv_does_not_leak_sys_argv(self, tmp_path):
        """argv=[] must be honoured as-is; sys.argv must not be inspected."""
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        # Inject a sys.argv that would trigger onboarding if leaked
        with (
            patch("navig.onboarding.runner.config_dir", return_value=tmp_path / ".navig"),
            patch.object(sys, "argv", ["navig", "host", "list"]),
        ):
            from navig.onboarding.runner import should_auto_run_onboarding

            # Explicit empty list — caller signals "no argv context"
            result = should_auto_run_onboarding([])
        assert result is False, "empty argv=[] must never trigger onboarding"

    def test_argv_none_falls_back_to_sys_argv(self, tmp_path):
        """argv=None must still fall back to sys.argv for normal invocations."""
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        with (
            patch("navig.onboarding.runner.config_dir", return_value=tmp_path / ".navig"),
            patch.object(sys, "argv", ["navig", "host", "list"]),
        ):
            from navig.onboarding.runner import should_auto_run_onboarding

            result = should_auto_run_onboarding(None)
        # onboarding.json doesn't exist, command is normal — should trigger
        assert result is True


# ---------------------------------------------------------------------------
# Trigger tests
# ---------------------------------------------------------------------------


class TestFirstRunTriggersEngine:
    """engine.run() must be called when no skip conditions apply."""

    def _call(self, tmp_path, argv=None):
        (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
        run_engine = MagicMock()

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            patch.object(sys, "argv", argv or ["navig", "status"]),
            patch(
                "navig.onboarding.runner.should_auto_run_onboarding",
                return_value=True,
            ),
            patch("navig.onboarding.runner.run_engine_onboarding", run_engine),
        ):
            _nm._check_first_run()

        return run_engine

    def test_engine_run_called_when_artifact_absent(self, tmp_path):
        runner = self._call(tmp_path)
        runner.assert_called_once()

    def test_engine_run_called_for_any_normal_command(self, tmp_path):
        runner = self._call(tmp_path, argv=["navig", "host", "list"])
        runner.assert_called_once()

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
            "web-search-provider",
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


# ---------------------------------------------------------------------------
# `navig init --profile` — the installer pipeline never pre-runs the wizard
# ---------------------------------------------------------------------------
#
# The option's own help says "Run installer profile without wizard", and
# `install.sh -p <name>` shells out to exactly that. First-run onboarding
# pre-empting it would put an interactive wizard in front of a scripted
# install; `navig init` WITHOUT --profile must keep triggering it as before.</think>
def _should_onboard(tmp_path, argv):
    (tmp_path / ".navig").mkdir(parents=True, exist_ok=True)
    with (
        patch("navig.onboarding.runner.config_dir", return_value=tmp_path / ".navig"),
        patch.object(sys, "argv", argv),
    ):
        from navig.onboarding.runner import should_auto_run_onboarding

        return should_auto_run_onboarding(None)


def test_init_with_profile_never_triggers_first_run_onboarding(tmp_path):
    assert _should_onboard(tmp_path, ["navig", "init", "--profile", "server"]) is False


def test_init_short_profile_flag_skips_too(tmp_path):
    assert _should_onboard(tmp_path, ["navig", "init", "-p", "cli"]) is False


def test_init_profile_equals_form_skips(tmp_path):
    assert _should_onboard(tmp_path, ["navig", "init", "--profile=full"]) is False


def test_init_without_profile_still_triggers_first_run_onboarding(tmp_path):
    """The skip is scoped to `init` + --profile — nothing else changes."""
    assert _should_onboard(tmp_path, ["navig", "init"]) is True


def test_another_commands_profile_flag_does_not_skip(tmp_path):
    assert _should_onboard(tmp_path, ["navig", "cdp", "new", "--profile", "research"]) is True


# ---------------------------------------------------------------------------
# Joined devices and plumbing (2026-10-16): the LAN join script's
# `navig config set gateway.mesh_token …` launched the 22-step BRAIN wizard on
# the joining laptop and stalled on the AI-provider prompt.
# ---------------------------------------------------------------------------


def test_config_plumbing_never_triggers_the_wizard(tmp_path):
    assert _should_onboard(tmp_path, ["navig", "config", "set", "gateway.mesh_token", "x"]) is False


def test_a_joined_node_is_never_handed_the_brain_wizard(tmp_path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)
    (navig_dir / "config.yaml").write_text("onboarding:\n  role: node\n", encoding="utf-8")
    assert _should_onboard(tmp_path, ["navig", "status"]) is False


def test_a_brain_or_unset_role_still_onboards(tmp_path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(parents=True, exist_ok=True)
    (navig_dir / "config.yaml").write_text("onboarding:\n  role: brain\n", encoding="utf-8")
    assert _should_onboard(tmp_path, ["navig", "status"]) is True


def test_configured_role_tolerates_missing_and_garbled_config(tmp_path):
    from navig.onboarding.runner import configured_role

    assert configured_role(tmp_path) == "brain"  # no config.yaml at all
    (tmp_path / "config.yaml").write_text("{ not: [valid", encoding="utf-8")
    assert configured_role(tmp_path) == "brain"
    (tmp_path / "config.yaml").write_text("onboarding:\n  role: NODE\n", encoding="utf-8")
    assert configured_role(tmp_path) == "node"


def test_nerd_font_prompt_defaults_to_no():
    """Enter used to mean YES — and the installer also rewrites VS Code and
    Windows Terminal settings. It is opt-in now, and says so."""
    import inspect

    from navig.onboarding import steps

    src = inspect.getsource(steps)
    assert "Install JetBrainsMono Nerd Font? [y/N]" in src
    assert 'if reply in ("y", "yes"):' in src
    assert "[Y/n] \").strip().lower()\n\n        if reply in (\"\", \"y\"" not in src

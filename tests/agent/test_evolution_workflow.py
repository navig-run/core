"""Tests for navig.core.evolution.workflow — WorkflowEvolver._validate, _generate, _save, evolve."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.core.evolution.base import EvolutionResult
from navig.core.evolution.workflow import WorkflowEvolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evolver() -> WorkflowEvolver:
    return WorkflowEvolver()


def _valid_yaml(
    name: str = "test_workflow",
    steps: list[dict] | None = None,
) -> str:
    if steps is None:
        steps = [{"action": "wait", "args": {"seconds": 1.0}}]
    steps_str = "\n".join(
        f"  - action: {s['action']}\n    args:\n"
        + "".join(f"      {k}: {v!r}\n" for k, v in s.get("args", {}).items())
        for s in steps
    )
    return f"name: {name}\ndescription: A test\nsteps:\n{steps_str}"


# ---------------------------------------------------------------------------
# WorkflowEvolver._validate — valid cases
# ---------------------------------------------------------------------------

class TestValidateValid:
    def test_minimal_valid_yaml(self):
        ev = _evolver()
        yaml_str = "name: my_flow\nsteps:\n  - action: wait\n    args:\n      seconds: 1.0\n"
        assert ev._validate(yaml_str, None) is None

    def test_valid_open_app_action(self):
        ev = _evolver()
        yaml_str = "name: open_flow\nsteps:\n  - action: open_app\n    args:\n      target: notepad.exe\n"
        assert ev._validate(yaml_str, None) is None

    def test_valid_click_action(self):
        ev = _evolver()
        yaml_str = "name: click_flow\nsteps:\n  - action: click\n    args:\n      x: 100\n      y: 200\n      button: left\n"
        assert ev._validate(yaml_str, None) is None

    def test_valid_type_action(self):
        ev = _evolver()
        yaml_str = "name: type_flow\nsteps:\n  - action: type\n    args:\n      text: hello\n"
        assert ev._validate(yaml_str, None) is None

    def test_valid_run_command_action(self):
        ev = _evolver()
        yaml_str = "name: cmd_flow\nsteps:\n  - action: run_command\n    args:\n      command: echo hi\n"
        assert ev._validate(yaml_str, None) is None

    def test_valid_custom_action_prefix(self):
        ev = _evolver()
        yaml_str = "name: custom_flow\nsteps:\n  - action: custom_my_action\n"
        assert ev._validate(yaml_str, None) is None

    def test_valid_with_platform_override(self):
        ev = _evolver()
        yaml_str = textwrap.dedent("""\
            name: plat_flow
            steps:
              - action: wait
                args:
                  seconds: 1.0
                platform:
                  windows:
                    action: wait
                    args:
                      seconds: 2.0
        """)
        assert ev._validate(yaml_str, None) is None

    def test_valid_multiple_steps(self):
        ev = _evolver()
        yaml_str = textwrap.dedent("""\
            name: multi_flow
            steps:
              - action: open_app
                args:
                  target: calc.exe
              - action: wait
                args:
                  seconds: 1.0
              - action: close_window
                args:
                  selector: Calculator
        """)
        assert ev._validate(yaml_str, None) is None


# ---------------------------------------------------------------------------
# WorkflowEvolver._validate — invalid cases
# ---------------------------------------------------------------------------

class TestValidateInvalid:
    def test_empty_string_returns_error(self):
        ev = _evolver()
        result = ev._validate("", None)
        assert result is not None

    def test_non_dict_root_returns_error(self):
        ev = _evolver()
        result = ev._validate("- item1\n- item2\n", None)
        assert "Root must be a dictionary" in result

    def test_missing_steps_returns_error(self):
        ev = _evolver()
        result = ev._validate("name: flow\n", None)
        assert "Missing 'steps'" in result

    def test_steps_not_list_returns_error(self):
        ev = _evolver()
        result = ev._validate("name: flow\nsteps: not_a_list\n", None)
        assert "'steps' must be a list" in result

    def test_step_not_dict_returns_error(self):
        ev = _evolver()
        result = ev._validate("name: flow\nsteps:\n  - just_a_string\n", None)
        assert "must be a dictionary" in result

    def test_step_missing_action_returns_error(self):
        ev = _evolver()
        result = ev._validate("name: flow\nsteps:\n  - args:\n      x: 1\n", None)
        assert "missing 'action'" in result

    def test_unknown_action_returns_error(self):
        ev = _evolver()
        result = ev._validate("name: flow\nsteps:\n  - action: fly_to_moon\n", None)
        assert "Unknown action" in result

    def test_args_not_dict_returns_error(self):
        ev = _evolver()
        result = ev._validate("name: flow\nsteps:\n  - action: wait\n    args: not_a_dict\n", None)
        assert "'args' must be a dictionary" in result

    def test_invalid_yaml_syntax_returns_error(self):
        ev = _evolver()
        result = ev._validate("{invalid yaml: [unterminated", None)
        assert result is not None  # YAML error or validation error

    def test_platform_not_dict_returns_error(self):
        ev = _evolver()
        yaml_str = textwrap.dedent("""\
            name: plat_flow
            steps:
              - action: wait
                platform: not_a_dict
        """)
        result = ev._validate(yaml_str, None)
        assert "'platform' must be a dictionary" in result

    def test_unknown_platform_returns_error(self):
        ev = _evolver()
        yaml_str = textwrap.dedent("""\
            name: plat_flow
            steps:
              - action: wait
                platform:
                  beos:
                    action: wait
        """)
        result = ev._validate(yaml_str, None)
        assert "Unknown platform" in result

    def test_platform_override_not_dict_returns_error(self):
        ev = _evolver()
        yaml_str = textwrap.dedent("""\
            name: plat_flow
            steps:
              - action: wait
                platform:
                  windows: just_a_string
        """)
        result = ev._validate(yaml_str, None)
        assert "must be a dictionary" in result

    def test_platform_override_unknown_action_returns_error(self):
        ev = _evolver()
        yaml_str = textwrap.dedent("""\
            name: plat_flow
            steps:
              - action: wait
                platform:
                  windows:
                    action: teleport
        """)
        result = ev._validate(yaml_str, None)
        assert "Unknown action" in result


# ---------------------------------------------------------------------------
# WorkflowEvolver._generate — with NAVIG_MOCK_AI
# ---------------------------------------------------------------------------

class TestGenerateMockAI:
    def test_mock_ai_returns_yaml_string(self):
        ev = _evolver()
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = ev._generate("do something", None, "", None)
        assert "name: mock_workflow" in result
        assert "steps:" in result

    def test_mock_ai_yaml_is_valid(self):
        ev = _evolver()
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = ev._generate("goal", None, "", None)
        error = ev._validate(result.strip(), None)
        assert error is None, f"Mock AI produced invalid YAML: {error}"

    def test_generate_extracts_yaml_block(self):
        ev = _evolver()
        fake_response = "Here is your workflow:\n```yaml\nname: demo\nsteps:\n  - action: wait\n    args:\n      seconds: 1\n```\nDone."

        with patch("navig.core.evolution.workflow.ask_ai_with_context", return_value=fake_response):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NAVIG_MOCK_AI", None)
                result = ev._generate("demo task", None, "", None)
        assert "name: demo" in result

    def test_generate_fallback_when_no_yaml_block(self):
        ev = _evolver()
        raw = "name: raw_workflow\nsteps:\n  - action: wait\n    args:\n      seconds: 1\n"

        with patch("navig.core.evolution.workflow.ask_ai_with_context", return_value=raw):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NAVIG_MOCK_AI", None)
                result = ev._generate("raw", None, "", None)
        assert result == raw

    def test_generate_with_previous_artifact_includes_it_in_prompt(self):
        ev = _evolver()
        captured_prompts = []

        def fake_ask(prompt, **kwargs):
            captured_prompts.append(prompt)
            return "```yaml\nname: retry_flow\nsteps:\n  - action: wait\n    args:\n      seconds: 1\n```"

        with patch("navig.core.evolution.workflow.ask_ai_with_context", side_effect=fake_ask):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NAVIG_MOCK_AI", None)
                ev._generate("retry", "old yaml", "old error", None)

        assert captured_prompts
        assert "old error" in captured_prompts[0]
        assert "old yaml" in captured_prompts[0]


# ---------------------------------------------------------------------------
# WorkflowEvolver._save
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_writes_yaml_to_workflows_dir(self, tmp_path):
        ev = _evolver()
        ev._workflows_dir = tmp_path
        yaml_content = "name: save_test_flow\nsteps:\n  - action: wait\n    args:\n      seconds: 1\n"
        ev._save("save goal", yaml_content)
        saved = tmp_path / "save_test_flow.yaml"
        assert saved.exists()
        assert "save_test_flow" in saved.read_text(encoding="utf-8")

    def test_save_sanitizes_name_with_spaces(self, tmp_path):
        ev = _evolver()
        ev._workflows_dir = tmp_path
        yaml_content = "name: my flow name\nsteps:\n  - action: wait\n    args:\n      seconds: 1\n"
        ev._save("goal", yaml_content)
        # spaces should be replaced with underscores
        expected = tmp_path / "my_flow_name.yaml"
        assert expected.exists()

    def test_save_exception_does_not_propagate(self):
        ev = _evolver()
        ev._workflows_dir = Path("/nonexistent/dir")
        # Should not raise
        ev._save("goal", "name: fail_save\nsteps:\n  - action: wait\n")


# ---------------------------------------------------------------------------
# WorkflowEvolver.evolve — integration test using NAVIG_MOCK_AI
# ---------------------------------------------------------------------------

class TestEvolveIntegration:
    def test_evolve_success_with_mock_ai(self):
        ev = _evolver()
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            with patch.object(ev, "_save"):  # Don't write to disk
                result = ev.evolve("automate calculator")
        assert isinstance(result, EvolutionResult)
        assert result.success is True
        assert result.artifact is not None

    def test_evolve_fails_after_max_retries(self):
        ev = _evolver()
        # _generate returns YAML that always fails validation
        bad_yaml = "- definitely: not valid\n"
        with patch.object(ev, "_generate", return_value=bad_yaml):
            with patch.object(ev, "_save"):
                result = ev.evolve("always fail")
        assert isinstance(result, EvolutionResult)
        assert result.success is False
        assert result.attempts == ev.max_retries

    def test_evolve_returns_cached_if_available(self):
        ev = _evolver()
        cached = "name: cached_flow\nsteps:\n  - action: wait\n    args:\n      seconds: 1\n"
        with patch.object(ev, "_check_cache", return_value=cached):
            result = ev.evolve("cached goal")
        assert result.success is True
        assert result.artifact == cached
        assert result.attempts == 0

    def test_evolve_captures_history(self):
        ev = _evolver()
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            with patch.object(ev, "_save"):
                result = ev.evolve("history test")
        assert result.history is not None
        assert len(result.history) >= 1

    def test_evolve_generation_exception_returns_failure(self):
        ev = _evolver()
        with patch.object(ev, "_generate", side_effect=RuntimeError("generate boom")):
            result = ev.evolve("crash test")
        assert result.success is False
        assert "generate boom" in result.error

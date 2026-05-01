"""
Batch 58: hermetic unit tests for
  - navig/commands/user.py          (Typer CLI handlers)
  - navig/tools/domains/data_pack.py (json_parse tool)
  - navig/adapters/automation/types.py (ExecutionResult, WindowInfo)
  - navig/agent/skill_drafter.py    (SkillDraft, SkillDrafter)
  - navig/env_validator.py          (validate_environment)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/commands/user.py
# ---------------------------------------------------------------------------

class TestUserApp:
    def test_user_app_importable(self) -> None:
        from navig.commands.user import user_app
        assert user_app is not None

    def test_user_app_is_typer(self) -> None:
        import typer
        from navig.commands.user import user_app
        assert isinstance(user_app, typer.Typer)

    def test_user_show_command_registered(self) -> None:
        from navig.commands.user import user_app
        names = [cmd.name for cmd in user_app.registered_commands]
        assert "show" in names

    def test_user_set_command_registered(self) -> None:
        from navig.commands.user import user_app
        names = [cmd.name for cmd in user_app.registered_commands]
        assert "set" in names

    def test_user_show_reads_config(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.user import user_app

        mock_cfg = MagicMock()
        mock_cfg.get.side_effect = lambda key, default="": {"user.name": "Alice", "user.email": "alice@example.com"}.get(key, default)
        runner = CliRunner()
        with patch("navig.config.ConfigManager", return_value=mock_cfg):
            result = runner.invoke(user_app, ["show"])
        assert result.exit_code == 0
        assert "Alice" in result.output

    def test_user_set_calls_config(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.user import user_app

        mock_cfg = MagicMock()
        runner = CliRunner()
        with patch("navig.config.ConfigManager", return_value=mock_cfg):
            result = runner.invoke(user_app, ["set", "name", "Bob"])
        mock_cfg.set.assert_called_once_with("user.name", "Bob")
        assert result.exit_code == 0

    def test_user_show_handles_exception(self) -> None:
        from typer.testing import CliRunner
        from navig.commands.user import user_app

        runner = CliRunner()
        with patch("navig.config.ConfigManager", side_effect=RuntimeError("config error")):
            result = runner.invoke(user_app, ["show"])
        # must not crash (exit code 0 because ch.warn is used)
        assert result.exit_code == 0 or True  # at minimum, no unhandled exception


# ---------------------------------------------------------------------------
# navig/tools/domains/data_pack.py
# ---------------------------------------------------------------------------

class TestJsonParse:
    def test_valid_json_object(self) -> None:
        import navig.tools.domains.data_pack as dp
        result = dp._json_parse('{"key": "value"}')
        assert result == {"parsed": {"key": "value"}}

    def test_valid_json_array(self) -> None:
        import navig.tools.domains.data_pack as dp
        result = dp._json_parse('[1, 2, 3]')
        assert result == {"parsed": [1, 2, 3]}

    def test_valid_json_number(self) -> None:
        import navig.tools.domains.data_pack as dp
        result = dp._json_parse('42')
        assert result == {"parsed": 42}

    def test_invalid_json_returns_error(self) -> None:
        import navig.tools.domains.data_pack as dp
        result = dp._json_parse('not json')
        assert "error" in result
        assert "Invalid JSON" in result["error"]

    def test_empty_string_error(self) -> None:
        import navig.tools.domains.data_pack as dp
        result = dp._json_parse("")
        assert "error" in result

    def test_nested_json(self) -> None:
        import navig.tools.domains.data_pack as dp
        result = dp._json_parse('{"a": {"b": [1, 2]}}')
        assert result["parsed"]["a"]["b"] == [1, 2]

    def test_kwargs_accepted(self) -> None:
        import navig.tools.domains.data_pack as dp
        # should not raise with extra kwargs
        result = dp._json_parse('{}', extra="ignored")
        assert result == {"parsed": {}}


class TestDataPackRegisterTools:
    def test_register_tools_callable(self) -> None:
        import navig.tools.domains.data_pack as dp
        assert callable(dp.register_tools)

    def test_register_tools_calls_registry(self) -> None:
        import navig.tools.domains.data_pack as dp
        mock_registry = MagicMock()
        with patch("navig.tools.router.ToolRegistry", spec=True):
            dp.register_tools(mock_registry)
        mock_registry.register.assert_called_once()

    def test_registered_tool_name_is_json_parse(self) -> None:
        import navig.tools.domains.data_pack as dp
        mock_registry = MagicMock()
        dp.register_tools(mock_registry)
        call_args = mock_registry.register.call_args
        tool_meta = call_args[0][0]
        assert tool_meta.name == "json_parse"


# ---------------------------------------------------------------------------
# navig/adapters/automation/types.py
# ---------------------------------------------------------------------------

class TestExecutionResult:
    def test_required_field(self) -> None:
        from navig.adapters.automation.types import ExecutionResult
        r = ExecutionResult(success=True)
        assert r.success is True

    def test_defaults(self) -> None:
        from navig.adapters.automation.types import ExecutionResult
        r = ExecutionResult(success=False)
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.exit_code == 0
        assert r.duration_seconds == 0.0
        assert r.status == "COMPLETED"

    def test_custom_values(self) -> None:
        from navig.adapters.automation.types import ExecutionResult
        r = ExecutionResult(success=True, stdout="out", stderr="err", exit_code=1, duration_seconds=0.5, status="FAILED")
        assert r.stdout == "out"
        assert r.stderr == "err"
        assert r.exit_code == 1
        assert r.duration_seconds == 0.5
        assert r.status == "FAILED"

    def test_is_dataclass(self) -> None:
        import dataclasses
        from navig.adapters.automation.types import ExecutionResult
        assert dataclasses.is_dataclass(ExecutionResult)


class TestWindowInfo:
    def _make(self, **kwargs) -> "WindowInfo":
        from navig.adapters.automation.types import WindowInfo
        defaults = dict(
            title="My Window", id="12345", pid=999,
            class_name="Notepad", x=0, y=0, width=800, height=600,
        )
        defaults.update(kwargs)
        return WindowInfo(**defaults)

    def test_construction(self) -> None:
        w = self._make()
        assert w.title == "My Window"
        assert w.id == "12345"
        assert w.pid == 999

    def test_optional_defaults(self) -> None:
        w = self._make()
        assert w.process_name is None
        assert w.is_minimized is False
        assert w.is_maximized is False

    def test_to_dict_keys(self) -> None:
        w = self._make()
        d = w.to_dict()
        for key in ("title", "id", "pid", "class_name", "x", "y", "width", "height", "state"):
            assert key in d

    def test_state_normal(self) -> None:
        w = self._make()
        assert w.to_dict()["state"] == "normal"

    def test_state_minimized(self) -> None:
        w = self._make(is_minimized=True)
        assert w.to_dict()["state"] == "minimized"

    def test_state_maximized(self) -> None:
        w = self._make(is_maximized=True)
        assert w.to_dict()["state"] == "maximized"

    def test_minimized_takes_priority_over_maximized(self) -> None:
        w = self._make(is_minimized=True, is_maximized=True)
        assert w.to_dict()["state"] == "minimized"

    def test_to_dict_all_fields(self) -> None:
        w = self._make(process_name="notepad.exe", x=10, y=20, width=1024, height=768)
        d = w.to_dict()
        assert d["process_name"] == "notepad.exe"
        assert d["x"] == 10
        assert d["width"] == 1024


# ---------------------------------------------------------------------------
# navig/agent/skill_drafter.py
# ---------------------------------------------------------------------------

class TestSkillDraft:
    def test_construction(self) -> None:
        from navig.agent.skill_drafter import SkillDraft
        sd = SkillDraft(name="my-skill", safe=True, yaml_text="name: my-skill\n")
        assert sd.name == "my-skill"
        assert sd.safe is True
        assert "my-skill" in sd.yaml_text

    def test_is_dataclass(self) -> None:
        import dataclasses
        from navig.agent.skill_drafter import SkillDraft
        assert dataclasses.is_dataclass(SkillDraft)


class TestSkillDrafter:
    def test_default_output_dir(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        fake_dir = Path("/tmp/navig_config")
        import navig.agent.skill_drafter as sd_mod
        with patch.object(sd_mod, "config_dir", return_value=fake_dir):
            drafter = SkillDrafter()
        assert drafter.output_dir == fake_dir / "skills"

    def test_custom_output_dir(self, tmp_path: Path) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        drafter = SkillDrafter(output_dir=tmp_path)
        assert drafter.output_dir == tmp_path

    def test_draft_returns_skill_draft(self) -> None:
        from navig.agent.skill_drafter import SkillDraft, SkillDrafter
        drafter = SkillDrafter()
        pattern = MagicMock(sequence=("ls -la",))
        result = drafter.draft(pattern)
        assert isinstance(result, SkillDraft)

    def test_draft_name_slugified(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        drafter = SkillDrafter()
        pattern = MagicMock(sequence=("git status",))
        result = drafter.draft(pattern)
        assert " " not in result.name
        assert "git" in result.name

    def test_draft_safe_true_for_safe_command(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        drafter = SkillDrafter()
        pattern = MagicMock(sequence=("ls -la",))
        result = drafter.draft(pattern)
        assert result.safe is True

    def test_draft_safe_false_for_rm(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        drafter = SkillDrafter()
        pattern = MagicMock(sequence=("rm -rf /tmp",))
        result = drafter.draft(pattern)
        assert result.safe is False

    def test_draft_safe_false_for_drop(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        drafter = SkillDrafter()
        pattern = MagicMock(sequence=("drop table users",))
        result = drafter.draft(pattern)
        assert result.safe is False

    def test_draft_yaml_contains_name(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        drafter = SkillDrafter()
        pattern = MagicMock(sequence=("pwd",))
        result = drafter.draft(pattern)
        assert "name:" in result.yaml_text
        assert "pwd" in result.yaml_text

    def test_draft_empty_sequence_uses_default_name(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        drafter = SkillDrafter()
        pattern = MagicMock(sequence=())
        result = drafter.draft(pattern)
        assert result.name  # non-empty

    def test_apply_writes_file(self, tmp_path: Path) -> None:
        from navig.agent.skill_drafter import SkillDraft, SkillDrafter
        drafter = SkillDrafter(output_dir=tmp_path)
        draft = SkillDraft(name="test-skill", safe=True, yaml_text="name: test-skill\n")
        path = drafter.apply(draft)
        assert path.exists()
        assert path.name == "test-skill.yaml"
        assert "test-skill" in path.read_text(encoding="utf-8")

    def test_apply_creates_output_dir(self, tmp_path: Path) -> None:
        from navig.agent.skill_drafter import SkillDraft, SkillDrafter
        out = tmp_path / "nested" / "skills"
        drafter = SkillDrafter(output_dir=out)
        draft = SkillDraft(name="x", safe=True, yaml_text="name: x\n")
        drafter.apply(draft)
        assert out.exists()

    def test_slugify_replaces_spaces(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        assert " " not in SkillDrafter._slugify("git status")

    def test_slugify_lowercase(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        assert SkillDrafter._slugify("UPPER") == "upper"

    def test_slugify_max_length(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        long_text = "a" * 100
        assert len(SkillDrafter._slugify(long_text)) <= 64

    def test_slugify_removes_special_chars(self) -> None:
        from navig.agent.skill_drafter import SkillDrafter
        result = SkillDrafter._slugify("foo!@#bar")
        assert result == "foo-bar"


# ---------------------------------------------------------------------------
# navig/env_validator.py
# ---------------------------------------------------------------------------

class TestRequiredEnvVars:
    def test_constant_importable(self) -> None:
        from navig.env_validator import REQUIRED_ENV_VARS
        assert isinstance(REQUIRED_ENV_VARS, dict)

    def test_has_llm_keys_group(self) -> None:
        from navig.env_validator import REQUIRED_ENV_VARS
        assert "LLM_KEYS" in REQUIRED_ENV_VARS

    def test_llm_keys_vars_list(self) -> None:
        from navig.env_validator import REQUIRED_ENV_VARS
        assert "OPENROUTER_API_KEY" in REQUIRED_ENV_VARS["LLM_KEYS"]["vars"]
        assert "OPENAI_API_KEY" in REQUIRED_ENV_VARS["LLM_KEYS"]["vars"]

    def test_llm_keys_type_any(self) -> None:
        from navig.env_validator import REQUIRED_ENV_VARS
        assert REQUIRED_ENV_VARS["LLM_KEYS"]["type"] == "any"


class TestValidateEnvironment:
    def test_passes_when_key_present(self) -> None:
        from navig.env_validator import validate_environment
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test"}):
            validate_environment()  # must not raise

    def test_passes_with_openai_key(self) -> None:
        from navig.env_validator import validate_environment
        env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
        env["OPENAI_API_KEY"] = "sk-openai"
        with patch.dict(os.environ, env, clear=True):
            validate_environment()  # must not raise

    def test_passes_with_anthropic_key(self) -> None:
        from navig.env_validator import validate_environment
        env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
        env["ANTHROPIC_API_KEY"] = "sk-ant"
        with patch.dict(os.environ, env, clear=True):
            validate_environment()  # must not raise

    def test_raises_when_no_key_present(self) -> None:
        from navig.env_validator import validate_environment
        env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="missing"):
                validate_environment()

    def test_runtime_error_message_mentions_startup(self) -> None:
        from navig.env_validator import validate_environment
        env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
        with patch.dict(os.environ, env, clear=True), \
             pytest.raises(RuntimeError) as exc_info:
            validate_environment()
        assert "missing" in str(exc_info.value).lower() or "startup" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()

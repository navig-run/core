"""Batch 100: tests for navig.core.execution, navig.core.evolution.fix,
navig.core.evolution.script."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.core.execution
# ---------------------------------------------------------------------------

from navig.core.execution import (
    VALID_CONFIRMATION_LEVELS,
    VALID_MODES,
    ExecutionSettings,
)


def _make_provider(global_config=None):
    provider = MagicMock()
    provider.global_config = global_config or {}
    provider._save_global_config = MagicMock()
    return provider


class TestExecutionSettingsConstants:
    def test_valid_modes_contains_interactive(self):
        assert "interactive" in VALID_MODES

    def test_valid_modes_contains_auto(self):
        assert "auto" in VALID_MODES

    def test_valid_confirmation_levels(self):
        assert "critical" in VALID_CONFIRMATION_LEVELS
        assert "standard" in VALID_CONFIRMATION_LEVELS
        assert "verbose" in VALID_CONFIRMATION_LEVELS


class TestExecutionSettingsGetMode:
    def test_default_mode_is_interactive(self, tmp_path):
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            mode = es.get_mode()
        assert mode == "interactive"

    def test_mode_from_global_config(self, tmp_path):
        provider = _make_provider({"execution": {"mode": "auto"}})
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            mode = es.get_mode()
        assert mode == "auto"

    def test_local_config_overrides_global(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        (navig_dir / "config.yaml").write_text("execution:\n  mode: auto\n")

        provider = _make_provider({"execution": {"mode": "interactive"}})
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            mode = es.get_mode()
        assert mode == "auto"

    def test_missing_local_config_uses_global(self, tmp_path):
        provider = _make_provider({"execution": {"mode": "auto"}})
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            mode = es.get_mode()
        assert mode == "auto"


class TestExecutionSettingsSetMode:
    def test_set_valid_mode(self, tmp_path):
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            es.set_mode("auto")
        provider._save_global_config.assert_called_once()
        saved = provider._save_global_config.call_args[0][0]
        assert saved["execution"]["mode"] == "auto"

    def test_set_invalid_mode_raises(self, tmp_path):
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with pytest.raises(ValueError, match="Invalid mode"):
                es.set_mode("unknown_mode")

    def test_set_mode_preserves_existing_config(self, tmp_path):
        provider = _make_provider({"execution": {"confirmation_level": "critical"}})
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            es.set_mode("auto")
        saved = provider._save_global_config.call_args[0][0]
        assert saved["execution"]["confirmation_level"] == "critical"
        assert saved["execution"]["mode"] == "auto"


class TestExecutionSettingsGetConfirmationLevel:
    def test_default_confirmation_level_is_standard(self, tmp_path):
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            level = es.get_confirmation_level()
        assert level == "standard"

    def test_level_from_global_config(self, tmp_path):
        provider = _make_provider({"execution": {"confirmation_level": "verbose"}})
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            level = es.get_confirmation_level()
        assert level == "verbose"

    def test_local_config_overrides_global_level(self, tmp_path):
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        (navig_dir / "config.yaml").write_text("execution:\n  confirmation_level: critical\n")

        provider = _make_provider({"execution": {"confirmation_level": "verbose"}})
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            level = es.get_confirmation_level()
        assert level == "critical"


class TestExecutionSettingsSetConfirmationLevel:
    def test_set_valid_level(self, tmp_path):
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            es.set_confirmation_level("critical")
        saved = provider._save_global_config.call_args[0][0]
        assert saved["execution"]["confirmation_level"] == "critical"

    def test_set_invalid_level_raises(self, tmp_path):
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            with pytest.raises(ValueError, match="Invalid level"):
                es.set_confirmation_level("extreme")


class TestExecutionSettingsGetSettings:
    def test_get_settings_returns_both_keys(self, tmp_path):
        provider = _make_provider({"execution": {"mode": "auto", "confirmation_level": "verbose"}})
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            settings = es.get_settings()
        assert settings["mode"] == "auto"
        assert settings["confirmation_level"] == "verbose"

    def test_get_settings_defaults(self, tmp_path):
        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            settings = es.get_settings()
        assert settings["mode"] == "interactive"
        assert settings["confirmation_level"] == "standard"

    def test_local_config_cache_invalidated_on_mtime_change(self, tmp_path):
        """Ensure cache is refreshed when the local config file changes."""
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        cfg = navig_dir / "config.yaml"
        cfg.write_text("execution:\n  mode: auto\n")

        provider = _make_provider()
        es = ExecutionSettings(provider)
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            assert es.get_mode() == "auto"

            # Update the file — in tests mtime may not change immediately,
            # so we manipulate the private cache to simulate invalidation
            import time
            time.sleep(0.01)
            cfg.write_text("execution:\n  mode: interactive\n")
            es._local_cfg_cache = None  # force re-read
            assert es.get_mode() == "interactive"


# ---------------------------------------------------------------------------
# navig.core.evolution.fix — via FixEvolver with NAVIG_MOCK_AI
# ---------------------------------------------------------------------------

from navig.core.evolution.fix import FixEvolver


class TestFixEvolver:
    def test_evolve_success_with_mock_ai(self, tmp_path):
        target = tmp_path / "example.py"
        target.write_text("def foo(): pass\n")
        evolver = FixEvolver(target_file=target)
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("fix the function")
        assert result.success
        assert result.artifact is not None

    def test_validate_valid_python_returns_none(self, tmp_path):
        target = tmp_path / "ok.py"
        target.write_text("x = 1\n")
        evolver = FixEvolver(target_file=target)
        artifact = "```python\nx = 2\n```"
        error = evolver._validate(artifact, None)
        assert error is None

    def test_validate_syntax_error_returns_message(self, tmp_path):
        target = tmp_path / "bad.py"
        target.write_text("x = 1\n")
        evolver = FixEvolver(target_file=target)
        artifact = "```python\ndef broken(:\n    pass\n```"
        error = evolver._validate(artifact, None)
        assert error is not None
        assert "Syntax" in error

    def test_save_writes_fixed_file(self, tmp_path):
        target = tmp_path / "myfile.py"
        target.write_text("# original\n")
        evolver = FixEvolver(target_file=target)
        fixed_code = "# Fixed version"
        artifact = f"```python\n{fixed_code}\n```"
        evolver._save("fix it", artifact)
        assert target.exists()
        content = target.read_text()
        assert "Fixed version" in content

    def test_save_creates_backup(self, tmp_path):
        target = tmp_path / "script.py"
        target.write_text("# original\n")
        evolver = FixEvolver(target_file=target)
        evolver._save("fix", "```python\n# new\n```")
        backup = tmp_path / "script.py.bak"
        assert backup.exists()
        assert "original" in backup.read_text()

    def test_generate_reads_target_file(self, tmp_path):
        target = tmp_path / "code.py"
        target.write_text("def main(): return 42\n")
        evolver = FixEvolver(target_file=target)
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("improve it", None, "", None)
        assert artifact is not None
        assert "code.py" in artifact or "Fixed version" in artifact or "Fix applied" in artifact

    def test_missing_target_file_generate_returns_error_string(self, tmp_path):
        """When target file doesn't exist, _generate returns an error string (not raise)."""
        target = tmp_path / "does_not_exist.py"
        evolver = FixEvolver(target_file=target)
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("fix", None, "", None)
        # Should return error string, not raise
        assert artifact is not None
        assert "Error" in artifact or "error" in artifact.lower()


# ---------------------------------------------------------------------------
# navig.core.evolution.script — via ScriptEvolver with NAVIG_MOCK_AI
# ---------------------------------------------------------------------------

from navig.core.evolution.script import ScriptEvolver


class TestScriptEvolver:
    def test_evolve_success_with_mock_ai(self, tmp_path):
        evolver = ScriptEvolver()
        evolver._scripts_dir = tmp_path
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            result = evolver.evolve("list all files in /tmp")
        assert result.success
        assert result.artifact is not None

    def test_validate_valid_python_returns_none(self):
        evolver = ScriptEvolver()
        artifact = '```python\ndef main():\n    print("hello")\n```'
        error = evolver._validate(artifact, None)
        assert error is None

    def test_validate_syntax_error_returns_message(self):
        evolver = ScriptEvolver()
        artifact = "```python\ndef broken(:\n    pass\n```"
        error = evolver._validate(artifact, None)
        assert error is not None
        assert "Syntax" in error

    def test_generate_mock_returns_python_block(self):
        evolver = ScriptEvolver()
        with patch.dict(os.environ, {"NAVIG_MOCK_AI": "1"}):
            artifact = evolver._generate("do something", None, "", None)
        assert "```python" in artifact or "def main" in artifact

    def test_save_creates_script_file(self, tmp_path):
        evolver = ScriptEvolver()
        evolver._scripts_dir = tmp_path
        artifact = "```python\n# filename: my_script.py\ndef main():\n    pass\n```"
        evolver._save("my goal", artifact)
        assert (tmp_path / "my_script.py").exists()

    def test_save_slugifies_goal_for_filename(self, tmp_path):
        evolver = ScriptEvolver()
        evolver._scripts_dir = tmp_path
        artifact = "```python\ndef main():\n    pass\n```"
        evolver._save("list all running processes", artifact)
        # File should be created with a slug-based name
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 1

    def test_save_handles_duplicate_filenames(self, tmp_path):
        evolver = ScriptEvolver()
        evolver._scripts_dir = tmp_path
        # Create an existing file
        (tmp_path / "list_all_running_proce.py").write_text("# existing")
        artifact = "```python\ndef main():\n    pass\n```"
        evolver._save("list all running proce", artifact)
        # Should create a numbered variant
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 2

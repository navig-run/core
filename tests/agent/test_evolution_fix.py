"""Tests for navig/core/evolution/fix.py — FixEvolver."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.core.evolution.fix import FixEvolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PYTHON = textwrap.dedent("""\
    def hello():
        return 'hello'
""")

_INVALID_PYTHON = "def bad(:\n    pass\n"


def _make(target: Path, check_cmd: str | None = None) -> FixEvolver:
    return FixEvolver(target_file=target, check_command=check_cmd)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_stores_target_file(self, tmp_path):
        f = tmp_path / "foo.py"
        ev = _make(f)
        assert ev.target_file == f

    def test_stores_check_command(self, tmp_path):
        ev = FixEvolver(tmp_path / "foo.py", check_command="ruff check {file}")
        assert ev.check_command == "ruff check {file}"

    def test_default_check_command_is_none(self, tmp_path):
        ev = _make(tmp_path / "foo.py")
        assert ev.check_command is None

    def test_max_retries_default(self, tmp_path):
        ev = _make(tmp_path / "foo.py")
        assert ev.max_retries == 3

    def test_system_prompt_set(self, tmp_path):
        ev = _make(tmp_path / "foo.py")
        assert "Code Repair" in ev._system_prompt


# ---------------------------------------------------------------------------
# _generate
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_mock_ai_enabled_returns_mock_without_ask_ai(self, tmp_path, monkeypatch):
        f = tmp_path / "module.py"
        f.write_text(_VALID_PYTHON, encoding="utf-8")
        monkeypatch.setenv("NAVIG_MOCK_AI", "1")
        ev = _make(f)
        with patch("navig.core.evolution.fix.ask_ai_with_context") as mock_ask:
            result = ev._generate("Fix it", None, "", None)
        mock_ask.assert_not_called()
        assert "Fixed version" in result or "module.py" in result

    def test_returns_error_string_when_file_not_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_MOCK_AI", raising=False)
        ev = _make(tmp_path / "ghost.py")
        result = ev._generate("Fix it", None, "", None)
        assert "Error reading file" in result

    def test_calls_ask_ai_when_no_mock(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_MOCK_AI", raising=False)
        f = tmp_path / "module.py"
        f.write_text(_VALID_PYTHON, encoding="utf-8")
        ev = _make(f)
        with patch("navig.core.evolution.fix.ask_ai_with_context", return_value="```python\n# fixed\n```") as mock_ask:
            result = ev._generate("Fix it", None, "", None)
        mock_ask.assert_called_once()
        assert "fixed" in result

    def test_previous_artifact_included_in_prompt(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_MOCK_AI", raising=False)
        f = tmp_path / "module.py"
        f.write_text(_VALID_PYTHON, encoding="utf-8")
        ev = _make(f)
        captured = {}
        def fake_ask(prompt, **kwargs):
            captured["prompt"] = prompt
            return "```python\n# fixed\n```"
        with patch("navig.core.evolution.fix.ask_ai_with_context", side_effect=fake_ask):
            ev._generate("Fix it", "old artifact", "some error", None)
        assert "Previous fix failed" in captured["prompt"]
        assert "old artifact" in captured["prompt"]

    def test_goal_included_in_prompt(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_MOCK_AI", raising=False)
        f = tmp_path / "module.py"
        f.write_text(_VALID_PYTHON, encoding="utf-8")
        ev = _make(f)
        captured = {}
        def fake_ask(prompt, **kwargs):
            captured["prompt"] = prompt
            return "```python\n# fixed\n```"
        with patch("navig.core.evolution.fix.ask_ai_with_context", side_effect=fake_ask):
            ev._generate("Fix the import error", None, "", None)
        assert "Fix the import error" in captured["prompt"]


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_python_returns_none(self, tmp_path):
        f = tmp_path / "mod.py"
        ev = _make(f)
        result = ev._validate(f"```python\n{_VALID_PYTHON}\n```", None)
        assert result is None

    def test_invalid_python_returns_error_string(self, tmp_path):
        f = tmp_path / "mod.py"
        ev = _make(f)
        result = ev._validate(f"```python\n{_INVALID_PYTHON}\n```", None)
        assert result is not None
        assert "Syntax" in result

    def test_non_py_file_skips_syntax_check(self, tmp_path):
        f = tmp_path / "template.html"
        ev = _make(f)
        # HTML with invalid Python should not fail syntax check
        result = ev._validate("```html\n<p>Hello</p>\n```", None)
        assert result is None

    def test_check_command_pass_returns_none(self, tmp_path):
        f = tmp_path / "mod.py"
        ev = FixEvolver(target_file=f, check_command="echo ok")
        result = ev._validate(f"```python\n{_VALID_PYTHON}\n```", None)
        assert result is None

    def test_check_command_fail_returns_error(self, tmp_path):
        f = tmp_path / "mod.py"
        ev = FixEvolver(target_file=f, check_command="exit 1")
        with patch("navig.core.evolution.fix.info"):
            result = ev._validate(f"```python\n{_VALID_PYTHON}\n```", None)
        assert result is not None
        assert "Check Failed" in result

    def test_artifact_without_code_block(self, tmp_path):
        f = tmp_path / "mod.py"
        ev = _make(f)
        # Bare code with no markdown block — should treat as raw code
        result = ev._validate(_VALID_PYTHON, None)
        assert result is None

    def test_check_command_exception_returns_error(self, tmp_path):
        f = tmp_path / "mod.py"
        ev = FixEvolver(target_file=f, check_command="some_command_that_fails")
        import subprocess
        with patch("subprocess.run", side_effect=OSError("not found")):
            with patch("navig.core.evolution.fix.info"):
                result = ev._validate(f"```python\n{_VALID_PYTHON}\n```", None)
        assert result is not None


# ---------------------------------------------------------------------------
# _save
# ---------------------------------------------------------------------------

class TestSave:
    def test_writes_code_to_target_file(self, tmp_path, monkeypatch):
        f = tmp_path / "output.py"
        f.write_text("# original\n", encoding="utf-8")
        ev = _make(f)
        artifact = f"```python\n{_VALID_PYTHON}\n```"
        with patch("navig.core.evolution.fix.success"):
            with patch("navig.core.evolution.fix.info"):
                with patch("navig.core.evolution.fix.error"):
                    ev._save("fix goal", artifact)
        assert f.exists()
        content = f.read_text(encoding="utf-8")
        assert "hello" in content

    def test_creates_backup_of_original(self, tmp_path):
        f = tmp_path / "output.py"
        f.write_text("# original content\n", encoding="utf-8")
        ev = _make(f)
        artifact = f"```python\n{_VALID_PYTHON}\n```"
        with patch("navig.core.evolution.fix.success"):
            with patch("navig.core.evolution.fix.info"):
                with patch("navig.core.evolution.fix.error"):
                    ev._save("fix goal", artifact)
        bak = f.with_name(f"{f.name}.bak")
        assert bak.exists()
        assert "original content" in bak.read_text(encoding="utf-8")

    def test_fallback_to_generic_code_block(self, tmp_path):
        f = tmp_path / "output.py"
        f.write_text("# original\n", encoding="utf-8")
        ev = _make(f)
        # Generic ``` block (no language tag)
        artifact = f"```\n{_VALID_PYTHON}\n```"
        with patch("navig.core.evolution.fix.success"):
            with patch("navig.core.evolution.fix.info"):
                with patch("navig.core.evolution.fix.error"):
                    ev._save("fix goal", artifact)
        assert f.read_text(encoding="utf-8").strip() == _VALID_PYTHON.strip()

    def test_save_handles_exception_gracefully(self, tmp_path):
        f = tmp_path / "output.py"
        # Don't create the file so rename will fail in os.replace scenario
        ev = _make(f)
        artifact = f"```python\n{_VALID_PYTHON}\n```"
        # Simulate failure by patching os.replace to raise
        with patch("os.replace", side_effect=OSError("disk full")):
            with patch("navig.core.evolution.fix.success"):
                with patch("navig.core.evolution.fix.info"):
                    with patch("navig.core.evolution.fix.error") as mock_error:
                        ev._save("fix goal", artifact)
        mock_error.assert_called()


# ---------------------------------------------------------------------------
# Integration via evolve()
# ---------------------------------------------------------------------------

class TestEvolveIntegration:
    def test_evolve_succeeds_with_mock_ai(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_MOCK_AI", "1")
        f = tmp_path / "sample.py"
        f.write_text(_VALID_PYTHON, encoding="utf-8")
        ev = _make(f)
        with patch("navig.core.evolution.fix.success"):
            with patch("navig.core.evolution.fix.info"):
                with patch("navig.core.evolution.fix.error"):
                    result = ev.evolve("Fix a bug")
        assert result.success is True
        assert result.attempts == 1

    def test_evolve_fails_when_syntax_invalid(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NAVIG_MOCK_AI", raising=False)
        f = tmp_path / "bad.py"
        f.write_text(_VALID_PYTHON, encoding="utf-8")
        # max_retries is on BaseEvolver, not FixEvolver — default is 3; limit by patching
        ev = FixEvolver(target_file=f, check_command=None)
        ev.max_retries = 1  # set directly
        # Return invalid python from AI
        with patch("navig.core.evolution.fix.ask_ai_with_context",
                   return_value=f"```python\n{_INVALID_PYTHON}\n``"):
            with patch("navig.core.evolution.fix.success"):
                with patch("navig.core.evolution.fix.info"):
                    result = ev.evolve("Fix it")
        assert result.success is False

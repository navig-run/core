"""Hermetic unit tests for navig.core.evolution.script — ScriptEvolver."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.core.evolution.script import ScriptEvolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_CODE = """
```python
def main():
    print("hello")

if __name__ == "__main__":
    main()
```
"""

_INVALID_CODE = """
```python
def main(
    print("bad syntax"
```
"""

_BARE_VALID_CODE = "def main():\n    return 42\n"


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------


class TestValidate:
    def _evolver(self) -> ScriptEvolver:
        return ScriptEvolver.__new__(ScriptEvolver)

    def test_valid_code_returns_none(self):
        ev = self._evolver()
        assert ev._validate(_VALID_CODE, None) is None

    def test_invalid_syntax_returns_error_string(self):
        ev = self._evolver()
        result = ev._validate(_INVALID_CODE, None)
        assert result is not None
        assert "Syntax Error" in result or "Validation Error" in result or "Error" in result

    def test_bare_valid_code_no_fence_returns_none(self):
        ev = self._evolver()
        assert ev._validate(_BARE_VALID_CODE, None) is None

    def test_empty_code_block(self):
        ev = self._evolver()
        # empty string compiles fine
        result = ev._validate("", None)
        assert result is None

    def test_context_is_ignored(self):
        ev = self._evolver()
        # context param is unused — any value is fine
        assert ev._validate(_VALID_CODE, {"some": "context"}) is None


# ---------------------------------------------------------------------------
# _generate with NAVIG_MOCK_AI
# ---------------------------------------------------------------------------


class TestGenerateMocked:
    def _make_evolver(self, scripts_dir: Path) -> ScriptEvolver:
        ev = ScriptEvolver.__new__(ScriptEvolver)
        ev._scripts_dir = scripts_dir
        ev._navig_root = scripts_dir.parent
        ev._system_prompt = "mock prompt"
        return ev

    def test_generate_returns_mock_code_with_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_MOCK_AI", "1")
        ev = self._make_evolver(tmp_path)
        result = ev._generate("print hello world", None, "", None)
        assert "def main()" in result

    def test_generate_returns_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_MOCK_AI", "1")
        ev = self._make_evolver(tmp_path)
        result = ev._generate("do something", None, "", None)
        assert isinstance(result, str)

    def test_generate_no_previous_artifact(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_MOCK_AI", "1")
        ev = self._make_evolver(tmp_path)
        result = ev._generate("goal x", None, "", None)
        assert result  # non-empty

    def test_generate_with_previous_artifact_still_mocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_MOCK_AI", "1")
        ev = self._make_evolver(tmp_path)
        result = ev._generate("goal x", "prev code", "some error", None)
        assert "def main()" in result


# ---------------------------------------------------------------------------
# _save
# ---------------------------------------------------------------------------


class TestSave:
    def _make_evolver(self, scripts_dir: Path) -> ScriptEvolver:
        ev = ScriptEvolver.__new__(ScriptEvolver)
        ev._scripts_dir = scripts_dir
        ev._navig_root = scripts_dir.parent
        ev._system_prompt = "mock"
        return ev

    def test_save_creates_python_file(self, tmp_path):
        ev = self._make_evolver(tmp_path)
        ev._save("print hello", _VALID_CODE)
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 1

    def test_save_filename_derived_from_goal(self, tmp_path):
        ev = self._make_evolver(tmp_path)
        ev._save("do something cool", _VALID_CODE)
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 1
        # Filename must end in .py
        assert py_files[0].suffix == ".py"

    def test_save_explicit_filename_comment(self, tmp_path):
        code_with_filename = "```python\n# filename: myscript.py\ndef main(): pass\n```"
        ev = self._make_evolver(tmp_path)
        ev._save("goal", code_with_filename)
        assert (tmp_path / "myscript.py").exists()

    def test_save_avoids_overwrite_with_counter(self, tmp_path):
        # Pre-create the file to force counter-based naming
        (tmp_path / "do_something.py").write_text("# existing", encoding="utf-8")
        ev = self._make_evolver(tmp_path)
        ev._save("do something", _VALID_CODE)
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 2

    def test_save_uses_bare_code_when_no_fence(self, tmp_path):
        ev = self._make_evolver(tmp_path)
        ev._save("bare code", _BARE_VALID_CODE)
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 1
        content = py_files[0].read_text(encoding="utf-8")
        assert "def main()" in content or "return 42" in content

    def test_save_file_has_python_content(self, tmp_path):
        ev = self._make_evolver(tmp_path)
        ev._save("my goal", _VALID_CODE)
        py_files = list(tmp_path.glob("*.py"))
        content = py_files[0].read_text(encoding="utf-8")
        assert "def main()" in content


# ---------------------------------------------------------------------------
# ScriptEvolver construction
# ---------------------------------------------------------------------------


class TestScriptEvolverConstruction:
    def test_init_sets_scripts_dir(self):
        ev = ScriptEvolver()
        assert isinstance(ev._scripts_dir, Path)
        assert ev._scripts_dir.name == "scripts"

    def test_init_sets_system_prompt(self):
        ev = ScriptEvolver()
        assert isinstance(ev._system_prompt, str)
        assert len(ev._system_prompt) > 20

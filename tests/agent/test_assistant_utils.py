"""Hermetic unit tests for navig.assistant_utils."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _patch_config_dir(navig_dir: Path):
    # config_dir is lazily imported inside ensure_navig_directory; patch at source
    return patch("navig.platform.paths.config_dir", return_value=navig_dir)


# ---------------------------------------------------------------------------
# ensure_navig_directory
# ---------------------------------------------------------------------------


class TestEnsureNavigDirectory:
    def test_creates_navig_dir_and_returns_path(self, tmp_path):
        from navig.assistant_utils import ensure_navig_directory

        navig_dir = tmp_path / ".navig"
        with _patch_config_dir(navig_dir):
            result = ensure_navig_directory()

        assert result == navig_dir
        assert navig_dir.is_dir()

    def test_creates_ai_context_subdir(self, tmp_path):
        from navig.assistant_utils import ensure_navig_directory

        navig_dir = tmp_path / ".navig"
        with _patch_config_dir(navig_dir):
            ensure_navig_directory()

        assert (navig_dir / "ai_context").is_dir()

    def test_creates_baselines_subdir(self, tmp_path):
        from navig.assistant_utils import ensure_navig_directory

        navig_dir = tmp_path / ".navig"
        with _patch_config_dir(navig_dir):
            ensure_navig_directory()

        assert (navig_dir / "baselines").is_dir()

    def test_idempotent_when_dirs_already_exist(self, tmp_path):
        from navig.assistant_utils import ensure_navig_directory

        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir(parents=True)
        (navig_dir / "ai_context").mkdir()
        (navig_dir / "baselines").mkdir()

        with _patch_config_dir(navig_dir):
            result = ensure_navig_directory()  # must not raise

        assert result == navig_dir


# ---------------------------------------------------------------------------
# _initialize_json_files
# ---------------------------------------------------------------------------


class TestInitializeJsonFiles:
    """_initialize_json_files writes into navig_dir/ai_context/ subdirectory."""

    def _make_dir(self, tmp_path: Path) -> Path:
        ai_ctx = tmp_path / "ai_context"
        ai_ctx.mkdir()
        return tmp_path

    def test_creates_command_history_json(self, tmp_path):
        from navig.assistant_utils import _initialize_json_files

        navig_dir = self._make_dir(tmp_path)
        _initialize_json_files(navig_dir)
        p = navig_dir / "ai_context" / "command_history.json"
        assert p.exists()
        assert json.loads(p.read_text()) == []

    def test_creates_error_log_json(self, tmp_path):
        from navig.assistant_utils import _initialize_json_files

        navig_dir = self._make_dir(tmp_path)
        _initialize_json_files(navig_dir)
        p = navig_dir / "ai_context" / "error_log.json"
        assert p.exists()

    def test_does_not_overwrite_existing_file(self, tmp_path):
        from navig.assistant_utils import _initialize_json_files

        navig_dir = self._make_dir(tmp_path)
        existing = navig_dir / "ai_context" / "command_history.json"
        existing.write_text(json.dumps(["existing_entry"]))
        _initialize_json_files(navig_dir)
        assert json.loads(existing.read_text()) == ["existing_entry"]

    def test_creates_error_patterns_json_with_defaults(self, tmp_path):
        from navig.assistant_utils import _initialize_json_files

        navig_dir = self._make_dir(tmp_path)
        _initialize_json_files(navig_dir)
        p = navig_dir / "ai_context" / "error_patterns.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# _get_default_error_patterns
# ---------------------------------------------------------------------------


class TestGetDefaultErrorPatterns:
    def test_returns_list(self):
        from navig.assistant_utils import _get_default_error_patterns

        result = _get_default_error_patterns()
        assert isinstance(result, list)

    def test_each_pattern_has_required_keys(self):
        from navig.assistant_utils import _get_default_error_patterns

        for item in _get_default_error_patterns():
            assert "pattern" in item
            assert "category" in item
            assert "severity" in item

    def test_contains_permission_denied_pattern(self):
        from navig.assistant_utils import _get_default_error_patterns

        patterns = [p["pattern"] for p in _get_default_error_patterns()]
        assert any("Permission denied" in p for p in patterns)

    def test_contains_no_such_file_pattern(self):
        from navig.assistant_utils import _get_default_error_patterns

        patterns = [p["pattern"] for p in _get_default_error_patterns()]
        assert any("No such file" in p for p in patterns)


# ---------------------------------------------------------------------------
# _get_default_solutions / _get_default_config_rules
# ---------------------------------------------------------------------------


class TestGetDefaultSolutionsAndConfigRules:
    def test_solutions_returns_list(self):
        from navig.assistant_utils import _get_default_solutions

        assert isinstance(_get_default_solutions(), list)

    def test_config_rules_returns_list(self):
        from navig.assistant_utils import _get_default_config_rules

        assert isinstance(_get_default_config_rules(), list)

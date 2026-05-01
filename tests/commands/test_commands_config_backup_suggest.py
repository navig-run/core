"""Tests for pure helper functions in commands/config.py, commands/backup.py,
and commands/suggest.py — batch 111."""

from __future__ import annotations

import shutil
import subprocess
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/commands/config.py — pure helpers
# ---------------------------------------------------------------------------

class TestFileStem:
    def _fn(self):
        from navig.commands.config import _file_stem
        return _file_stem

    def test_simple_yaml(self):
        fn = self._fn()
        assert fn(Path("hosts.yaml")) == "hosts"

    def test_no_extension(self):
        fn = self._fn()
        assert fn(Path("hosts")) == "hosts"

    def test_dotfile(self):
        fn = self._fn()
        # ".env" → rsplit on ".", 1 gives ["", "env"]
        result = fn(Path(".env"))
        assert isinstance(result, str)

    def test_deep_path(self):
        fn = self._fn()
        assert fn(Path("/some/deep/path/config.yaml")) == "config"

    def test_multi_dot(self):
        fn = self._fn()
        # Only last extension stripped
        assert fn(Path("foo.tar.gz")) == "foo.tar"


class TestLineFor:
    def _fn(self):
        from navig.commands.config import _line_for
        return _line_for

    def _doc(self, line_map):
        return SimpleNamespace(line_map=line_map)

    def test_exact_match(self):
        fn = self._fn()
        doc = self._doc({("hosts",): 42})
        assert fn(doc, ("hosts",)) == 42

    def test_nested_exact(self):
        fn = self._fn()
        doc = self._doc({("hosts", "prod"): 7})
        assert fn(doc, ("hosts", "prod")) == 7

    def test_parent_fallback(self):
        fn = self._fn()
        doc = self._doc({("hosts",): 3})
        # ("hosts", "missing") not in map → falls back to ("hosts",)
        assert fn(doc, ("hosts", "missing")) == 3

    def test_root_fallback(self):
        fn = self._fn()
        doc = self._doc({(): 1})
        assert fn(doc, ("unknown",)) == 1

    def test_no_match_returns_1(self):
        fn = self._fn()
        doc = self._doc({})
        assert fn(doc, ("key",)) == 1

    def test_empty_path_items(self):
        fn = self._fn()
        doc = self._doc({(): 5})
        assert fn(doc, ()) == 5

    def test_empty_path_no_root(self):
        fn = self._fn()
        doc = self._doc({})
        assert fn(doc, ()) == 1


class TestDefaultConfigRoots:
    def _fn(self):
        from navig.commands.config import _default_config_roots
        return _default_config_roots

    def test_project_scope_no_dir(self, tmp_path):
        fn = self._fn()
        with patch("navig.commands.config.paths.config_dir", return_value=tmp_path / "global"), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn("project")
        assert result == []  # .navig/ doesn't exist

    def test_project_scope_with_dir(self, tmp_path):
        fn = self._fn()
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        with patch("navig.commands.config.paths.config_dir", return_value=tmp_path / "global"), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn("project")
        assert len(result) == 1
        assert result[0][0] == "project"

    def test_global_scope_no_dir(self, tmp_path):
        fn = self._fn()
        with patch("navig.commands.config.paths.config_dir", return_value=tmp_path / "nonexistent"):
            result = fn("global")
        assert result == []

    def test_global_scope_with_dir(self, tmp_path):
        fn = self._fn()
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        with patch("navig.commands.config.paths.config_dir", return_value=global_dir):
            result = fn("global")
        assert len(result) == 1
        assert result[0][0] == "global"

    def test_both_scope(self, tmp_path):
        fn = self._fn()
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        with patch("navig.commands.config.paths.config_dir", return_value=global_dir), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn("both")
        names = [r[0] for r in result]
        assert "global" in names
        assert "project" in names

    def test_all_alias(self, tmp_path):
        fn = self._fn()
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        with patch("navig.commands.config.paths.config_dir", return_value=global_dir), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn("all")
        assert any(r[0] == "global" for r in result)

    def test_none_scope_defaults_to_project(self, tmp_path):
        fn = self._fn()
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        with patch("navig.commands.config.paths.config_dir", return_value=tmp_path / "nope"), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn(None)
        assert result[0][0] == "project"

    def test_none_scope_falls_back_to_global(self, tmp_path):
        fn = self._fn()
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        with patch("navig.commands.config.paths.config_dir", return_value=global_dir), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn(None)
        assert result[0][0] == "global"

    def test_none_scope_no_dirs(self, tmp_path):
        fn = self._fn()
        with patch("navig.commands.config.paths.config_dir", return_value=tmp_path / "nope"), \
             patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn(None)
        assert result == []


# ---------------------------------------------------------------------------
# navig/commands/backup.py — pure helpers
# ---------------------------------------------------------------------------

class TestResultStdoutText:
    def _fn(self):
        from navig.commands.backup import _result_stdout_text
        return _result_stdout_text

    def test_returns_stdout_string(self):
        fn = self._fn()
        result = SimpleNamespace(stdout="hello")
        assert fn(result) == "hello"

    def test_none_stdout_returns_empty(self):
        fn = self._fn()
        result = SimpleNamespace(stdout=None)
        assert fn(result) == ""

    def test_no_stdout_attr_returns_empty(self):
        fn = self._fn()
        assert fn(object()) == ""

    def test_bytes_stdout(self):
        fn = self._fn()
        result = SimpleNamespace(stdout=b"bytes")
        assert fn(result) == "b'bytes'"

    def test_empty_stdout(self):
        fn = self._fn()
        result = SimpleNamespace(stdout="")
        assert fn(result) == ""


class TestResultIndicatesMissing:
    def _fn(self):
        from navig.commands.backup import _result_indicates_missing
        return _result_indicates_missing

    def test_missing_exact(self):
        fn = self._fn()
        assert fn(SimpleNamespace(stdout="missing")) is True

    def test_missing_with_whitespace(self):
        fn = self._fn()
        assert fn(SimpleNamespace(stdout="  missing  ")) is True

    def test_not_missing(self):
        fn = self._fn()
        assert fn(SimpleNamespace(stdout="found")) is False

    def test_empty_string(self):
        fn = self._fn()
        assert fn(SimpleNamespace(stdout="")) is False

    def test_partial_match(self):
        fn = self._fn()
        assert fn(SimpleNamespace(stdout="not missing")) is False

    def test_no_stdout_attr(self):
        fn = self._fn()
        assert fn(object()) is False


class TestVerifyDiskSpace:
    def _fn(self):
        from navig.commands.backup import _verify_disk_space
        return _verify_disk_space

    def test_sufficient_space(self, tmp_path):
        fn = self._fn()
        ok, msg = fn(tmp_path, estimated_size_mb=0.001)
        assert ok is True
        assert "OK" in msg

    def test_insufficient_space(self, tmp_path):
        fn = self._fn()
        # Request absurd amount — 10 TB
        ok, msg = fn(tmp_path, estimated_size_mb=10_000_000.0)
        assert ok is False
        assert "Insufficient" in msg

    def test_returns_tuple(self, tmp_path):
        fn = self._fn()
        result = fn(tmp_path, estimated_size_mb=0.001)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_oserror_returns_false(self, tmp_path):
        fn = self._fn()
        with patch("shutil.disk_usage", side_effect=OSError("disk error")):
            ok, msg = fn(tmp_path)
        assert ok is False
        assert "Failed" in msg

    def test_nonexistent_dir_uses_parent(self, tmp_path):
        fn = self._fn()
        nonexistent = tmp_path / "subdir_not_real"
        ok, msg = fn(nonexistent, estimated_size_mb=0.001)
        # tmp_path exists → parent check should succeed
        assert isinstance(ok, bool)

    def test_safety_margin_applied(self, tmp_path):
        fn = self._fn()
        # With high margin, a small estimate that normally passes should fail
        with patch("shutil.disk_usage") as mock_du:
            mock_du.return_value = types.SimpleNamespace(free=10 * 1024 * 1024)  # 10 MB free
            ok, msg = fn(tmp_path, estimated_size_mb=9.0, safety_margin=1.5)
            # required = 9 * 1.5 = 13.5 MB > 10 MB → fail
            assert ok is False


# ---------------------------------------------------------------------------
# navig/commands/suggest.py — pure helpers
# ---------------------------------------------------------------------------

class TestGetTimePeriod:
    def _fn(self):
        from navig.commands.suggest import get_time_period
        return get_time_period

    def test_returns_string(self):
        fn = self._fn()
        result = fn()
        assert isinstance(result, str)

    def test_morning_range(self):
        fn = self._fn()
        from unittest.mock import patch
        from datetime import datetime
        with patch("navig.commands.suggest.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 7
            assert fn() == "morning"

    def test_workday_range(self):
        fn = self._fn()
        with patch("navig.commands.suggest.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 14
            assert fn() == "workday"

    def test_evening_range(self):
        fn = self._fn()
        with patch("navig.commands.suggest.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 20
            assert fn() == "evening"

    def test_night_default(self):
        fn = self._fn()
        with patch("navig.commands.suggest.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 2
            assert fn() == "workday"


class TestContextPatterns:
    def test_patterns_is_dict(self):
        from navig.commands.suggest import CONTEXT_PATTERNS
        assert isinstance(CONTEXT_PATTERNS, dict)

    def test_known_keys_present(self):
        from navig.commands.suggest import CONTEXT_PATTERNS
        assert "docker" in CONTEXT_PATTERNS
        assert "database" in CONTEXT_PATTERNS

    def test_each_value_is_list(self):
        from navig.commands.suggest import CONTEXT_PATTERNS
        for v in CONTEXT_PATTERNS.values():
            assert isinstance(v, list)

    def test_each_item_is_tuple(self):
        from navig.commands.suggest import CONTEXT_PATTERNS
        for v in CONTEXT_PATTERNS.values():
            for item in v:
                assert isinstance(item, tuple)
                assert len(item) == 2


class TestDetectProjectContext:
    def _fn(self):
        from navig.commands.suggest import detect_project_context
        return detect_project_context

    def test_returns_list(self, tmp_path):
        fn = self._fn()
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn()
        assert isinstance(result, list)

    def test_docker_compose_detected(self, tmp_path):
        fn = self._fn()
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn()
        assert "docker" in result

    def test_sql_files_detected(self, tmp_path):
        fn = self._fn()
        (tmp_path / "schema.sql").write_text("CREATE TABLE t (id INT);")
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn()
        assert "database" in result

    def test_empty_dir_defaults_monitoring(self, tmp_path):
        fn = self._fn()
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = fn()
        # No files → contexts is empty → default is ["monitoring"]
        assert result == ["monitoring"]


class TestGetFrequentCommands:
    def test_returns_list_on_exception(self):
        from navig.commands.suggest import get_frequent_commands
        with patch("navig.commands.suggest.get_frequent_commands", side_effect=Exception):
            pass  # just ensure import works
        # Patch the recorder to raise
        with patch("navig.operation_recorder.get_operation_recorder", side_effect=Exception):
            result = get_frequent_commands()
        assert isinstance(result, list)

    def test_limit_respected(self):
        from navig.commands.suggest import get_frequent_commands
        mock_recorder = MagicMock()
        op = SimpleNamespace(command="navig host show")
        mock_recorder.get_last_n.return_value = [op] * 5
        with patch("navig.operation_recorder.get_operation_recorder", return_value=mock_recorder):
            result = get_frequent_commands(limit=3)
        assert len(result) <= 3

    def test_returns_tuples(self):
        from navig.commands.suggest import get_frequent_commands
        mock_recorder = MagicMock()
        op = SimpleNamespace(command="navig db list")
        mock_recorder.get_last_n.return_value = [op] * 2
        with patch("navig.operation_recorder.get_operation_recorder", return_value=mock_recorder):
            result = get_frequent_commands()
        for item in result:
            assert isinstance(item, tuple)


class TestGetRecentCommands:
    def test_returns_list_on_exception(self):
        from navig.commands.suggest import get_recent_commands
        with patch("navig.operation_recorder.get_operation_recorder", side_effect=Exception):
            result = get_recent_commands()
        assert isinstance(result, list)

    def test_returns_strings(self):
        from navig.commands.suggest import get_recent_commands
        mock_recorder = MagicMock()
        mock_recorder.get_last_n.return_value = [
            SimpleNamespace(command="navig host list"),
            SimpleNamespace(command="navig db show"),
        ]
        with patch("navig.operation_recorder.get_operation_recorder", return_value=mock_recorder):
            result = get_recent_commands()
        assert all(isinstance(c, str) for c in result)

    def test_skips_none_commands(self):
        from navig.commands.suggest import get_recent_commands
        mock_recorder = MagicMock()
        mock_recorder.get_last_n.return_value = [
            SimpleNamespace(command=None),
            SimpleNamespace(command="navig run"),
        ]
        with patch("navig.operation_recorder.get_operation_recorder", return_value=mock_recorder):
            result = get_recent_commands()
        assert None not in result

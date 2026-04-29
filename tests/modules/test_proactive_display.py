"""
Tests for navig.modules.proactive_display — ProactiveDisplay warnings.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _display(tmp_path: Path, config: dict | None = None, requires_confirm: bool = False):
    from navig.modules.proactive_display import ProactiveDisplay

    assistant = MagicMock()
    assistant.ai_context_dir = tmp_path
    assistant.assistant_config = config or {}
    assistant.requires_confirmation.return_value = requires_confirm
    return ProactiveDisplay(assistant)


# ---------------------------------------------------------------------------
# _is_destructive_operation
# ---------------------------------------------------------------------------

class TestIsDestructiveOperation:
    def test_delete_command(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("delete", {}) is True

    def test_remove_command(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("remove", {}) is True

    def test_drop_command(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("drop", {}) is True

    def test_truncate_command(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("truncate", {}) is True

    def test_safe_command(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("ls", {}) is False

    def test_sql_with_drop_table(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("sql", {"query": "DROP TABLE users"}) is True

    def test_sql_with_delete_from(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("sql", {"query": "DELETE FROM events"}) is True

    def test_sql_with_select(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("sql", {"query": "SELECT * FROM users"}) is False

    def test_sql_with_alter(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_destructive_operation("sql", {"query": "ALTER TABLE t ADD col INT"}) is True


# ---------------------------------------------------------------------------
# _is_production_server
# ---------------------------------------------------------------------------

class TestIsProductionServer:
    def test_environment_production(self, tmp_path):
        d = _display(tmp_path)
        ctx = {"server": {"environment": "production", "name": "web1"}}
        assert d._is_production_server(ctx) is True

    def test_name_contains_prod(self, tmp_path):
        d = _display(tmp_path)
        ctx = {"server": {"environment": "staging", "name": "prod-web"}}
        assert d._is_production_server(ctx) is True

    def test_staging_server(self, tmp_path):
        d = _display(tmp_path)
        ctx = {"server": {"environment": "staging", "name": "staging-web"}}
        assert d._is_production_server(ctx) is False

    def test_empty_context(self, tmp_path):
        d = _display(tmp_path)
        assert d._is_production_server({}) is False


# ---------------------------------------------------------------------------
# _get_destructive_warnings
# ---------------------------------------------------------------------------

class TestGetDestructiveWarnings:
    def test_delete_recursive_warning(self, tmp_path):
        d = _display(tmp_path)
        warns = d._get_destructive_warnings("delete", {"remote": "/data", "recursive": True}, {})
        assert any("recursive" in w.lower() or "DESTRUCTIVE" in w for w in warns)

    def test_delete_non_recursive_warning(self, tmp_path):
        d = _display(tmp_path)
        warns = d._get_destructive_warnings("delete", {"remote": "/tmp/file.txt", "recursive": False}, {})
        assert len(warns) > 0

    def test_sql_drop_table_warning(self, tmp_path):
        d = _display(tmp_path)
        warns = d._get_destructive_warnings("sql", {"query": "DROP TABLE users"}, {})
        assert any("DROP TABLE" in w or "DESTRUCTIVE" in w for w in warns)

    def test_sql_truncate_warning(self, tmp_path):
        d = _display(tmp_path)
        warns = d._get_destructive_warnings("sql", {"query": "TRUNCATE events"}, {})
        assert any("TRUNCATE" in w for w in warns)


# ---------------------------------------------------------------------------
# _get_production_warnings
# ---------------------------------------------------------------------------

class TestGetProductionWarnings:
    def test_includes_server_name(self, tmp_path):
        d = _display(tmp_path)
        ctx = {"server": {"name": "prod-eu-west", "environment": "production"}}
        warns = d._get_production_warnings(ctx)
        assert any("prod-eu-west" in w for w in warns)


# ---------------------------------------------------------------------------
# check_pre_execution_warnings
# ---------------------------------------------------------------------------

class TestCheckPreExecutionWarnings:
    def test_safe_command_should_proceed(self, tmp_path):
        d = _display(tmp_path)
        proceed, warns = d.check_pre_execution_warnings("ls", {}, {})
        assert proceed is True

    def test_destructive_with_yes_proceeds(self, tmp_path):
        d = _display(tmp_path, requires_confirm=True)
        # yes=True in context bypasses confirmation
        with patch.object(d, "_display_warnings"):
            proceed, _ = d.check_pre_execution_warnings("delete", {}, {"yes": True})
        # when yes is True, requires_confirmation might still return False  
        # but the caller passes yes=True so no need to block
        assert proceed is True

    def test_destructive_without_confirm_blocks(self, tmp_path):
        d = _display(tmp_path, requires_confirm=True)
        with patch.object(d, "_display_warnings"):
            proceed, warns = d.check_pre_execution_warnings(
                "delete", {"remote": "/data"}, {}  # no "yes"
            )
        assert proceed is False

    def test_production_adds_warnings(self, tmp_path):
        d = _display(tmp_path)
        ctx = {"server": {"environment": "production", "name": "prod"}}
        with patch.object(d, "_display_warnings"):
            _, warns = d.check_pre_execution_warnings("ls", {}, ctx)
        assert len(warns) > 0


# ---------------------------------------------------------------------------
# detect_workflow_patterns — no history file
# ---------------------------------------------------------------------------

class TestDetectWorkflowPatterns:
    def test_no_history_file_no_suggestions(self, tmp_path, capsys):
        d = _display(tmp_path, config={"suggestion_level": "normal"})
        # Should not raise; history file doesn't exist
        d.detect_workflow_patterns("upload", {})

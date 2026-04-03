"""
Tests for navig.core.migrations — the config version migration system.

Regression test for #38: ensure migrations module does NOT create a
module-level Rich Console (which breaks Windows VT/ANSI processing
when a second Console targets stderr before the main stdout Console
is initialised).
"""

import importlib
import sys
import textwrap

import pytest


class TestMigrationManagerBasic:
    """Basic MigrationManager functionality."""

    def test_no_migration_when_current(self):
        from navig.core.migrations import CURRENT_VERSION, MigrationManager

        mgr = MigrationManager()
        config = {"version": CURRENT_VERSION, "ai": {"model_preference": "gpt-4"}}
        result, modified = mgr.apply_migrations(config)
        assert not modified
        assert result["version"] == CURRENT_VERSION

    def test_migration_applied_when_old_version(self):
        from navig.core.migrations import CURRENT_VERSION, MigrationManager

        mgr = MigrationManager()
        config = {"version": "0.9", "ai_model_preference": "gpt-4"}
        result, modified = mgr.apply_migrations(config)
        assert modified
        assert result["version"] == CURRENT_VERSION
        assert "ai_model_preference" not in result
        assert result["ai"]["model_preference"] == "gpt-4"

    def test_migration_applied_when_no_version(self):
        from navig.core.migrations import CURRENT_VERSION, MigrationManager

        mgr = MigrationManager()
        config = {"ai_model_preference": "claude"}
        result, modified = mgr.apply_migrations(config)
        assert modified
        assert result["version"] == CURRENT_VERSION

    def test_does_not_overwrite_existing_ai_field(self):
        from navig.core.migrations import MigrationManager

        mgr = MigrationManager()
        config = {
            "version": "0.9",
            "ai_model_preference": "old",
            "ai": {"model_preference": "keep-this"},
        }
        result, _ = mgr.apply_migrations(config)
        assert result["ai"]["model_preference"] == "keep-this"
        assert "ai_model_preference" not in result


class TestNoModuleLevelConsole:
    """Regression: migrations must NOT create a module-level Console (#38)."""

    def test_module_has_no_console_attribute(self):
        """The module should not expose a Console singleton."""
        import navig.core.migrations as mod

        # There must be no 'stderr_console' or 'console' at module scope
        assert not hasattr(mod, "stderr_console"), (
            "Module-level stderr_console found — this breaks Windows ANSI "
            "colour by creating a second Rich Console before the main one."
        )

    def test_module_does_not_import_rich_console_at_top_level(self):
        """Rich Console import should be deferred, not at module scope."""
        import ast
        import inspect

        import navig.core.migrations as mod

        source = inspect.getsource(mod)
        tree = ast.parse(source)

        # Check top-level imports (not inside functions/classes)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "rich" in node.module:
                    pytest.fail(
                        f"Top-level 'from {node.module} import ...' found at "
                        f"line {node.lineno}. Rich imports in migrations must "
                        f"be deferred to avoid Windows Console state conflicts."
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "rich" in alias.name:
                        pytest.fail(
                            f"Top-level 'import {alias.name}' found at "
                            f"line {node.lineno}. Rich imports in migrations "
                            f"must be deferred."
                        )

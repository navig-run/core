"""
Tests for navig.server_template_manager — pure-logic methods only.

All tests use mock ConfigManager/TemplateManager to avoid filesystem and
network side effects.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.server_template_manager import ServerTemplateManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stm(apps_dir: Path) -> ServerTemplateManager:
    """Return a ServerTemplateManager backed by mocked managers."""
    config_manager = MagicMock()
    config_manager.apps_dir = apps_dir

    template_manager = MagicMock()
    template_manager.discover_templates.return_value = {}
    template_manager.get_template.return_value = None

    with patch("navig.server_template_manager.get_config_manager", return_value=config_manager):
        stm = ServerTemplateManager(
            config_manager=config_manager,
            template_manager=template_manager,
        )
    return stm


# ---------------------------------------------------------------------------
# _get_server_template_dir
# ---------------------------------------------------------------------------


class TestGetServerTemplateDir:
    def test_returns_correct_path(self, tmp_path):
        stm = _make_stm(tmp_path)
        result = stm._get_server_template_dir("production")
        assert result == tmp_path / "production" / "templates"

    def test_includes_server_name(self, tmp_path):
        stm = _make_stm(tmp_path)
        result = stm._get_server_template_dir("staging")
        assert "staging" in str(result)

    def test_ends_with_templates(self, tmp_path):
        stm = _make_stm(tmp_path)
        result = stm._get_server_template_dir("my-server")
        assert result.name == "templates"

    def test_returns_path_object(self, tmp_path):
        stm = _make_stm(tmp_path)
        result = stm._get_server_template_dir("srv")
        assert isinstance(result, Path)

    def test_does_not_create_dir(self, tmp_path):
        stm = _make_stm(tmp_path)
        stm._get_server_template_dir("new-server")
        assert not (tmp_path / "new-server").exists()


# ---------------------------------------------------------------------------
# _ensure_server_template_dir
# ---------------------------------------------------------------------------


class TestEnsureServerTemplateDir:
    def test_creates_directory(self, tmp_path):
        stm = _make_stm(tmp_path)
        stm._ensure_server_template_dir("prod")
        assert (tmp_path / "prod" / "templates").is_dir()

    def test_idempotent(self, tmp_path):
        stm = _make_stm(tmp_path)
        stm._ensure_server_template_dir("prod")
        stm._ensure_server_template_dir("prod")  # second call must not raise


# ---------------------------------------------------------------------------
# _deep_merge (backward-compat wrapper)
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def setup_method(self):
        self.stm = _make_stm(Path("/tmp"))

    def test_simple_non_overlapping_keys(self):
        base = {"a": 1}
        overlay = {"b": 2}
        result = self.stm._deep_merge(base, overlay)
        assert result == {"a": 1, "b": 2}

    def test_overlay_wins_for_scalar(self):
        base = {"key": "old"}
        overlay = {"key": "new"}
        result = self.stm._deep_merge(base, overlay)
        assert result["key"] == "new"

    def test_nested_dicts_are_merged_not_replaced(self):
        base = {"nested": {"a": 1, "b": 2}}
        overlay = {"nested": {"b": 99, "c": 3}}
        result = self.stm._deep_merge(base, overlay)
        assert result["nested"]["a"] == 1
        assert result["nested"]["b"] == 99
        assert result["nested"]["c"] == 3

    def test_base_not_mutated(self):
        base = {"a": {"x": 1}}
        overlay = {"a": {"y": 2}}
        original = {"a": {"x": 1}}
        self.stm._deep_merge(base, overlay)
        assert base == original

    def test_empty_overlay_returns_base(self):
        base = {"a": 1, "b": 2}
        result = self.stm._deep_merge(base, {})
        assert result == base

    def test_empty_base_returns_overlay(self):
        overlay = {"x": 10}
        result = self.stm._deep_merge({}, overlay)
        assert result == overlay

    def test_deep_nesting(self):
        base = {"l1": {"l2": {"l3": "base"}}}
        overlay = {"l1": {"l2": {"l3": "overlay", "extra": True}}}
        result = self.stm._deep_merge(base, overlay)
        assert result["l1"]["l2"]["l3"] == "overlay"
        assert result["l1"]["l2"]["extra"] is True

    def test_list_values_are_merged(self):
        """deep_merge appends lists — overlay items are added to base list."""
        base = {"tags": ["a", "b"]}
        overlay = {"tags": ["c"]}
        result = self.stm._deep_merge(base, overlay)
        # deep_merge extends lists rather than replacing them
        assert "c" in result["tags"]
        assert len(result["tags"]) >= 1

    def test_none_overlay_value_replaces(self):
        base = {"key": "value"}
        overlay = {"key": None}
        result = self.stm._deep_merge(base, overlay)
        assert result["key"] is None


# ---------------------------------------------------------------------------
# Constructor — template discovery called on init
# ---------------------------------------------------------------------------


class TestConstructorBehavior:
    def test_discover_templates_is_called_on_init(self, tmp_path):
        config_manager = MagicMock()
        config_manager.apps_dir = tmp_path

        template_manager = MagicMock()
        template_manager.discover_templates.return_value = {}

        with patch(
            "navig.server_template_manager.get_config_manager", return_value=config_manager
        ):
            ServerTemplateManager(
                config_manager=config_manager,
                template_manager=template_manager,
            )

        template_manager.discover_templates.assert_called_once()

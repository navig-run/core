"""
Tests for navig/core/apps.py — AppManager class.
Batch 90.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from navig.core.apps import AppManager


# ---------------------------------------------------------------------------
# Minimal fake ConfigProvider
# ---------------------------------------------------------------------------

class FakeConfig:
    """Minimal implementation of AppConfigProvider required by AppManager."""

    def __init__(self, tmp_path: Path):
        self.base_dir = tmp_path / ".navig"
        self.app_config_dir = self.base_dir
        self.verbose = False
        self._hosts: dict[str, dict] = {}
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # --- protocol methods ---

    def get_config_directories(self) -> list[Path]:
        return [self.base_dir]

    def list_hosts(self) -> list[str]:
        return list(self._hosts.keys())

    def load_host_config(self, host_name: str) -> dict[str, Any]:
        if host_name not in self._hosts:
            raise FileNotFoundError(f"host not found: {host_name}")
        return dict(self._hosts[host_name])

    def save_host_config(self, host_name: str, config: dict[str, Any]) -> None:
        self._hosts[host_name] = dict(config)

    # --- helpers for test setup ---

    def add_host(self, host_name: str, config: dict | None = None) -> None:
        self._hosts[host_name] = config or {}

    def create_app_file(self, app_name: str, data: dict) -> Path:
        apps_dir = self.base_dir / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        p = apps_dir / f"{app_name}.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return p


def make_manager(tmp_path: Path) -> tuple[AppManager, FakeConfig]:
    cfg = FakeConfig(tmp_path)
    return AppManager(cfg), cfg


# ---------------------------------------------------------------------------
# get_file_path
# ---------------------------------------------------------------------------

class TestGetFilePath:
    def test_returns_path_object(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        p = mgr.get_file_path("myapp")
        assert isinstance(p, Path)

    def test_path_ends_with_yaml(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        p = mgr.get_file_path("myapp")
        assert p.name == "myapp.yaml"

    def test_path_under_apps_subdir(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        p = mgr.get_file_path("myapp")
        assert p.parent.name == "apps"

    def test_custom_navig_dir(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        custom = tmp_path / "custom"
        p = mgr.get_file_path("myapp", custom)
        assert p == custom / "apps" / "myapp.yaml"


# ---------------------------------------------------------------------------
# load_from_file
# ---------------------------------------------------------------------------

class TestLoadFromFile:
    def test_returns_none_when_missing(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        result = mgr.load_from_file("nonexistent")
        assert result is None

    def test_loads_valid_app(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"name": "myapp", "host": "prod", "webserver": {"type": "nginx"}})
        result = mgr.load_from_file("myapp")
        assert result is not None
        assert result["name"] == "myapp"
        assert result["host"] == "prod"

    def test_missing_name_raises(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"host": "prod"})
        with pytest.raises(ValueError, match="name"):
            mgr.load_from_file("myapp")

    def test_missing_host_raises(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"name": "myapp"})
        with pytest.raises(ValueError, match="host"):
            mgr.load_from_file("myapp")

    def test_name_mismatch_raises(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"name": "other", "host": "prod"})
        with pytest.raises(ValueError, match="mismatch"):
            mgr.load_from_file("myapp")

    def test_invalid_yaml_raises(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        apps_dir = cfg.base_dir / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        (apps_dir / "myapp.yaml").write_text("key: [\n  broken", encoding="utf-8")
        with pytest.raises(ValueError, match="YAML"):
            mgr.load_from_file("myapp")


# ---------------------------------------------------------------------------
# save_to_file
# ---------------------------------------------------------------------------

class TestSaveToFile:
    def test_creates_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        mgr.save_to_file("myapp", {"name": "myapp", "host": "prod"})
        assert mgr.get_file_path("myapp").exists()

    def test_auto_adds_name_if_missing(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        mgr.save_to_file("myapp", {"host": "prod"})
        # Should not raise
        result = mgr.load_from_file("myapp")
        assert result["name"] == "myapp"

    def test_missing_host_raises(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        with pytest.raises(ValueError, match="host"):
            mgr.save_to_file("myapp", {"name": "myapp"})

    def test_name_mismatch_raises(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        with pytest.raises(ValueError, match="mismatch"):
            mgr.save_to_file("myapp", {"name": "other", "host": "prod"})

    def test_adds_metadata_created(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        mgr.save_to_file("myapp", {"name": "myapp", "host": "prod"})
        result = mgr.load_from_file("myapp")
        assert "metadata" in result
        assert "created" in result["metadata"]

    def test_adds_metadata_updated(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        mgr.save_to_file("myapp", {"name": "myapp", "host": "prod"})
        result = mgr.load_from_file("myapp")
        assert "updated" in result["metadata"]

    def test_creates_parent_dirs(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        custom = tmp_path / "nested" / "deep"
        mgr.save_to_file("myapp", {"name": "myapp", "host": "prod"}, custom)
        assert (custom / "apps" / "myapp.yaml").exists()


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------

class TestExists:
    def test_false_when_nothing(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod")
        assert mgr.exists("prod", "myapp") is False

    def test_true_with_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"name": "myapp", "host": "prod"})
        assert mgr.exists("prod", "myapp") is True

    def test_false_when_host_mismatch(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"name": "myapp", "host": "staging"})
        assert mgr.exists("prod", "myapp") is False

    def test_true_legacy_embedded(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod", {"apps": {"myapp": {"webserver": {"type": "nginx"}}}})
        assert mgr.exists("prod", "myapp") is True

    def test_false_legacy_host_not_found(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        # Host not in registry → FileNotFoundError → False
        assert mgr.exists("unknown-host", "myapp") is False


# ---------------------------------------------------------------------------
# list_apps
# ---------------------------------------------------------------------------

class TestListApps:
    def test_empty_host_returns_empty(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod")
        result = mgr.list_apps("prod")
        assert result == []

    def test_lists_apps_from_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("app1", {"name": "app1", "host": "prod"})
        cfg.create_app_file("app2", {"name": "app2", "host": "prod"})
        result = mgr.list_apps("prod")
        assert "app1" in result
        assert "app2" in result

    def test_excludes_app_for_different_host(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("app1", {"name": "app1", "host": "staging"})
        result = mgr.list_apps("prod")
        assert "app1" not in result

    def test_lists_legacy_apps(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod", {"apps": {"legacy_app": {}}})
        result = mgr.list_apps("prod")
        assert "legacy_app" in result

    def test_returns_sorted_list(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("zapp", {"name": "zapp", "host": "prod"})
        cfg.create_app_file("aapp", {"name": "aapp", "host": "prod"})
        result = mgr.list_apps("prod")
        assert result == sorted(result)

    def test_merges_file_and_legacy(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("new_app", {"name": "new_app", "host": "prod"})
        cfg.add_host("prod", {"apps": {"old_app": {}}})
        result = mgr.list_apps("prod")
        assert "new_app" in result
        assert "old_app" in result


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_app_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"name": "myapp", "host": "prod"})
        result = mgr.delete("prod", "myapp")
        assert result is True
        assert not mgr.get_file_path("myapp").exists()

    def test_delete_nonexistent_returns_false(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod")
        result = mgr.delete("prod", "nonexistent")
        assert result is False

    def test_delete_legacy_app(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod", {"apps": {"old_app": {"webserver": {"type": "nginx"}}}})
        result = mgr.delete("prod", "old_app")
        assert result is True
        host_cfg = cfg.load_host_config("prod")
        assert "apps" not in host_cfg or "old_app" not in host_cfg.get("apps", {})

    def test_delete_wrong_host_returns_false(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_app_file("myapp", {"name": "myapp", "host": "staging"})
        result = mgr.delete("prod", "myapp")
        assert result is False
        # File should still exist
        assert mgr.get_file_path("myapp").exists()


# ---------------------------------------------------------------------------
# save (high-level)
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_new_format_creates_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        mgr.save("prod", "myapp", {"webserver": {"type": "nginx"}}, use_individual_file=True)
        assert mgr.get_file_path("myapp").exists()

    def test_save_new_format_injects_host_and_name(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        mgr.save("prod", "myapp", {"webserver": {"type": "nginx"}}, use_individual_file=True)
        result = mgr.load_from_file("myapp")
        assert result["host"] == "prod"
        assert result["name"] == "myapp"

    def test_save_legacy_format(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod", {})
        mgr.save("prod", "myapp", {"webserver": {"type": "nginx"}}, use_individual_file=False)
        host_cfg = cfg.load_host_config("prod")
        assert "apps" in host_cfg
        assert "myapp" in host_cfg["apps"]

    def test_save_legacy_preserves_existing_apps(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.add_host("prod", {"apps": {"existing": {"webserver": {"type": "nginx"}}}})
        mgr.save("prod", "other_app", {"webserver": {"type": "apache2"}}, use_individual_file=False)
        host_cfg = cfg.load_host_config("prod")
        assert "existing" in host_cfg["apps"]
        assert "other_app" in host_cfg["apps"]

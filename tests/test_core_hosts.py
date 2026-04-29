"""
Tests for navig/core/hosts.py — HostManager class.
Batch 91.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from navig.core.hosts import HostManager


# ---------------------------------------------------------------------------
# Fake ConfigProvider
# ---------------------------------------------------------------------------

class FakeConfig:
    """Minimal HostConfigProvider for HostManager tests."""

    def __init__(self, tmp_path: Path):
        self.base_dir = tmp_path / ".navig"
        self.app_config_dir = self.base_dir
        self.hosts_dir = self.base_dir / "hosts"
        self.verbose = False
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.hosts_dir.mkdir(parents=True, exist_ok=True)

    def get_config_directories(self) -> list[Path]:
        return [self.base_dir]

    def _is_directory_accessible(self, path: Path) -> bool:
        return path.exists()

    # helper: write a host YAML in hosts/
    def create_host_file(self, host_name: str, data: dict) -> Path:
        hosts_dir = self.base_dir / "hosts"
        hosts_dir.mkdir(parents=True, exist_ok=True)
        p = hosts_dir / f"{host_name}.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return p

    # helper: write a legacy host YAML in apps/
    def create_legacy_host_file(self, host_name: str, data: dict) -> Path:
        apps_dir = self.base_dir / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        p = apps_dir / f"{host_name}.yaml"
        p.write_text(yaml.dump(data), encoding="utf-8")
        return p


def make_manager(tmp_path: Path) -> tuple[HostManager, FakeConfig]:
    cfg = FakeConfig(tmp_path)
    return HostManager(cfg), cfg


# ---------------------------------------------------------------------------
# instantiation / init
# ---------------------------------------------------------------------------

class TestInit:
    def test_cache_empty_on_init(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        assert mgr._host_config_cache == {}

    def test_hosts_list_cache_none_on_init(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        assert mgr._hosts_list_cache is None


# ---------------------------------------------------------------------------
# invalidate_cache
# ---------------------------------------------------------------------------

class TestInvalidateCache:
    def test_invalidate_specific_host(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        mgr._host_config_cache["prod"] = {"host": "192.168.1.1"}
        mgr.invalidate_cache("prod")
        assert "prod" not in mgr._host_config_cache

    def test_invalidate_other_hosts_unaffected(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        mgr._host_config_cache["prod"] = {"host": "1.2.3.4"}
        mgr._host_config_cache["staging"] = {"host": "5.6.7.8"}
        mgr.invalidate_cache("prod")
        assert "staging" in mgr._host_config_cache

    def test_invalidate_all(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        mgr._host_config_cache["a"] = {}
        mgr._host_config_cache["b"] = {}
        mgr._hosts_list_cache = (["a", "b"], (0.0, 2))
        mgr.invalidate_cache()
        assert mgr._host_config_cache == {}
        assert mgr._hosts_list_cache is None

    def test_invalidate_nonexistent_host_no_crash(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        mgr.invalidate_cache("ghost")  # should not raise


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------

class TestExists:
    def test_false_when_no_files(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        assert mgr.exists("prod") is False

    def test_true_with_hosts_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"host": "192.168.1.1"})
        assert mgr.exists("prod") is True

    def test_true_with_legacy_apps_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_legacy_host_file("prod", {"host": "192.168.1.1"})
        assert mgr.exists("prod") is True

    def test_false_for_different_host(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("staging", {"host": "10.0.0.1"})
        assert mgr.exists("prod") is False


# ---------------------------------------------------------------------------
# list_hosts
# ---------------------------------------------------------------------------

class TestListHosts:
    def test_empty_when_no_hosts(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        result = mgr.list_hosts()
        assert result == []

    def test_lists_hosts_from_hosts_dir(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("alpha", {"host": "1.2.3.4"})
        cfg.create_host_file("beta", {"host": "5.6.7.8"})
        result = mgr.list_hosts()
        assert "alpha" in result
        assert "beta" in result

    def test_result_is_sorted(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("zzz", {"host": "1.1.1.1"})
        cfg.create_host_file("aaa", {"host": "2.2.2.2"})
        result = mgr.list_hosts()
        assert result == sorted(result)

    def test_cache_populated_after_call(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"host": "1.2.3.4"})
        mgr.list_hosts()
        assert mgr._hosts_list_cache is not None

    def test_cached_result_returned_on_second_call(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"host": "1.2.3.4"})
        r1 = mgr.list_hosts()
        r2 = mgr.list_hosts()
        assert r1 == r2

    def test_legacy_hosts_included(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        # Legacy: no 'host' field → treated as host
        cfg.create_legacy_host_file("legacy_host", {"ssh_host": "1.2.3.4"})
        result = mgr.list_hosts()
        assert "legacy_host" in result


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

class TestLoad:
    def test_loads_from_hosts_dir(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"ssh_host": "1.2.3.4", "user": "root"})
        result = mgr.load("prod")
        assert result["user"] == "root"

    def test_raises_file_not_found_when_missing(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        with pytest.raises(FileNotFoundError, match="prod"):
            mgr.load("prod")

    def test_cache_populated_after_load(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        mgr.load("prod")
        assert "prod" in mgr._host_config_cache

    def test_cache_returned_on_second_call(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        r1 = mgr.load("prod")
        r2 = mgr.load("prod")
        assert r1 is r2  # Same dict object from cache

    def test_use_cache_false_bypasses_cache(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        mgr.load("prod")  # Populate cache
        # Write updated file
        cfg.create_host_file("prod", {"user": "admin"})
        result = mgr.load("prod", use_cache=False)
        assert result["user"] == "admin"

    def test_loads_legacy_from_apps_dir(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_legacy_host_file("legacy", {"user": "ubuntu"})
        result = mgr.load("legacy")
        assert result["user"] == "ubuntu"

    def test_ssh_key_expanded(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"ssh_key": "~/.ssh/id_rsa"})
        result = mgr.load("prod")
        assert not result["ssh_key"].startswith("~")

    def test_no_ssh_key_field_no_crash(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        result = mgr.load("prod")
        assert "ssh_key" not in result


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

class TestSave:
    def test_creates_host_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        mgr.save("prod", {"user": "root", "ssh_host": "1.2.3.4"})
        host_file = cfg.base_dir / "hosts" / "prod.yaml"
        assert host_file.exists()

    def test_saves_metadata_last_updated(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        mgr.save("prod", {"user": "root"})
        host_file = cfg.base_dir / "hosts" / "prod.yaml"
        with open(host_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert "metadata" in data
        assert "last_updated" in data["metadata"]

    def test_save_invalidates_cache(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        mgr.load("prod")  # populate cache
        mgr.save("prod", {"user": "admin"})
        assert "prod" not in mgr._host_config_cache

    def test_creates_hosts_dir_if_missing(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        # Remove hosts dir
        import shutil
        shutil.rmtree(cfg.hosts_dir, ignore_errors=True)
        mgr.save("prod", {"user": "root"})
        assert (cfg.base_dir / "hosts" / "prod.yaml").exists()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_returns_true(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        assert mgr.delete("prod") is True

    def test_delete_removes_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        mgr.delete("prod")
        assert not (cfg.base_dir / "hosts" / "prod.yaml").exists()

    def test_delete_nonexistent_returns_false(self, tmp_path):
        mgr, _ = make_manager(tmp_path)
        assert mgr.delete("ghost") is False

    def test_delete_invalidates_cache(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_host_file("prod", {"user": "root"})
        mgr.load("prod")
        mgr.delete("prod")
        assert "prod" not in mgr._host_config_cache

    def test_delete_legacy_file(self, tmp_path):
        mgr, cfg = make_manager(tmp_path)
        cfg.create_legacy_host_file("old_host", {"user": "root"})
        result = mgr.delete("old_host")
        assert result is True
        assert not (cfg.base_dir / "apps" / "old_host.yaml").exists()

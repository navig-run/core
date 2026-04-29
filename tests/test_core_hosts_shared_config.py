"""
Batch 91 — tests for:
  navig/core/hosts.py         (HostManager: exists, list, load, save, delete)
  navig/core/shared_config.py (ConfigSingleton: get/set nested, plugin config, singleton)
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_provider(tmp_path: Path, *, app_config_dir=None, verbose=False):
    """Return a minimal ConfigProvider-compatible mock backed by tmp_path."""
    provider = MagicMock()
    provider.get_config_directories.return_value = [tmp_path]
    provider._is_directory_accessible.return_value = True
    provider.verbose = verbose
    provider.app_config_dir = app_config_dir
    provider.hosts_dir = tmp_path / "hosts"
    return provider


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  hosts.py — HostManager                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestHostManagerExists:
    def test_returns_false_when_no_files(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        assert hm.exists("myhost") is False

    def test_returns_true_for_new_format(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "myhost.yaml").write_text("host: myhost\n")

        hm = HostManager(_make_provider(tmp_path))
        assert hm.exists("myhost") is True

    def test_returns_true_for_legacy_format(self, tmp_path):
        from navig.core.hosts import HostManager

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "legacyhost.yaml").write_text("host: 1.2.3.4\n")

        hm = HostManager(_make_provider(tmp_path))
        assert hm.exists("legacyhost") is True

    def test_returns_false_for_unknown_host(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "other.yaml").write_text("host: other\n")

        hm = HostManager(_make_provider(tmp_path))
        assert hm.exists("unknown") is False


class TestHostManagerListHosts:
    def test_empty_dirs_returns_empty(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        assert hm.list_hosts() == []

    def test_lists_hosts_from_hosts_dir(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "alpha.yaml").write_text("host: alpha\n")
        (hosts_dir / "beta.yaml").write_text("host: beta\n")

        hm = HostManager(_make_provider(tmp_path))
        result = hm.list_hosts()
        assert sorted(result) == ["alpha", "beta"]

    def test_returns_sorted_list(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        for name in ["zeta", "alpha", "gamma"]:
            (hosts_dir / f"{name}.yaml").write_text(f"host: {name}\n")

        hm = HostManager(_make_provider(tmp_path))
        result = hm.list_hosts()
        assert result == sorted(result)

    def test_caches_result_on_second_call(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "one.yaml").write_text("host: one\n")

        hm = HostManager(_make_provider(tmp_path))
        r1 = hm.list_hosts()
        r2 = hm.list_hosts()
        assert r1 == r2

    def test_cache_invalidated_after_invalidate_call(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "first.yaml").write_text("host: first\n")

        hm = HostManager(_make_provider(tmp_path))
        hm.list_hosts()
        hm.invalidate_cache()
        assert hm._hosts_list_cache is None


class TestHostManagerLoad:
    def test_loads_new_format_host(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        config_data = {"host": "prod.example.com", "user": "deploy"}
        (hosts_dir / "prod.yaml").write_text(yaml.dump(config_data))

        hm = HostManager(_make_provider(tmp_path))
        with patch("navig.core.hosts.load_config", None, create=True):
            result = hm.load("prod")
        assert result["host"] == "prod.example.com"
        assert result["user"] == "deploy"

    def test_raises_for_missing_host(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        with pytest.raises(FileNotFoundError, match="missing"):
            hm.load("missing")

    def test_uses_cache_on_second_load(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "cached.yaml").write_text(yaml.dump({"host": "1.2.3.4"}))

        hm = HostManager(_make_provider(tmp_path))
        r1 = hm.load("cached")
        r2 = hm.load("cached")
        assert r1 is r2

    def test_bypass_cache_with_use_cache_false(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        host_file = hosts_dir / "server.yaml"
        host_file.write_text(yaml.dump({"host": "initial"}))

        hm = HostManager(_make_provider(tmp_path))
        r1 = hm.load("server")

        host_file.write_text(yaml.dump({"host": "updated"}))
        r2 = hm.load("server", use_cache=False)
        assert r2["host"] == "updated"

    def test_expands_ssh_key_path(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "key_host.yaml").write_text(yaml.dump({"host": "h", "ssh_key": "~/.ssh/id_rsa"}))

        hm = HostManager(_make_provider(tmp_path))
        result = hm.load("key_host")
        assert not result["ssh_key"].startswith("~")


class TestHostManagerSave:
    def test_save_creates_file(self, tmp_path):
        from navig.core.hosts import HostManager

        provider = _make_provider(tmp_path)
        hm = HostManager(provider)

        config = {"host": "new.example.com"}
        hm.save("newhost", config)

        saved_file = tmp_path / "hosts" / "newhost.yaml"
        assert saved_file.exists()
        loaded = yaml.safe_load(saved_file.read_text())
        assert loaded["host"] == "new.example.com"

    def test_save_adds_metadata_timestamp(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        config = {"host": "ts_host"}
        hm.save("ts_host", config)

        assert "metadata" in config
        assert "last_updated" in config["metadata"]

    def test_save_invalidates_cache(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        hm._host_config_cache["myhost"] = {"host": "old"}

        hm.save("myhost", {"host": "new"})
        assert "myhost" not in hm._host_config_cache


class TestHostManagerDelete:
    def test_delete_existing_host_returns_true(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "todelete.yaml").write_text("host: x\n")

        hm = HostManager(_make_provider(tmp_path))
        result = hm.delete("todelete")
        assert result is True
        assert not (hosts_dir / "todelete.yaml").exists()

    def test_delete_missing_host_returns_false(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        result = hm.delete("ghost")
        assert result is False

    def test_delete_invalidates_cache(self, tmp_path):
        from navig.core.hosts import HostManager

        hosts_dir = tmp_path / "hosts"
        hosts_dir.mkdir()
        (hosts_dir / "del_host.yaml").write_text("host: del\n")

        hm = HostManager(_make_provider(tmp_path))
        hm._host_config_cache["del_host"] = {"host": "del"}
        hm.delete("del_host")
        assert "del_host" not in hm._host_config_cache


class TestHostManagerInvalidateCache:
    def test_invalidate_specific_host(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        hm._host_config_cache["a"] = {"host": "a"}
        hm._host_config_cache["b"] = {"host": "b"}
        hm.invalidate_cache("a")
        assert "a" not in hm._host_config_cache
        assert "b" in hm._host_config_cache

    def test_invalidate_all(self, tmp_path):
        from navig.core.hosts import HostManager

        hm = HostManager(_make_provider(tmp_path))
        hm._host_config_cache["x"] = {"host": "x"}
        hm._host_config_cache["y"] = {"host": "y"}
        hm.invalidate_cache()
        assert hm._host_config_cache == {}
        assert hm._hosts_list_cache is None


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  shared_config.py — ConfigSingleton internals                           ║
# ╚══════════════════════════════════════════════════════════════════════════╝


@pytest.fixture()
def fresh_config(tmp_path):
    """Provide a fresh ConfigSingleton-like instance with mocked _load."""
    from navig.core.shared_config import ConfigSingleton

    # Reset singleton between tests
    old_instance = ConfigSingleton._instance
    ConfigSingleton._instance = None
    try:
        with (
            patch("navig.core.shared_config.config_dir", return_value=tmp_path),
            patch.object(ConfigSingleton, "_load"),
        ):
            cfg = ConfigSingleton()
            cfg._global_data = {}
            cfg._project_data = {}
            cfg.global_config_dir = tmp_path
            cfg.cache_dir = tmp_path / "cache"
            cfg.plugins_dir = tmp_path / "plugins"
            yield cfg
    finally:
        ConfigSingleton._instance = old_instance


class TestConfigSingletonNested:
    def test_get_nested_simple_key(self, fresh_config):
        fresh_config._global_data = {"key": "value"}
        result = fresh_config._get_nested(fresh_config._global_data, "key")
        assert result == "value"

    def test_get_nested_dot_key(self, fresh_config):
        fresh_config._global_data = {"section": {"sub": "deep"}}
        result = fresh_config._get_nested(fresh_config._global_data, "section.sub")
        assert result == "deep"

    def test_get_nested_missing_returns_default(self, fresh_config):
        result = fresh_config._get_nested({}, "missing.key", default="fallback")
        assert result == "fallback"

    def test_set_nested_simple(self, fresh_config):
        fresh_config._set_nested(fresh_config._global_data, "newkey", 42)
        assert fresh_config._global_data["newkey"] == 42

    def test_set_nested_creates_intermediate_dicts(self, fresh_config):
        fresh_config._set_nested(fresh_config._global_data, "a.b.c", "deep")
        assert fresh_config._global_data["a"]["b"]["c"] == "deep"

    def test_set_nested_overwrites_existing(self, fresh_config):
        fresh_config._global_data = {"x": {"y": "old"}}
        fresh_config._set_nested(fresh_config._global_data, "x.y", "new")
        assert fresh_config._global_data["x"]["y"] == "new"


class TestConfigSingletonGetSet:
    def test_get_global_scope(self, fresh_config):
        fresh_config._global_data = {"mode": "interactive"}
        assert fresh_config.get("mode", scope="global") == "interactive"

    def test_get_project_overrides_global(self, fresh_config):
        fresh_config._global_data = {"mode": "global_mode"}
        fresh_config._project_data = {"mode": "project_mode"}
        with patch.object(fresh_config, "_refresh_project_data"):
            result = fresh_config.get("mode", scope="merged")
        assert result == "project_mode"

    def test_get_fallback_to_global_when_project_empty(self, fresh_config):
        fresh_config._global_data = {"mode": "global_mode"}
        fresh_config._project_data = {}
        with patch.object(fresh_config, "_refresh_project_data"):
            result = fresh_config.get("mode", scope="merged")
        assert result == "global_mode"

    def test_set_global_scope(self, fresh_config):
        fresh_config.set("x", "hello", scope="global")
        assert fresh_config._global_data["x"] == "hello"

    def test_get_returns_default_when_missing(self, fresh_config):
        result = fresh_config.get("nonexistent", default="fallback_val")
        assert result == "fallback_val"


class TestConfigSingletonPlugins:
    def test_get_plugin_config_returns_section(self, fresh_config):
        fresh_config._global_data = {"plugins": {"brain": {"db_path": "/tmp/brain.db"}}}
        with patch.object(fresh_config, "_refresh_project_data"):
            result = fresh_config.get_plugin_config("brain", "db_path")
        assert result == "/tmp/brain.db"

    def test_set_plugin_config(self, fresh_config):
        fresh_config.set_plugin_config("myplugin", "enabled", True)
        assert fresh_config._global_data["plugins"]["myplugin"]["enabled"] is True

    def test_is_plugin_disabled_false_by_default(self, fresh_config):
        fresh_config._global_data = {"plugins": {"disabled_plugins": []}}
        with patch.object(fresh_config, "_refresh_project_data"):
            assert fresh_config.is_plugin_disabled("someplugin") is False

    def test_disable_plugin(self, fresh_config):
        fresh_config._global_data = {"plugins": {"disabled_plugins": []}}
        with patch.object(fresh_config, "_save_global"):
            fresh_config.disable_plugin("bad_plugin")
        assert "bad_plugin" in fresh_config._global_data["plugins"]["disabled_plugins"]

    def test_enable_plugin(self, fresh_config):
        fresh_config._global_data = {"plugins": {"disabled_plugins": ["bad_plugin"]}}
        with patch.object(fresh_config, "_save_global"):
            fresh_config.enable_plugin("bad_plugin")
        assert "bad_plugin" not in fresh_config._global_data["plugins"]["disabled_plugins"]

    def test_disable_plugin_idempotent(self, fresh_config):
        fresh_config._global_data = {"plugins": {"disabled_plugins": ["already_disabled"]}}
        with patch.object(fresh_config, "_save_global"):
            fresh_config.disable_plugin("already_disabled")
        count = fresh_config._global_data["plugins"]["disabled_plugins"].count("already_disabled")
        assert count == 1


class TestConfigSingletonSingleton:
    def test_same_instance_returned(self):
        from navig.core.shared_config import ConfigSingleton

        # The singleton instance should be the same object
        a = ConfigSingleton._instance
        b = ConfigSingleton._instance
        assert a is b

    def test_thread_safety(self, fresh_config):
        """Multiple threads calling get() should not raise."""
        errors = []

        def reader():
            try:
                with patch.object(fresh_config, "_refresh_project_data"):
                    fresh_config.get("mode", default="safe")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

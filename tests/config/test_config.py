"""
Tests for ConfigManager with new two-tier hierarchy.
"""

from pathlib import Path

import pytest
import yaml

from navig.config import (
    ConfigManager,
    get_config_manager,
    reset_config_manager,
    set_config_cache_bypass,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_home(monkeypatch, tmp_path):
    """Create temporary home directory for tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    yield tmp_path


@pytest.fixture
def config_manager(temp_home):
    """Create ConfigManager with temporary home, isolated from project's .navig."""
    # Pass explicit config_dir to skip auto-detection from cwd
    # which would find the project's actual .navig directory
    config_dir = temp_home / ".navig"
    return ConfigManager(config_dir=config_dir)


@pytest.fixture
def sample_host_config():
    """Sample host configuration (new format)."""
    return {
        "name": "myhost",
        "host": "srv.example.host",
        "port": 22,
        "user": "root",
        "ssh_key": "~/.ssh/myhost",
        "default_app": "myapp",
        "apps": {
            "myapp": {
                "database": {"type": "mysql", "name": "myapp_db"},
                "webserver": {"type": "apache2"},
            },
            "myapp-staging": {
                "database": {"type": "mysql", "name": "myapp_staging"},
                "webserver": {"type": "apache2"},
            },
        },
    }


@pytest.fixture
def sample_legacy_config():
    """Sample legacy configuration (old format)."""
    return {
        "name": "production",
        "host": "srv.example.com",
        "port": 22,
        "user": "root",
        "ssh_key": "~/.ssh/production",
        "database": {"type": "mysql", "name": "myapp_db"},
        "services": {"web": "nginx"},
    }


class TestHostManagement:
    """Test host management methods."""

    def test_host_exists_new_format(self, config_manager, sample_host_config):
        """Test host_exists with new format."""
        config_manager.save_host_config("myhost", sample_host_config)
        assert config_manager.host_exists("myhost")
        assert not config_manager.host_exists("nonexistent")

    def test_host_exists_legacy_format(self, config_manager, sample_legacy_config):
        """Test host_exists with legacy format."""
        # Save to legacy directory
        legacy_file = config_manager.apps_dir / "production.yaml"
        with open(legacy_file, "w") as f:
            yaml.dump(sample_legacy_config, f)

        assert config_manager.host_exists("production")

    def test_list_hosts_new_format(self, config_manager, sample_host_config):
        """Test list_hosts with new format."""
        config_manager.save_host_config("myhost", sample_host_config)
        config_manager.save_host_config("vps", sample_host_config)

        hosts = config_manager.list_hosts()
        assert "myhost" in hosts
        assert "vps" in hosts
        assert len(hosts) == 2

    def test_list_hosts_mixed_formats(
        self, config_manager, sample_host_config, sample_legacy_config
    ):
        """Test list_hosts with both new and legacy formats."""
        # New format
        config_manager.save_host_config("myhost", sample_host_config)

        # Legacy format
        legacy_file = config_manager.apps_dir / "production.yaml"
        with open(legacy_file, "w") as f:
            yaml.dump(sample_legacy_config, f)

        hosts = config_manager.list_hosts()
        assert "myhost" in hosts
        assert "production" in hosts
        assert len(hosts) == 2

    def test_load_host_config_new_format(self, config_manager, sample_host_config):
        """Test load_host_config with new format."""
        config_manager.save_host_config("myhost", sample_host_config)

        loaded = config_manager.load_host_config("myhost")
        assert loaded["name"] == "myhost"
        assert loaded["host"] == "srv.example.host"
        assert "apps" in loaded
        assert "myapp" in loaded["apps"]

    def test_load_host_config_legacy_format(self, config_manager, sample_legacy_config):
        """Test load_host_config with legacy format."""
        legacy_file = config_manager.apps_dir / "production.yaml"
        with open(legacy_file, "w") as f:
            yaml.dump(sample_legacy_config, f)

        loaded = config_manager.load_host_config("production")
        assert loaded["name"] == "production"
        assert loaded["host"] == "srv.example.com"

    def test_load_host_config_not_found(self, config_manager):
        """Test load_host_config with nonexistent host."""
        with pytest.raises(FileNotFoundError) as exc_info:
            config_manager.load_host_config("nonexistent")

        assert "Host configuration not found" in str(exc_info.value)

    def test_delete_host_config(self, config_manager, sample_host_config):
        """Test delete_host_config."""
        config_manager.save_host_config("myhost", sample_host_config)
        assert config_manager.host_exists("myhost")

        config_manager.delete_host_config("myhost")
        assert not config_manager.host_exists("myhost")


class TestAppManagement:
    """Test app management methods."""

    def test_app_exists(self, config_manager, sample_host_config):
        """Test app_exists."""
        config_manager.save_host_config("myhost", sample_host_config)

        assert config_manager.app_exists("myhost", "myapp")
        assert config_manager.app_exists("myhost", "myapp-staging")
        assert not config_manager.app_exists("myhost", "nonexistent")

    def test_list_apps(self, config_manager, sample_host_config):
        """Test list_apps."""
        config_manager.save_host_config("myhost", sample_host_config)

        apps = config_manager.list_apps("myhost")
        assert "myapp" in apps
        assert "myapp-staging" in apps
        assert len(apps) == 2

    def test_list_apps_empty(self, config_manager):
        """Test list_apps with no apps."""
        config = {
            "name": "empty",
            "host": "empty.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/empty",
        }
        config_manager.save_host_config("empty", config)

        apps = config_manager.list_apps("empty")
        assert len(apps) == 0

    def test_load_app_config(self, config_manager, sample_host_config):
        """Test load_app_config."""
        config_manager.save_host_config("myhost", sample_host_config)

        app = config_manager.load_app_config("myhost", "myapp")
        assert app["database"]["type"] == "mysql"
        assert app["database"]["name"] == "myapp_db"
        assert app["webserver"]["type"] == "apache2"

    def test_load_app_config_not_found(self, config_manager, sample_host_config):
        """Test load_app_config with nonexistent app."""
        config_manager.save_host_config("myhost", sample_host_config)

        with pytest.raises(FileNotFoundError) as exc_info:
            config_manager.load_app_config("myhost", "nonexistent")

        assert "App 'nonexistent' not found" in str(exc_info.value)

    def test_load_app_config_missing_webserver_type(self, config_manager):
        """Test load_app_config fails when webserver.type is missing."""
        config = {
            "name": "test",
            "host": "test.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/test",
            "apps": {
                "myapp": {
                    "database": {"type": "mysql"}
                    # Missing webserver.type
                }
            },
        }
        config_manager.save_host_config("test", config)

        with pytest.raises(ValueError) as exc_info:
            config_manager.load_app_config("test", "myapp")

        assert "Missing 'webserver.type'" in str(exc_info.value)
        assert "webserver.type: nginx" in str(exc_info.value)

    def test_save_app_config(self, config_manager, sample_host_config):
        """Test save_app_config."""
        config_manager.save_host_config("myhost", sample_host_config)

        new_app = {
            "database": {"type": "postgresql", "name": "ai_db"},
            "webserver": {"type": "nginx"},
        }

        config_manager.save_app_config("myhost", "ai", new_app)

        # Verify saved
        loaded = config_manager.load_app_config("myhost", "ai")
        assert loaded["database"]["type"] == "postgresql"
        assert loaded["webserver"]["type"] == "nginx"

    def test_delete_app_config(self, config_manager, sample_host_config):
        """Test delete_app_config."""
        config_manager.save_host_config("myhost", sample_host_config)

        assert config_manager.app_exists("myhost", "myapp")

        config_manager.delete_app_config("myhost", "myapp")

        assert not config_manager.app_exists("myhost", "myapp")
        assert config_manager.app_exists("myhost", "myapp-staging")


class TestActiveContext:
    """Test active host/app management."""

    def test_set_get_active_host(self, config_manager, sample_host_config):
        """Test set_active_host and get_active_host."""
        config_manager.save_host_config("myhost", sample_host_config)

        config_manager.set_active_host("myhost")
        assert config_manager.get_active_host() == "myhost"

    def test_get_active_host_from_global_active_host_key(self, config_manager, sample_host_config):
        """Test compatibility fallback to global config active_host key."""
        config_manager.save_host_config("myhost", sample_host_config)

        config_manager.update_global_config({"active_host": "myhost"})
        assert config_manager.get_active_host() == "myhost"

    def test_set_active_host_not_found(self, config_manager):
        """Test set_active_host with nonexistent host."""
        with pytest.raises(ValueError) as exc_info:
            config_manager.set_active_host("nonexistent")

        assert "Host 'nonexistent' not found" in str(exc_info.value)

    def test_set_get_active_app(self, config_manager):
        """Test set_active_app and get_active_app."""
        config_manager.set_active_app("myapp")
        assert config_manager.get_active_app() == "myapp"

    def test_set_active_app_default_mode_falls_back_to_global(self, config_manager, monkeypatch):
        """Default set_active_app should still set global cache when local context is invalid."""
        monkeypatch.setattr(
            config_manager._context,
            "set_active_app_local",
            lambda _app: (_ for _ in ()).throw(ValueError("no active host")),
        )

        config_manager._context.set_active_app("myapp", local=None)
        assert config_manager.get_active_app() == "myapp"

    def test_set_active_context(self, config_manager, sample_host_config):
        """Test set_active_context."""
        config_manager.save_host_config("myhost", sample_host_config)

        config_manager.set_active_context("myhost", "myapp")

        assert config_manager.get_active_host() == "myhost"
        assert config_manager.get_active_app() == "myapp"

    def test_set_active_context_invalid_host(self, config_manager):
        """Test set_active_context with invalid host."""
        with pytest.raises(ValueError) as exc_info:
            config_manager.set_active_context("nonexistent", "myapp")

        assert "Host 'nonexistent' not found" in str(exc_info.value)

    def test_set_active_context_invalid_app(self, config_manager, sample_host_config):
        """Test set_active_context with invalid app."""
        config_manager.save_host_config("myhost", sample_host_config)

        with pytest.raises(ValueError) as exc_info:
            config_manager.set_active_context("myhost", "nonexistent")

        assert "App 'nonexistent' not found" in str(exc_info.value)


class TestBackwardCompatibility:
    """Test backward compatibility with legacy format."""

    def test_load_app_config_legacy_format(self, config_manager, sample_legacy_config):
        """Test load_app_config with legacy format."""
        # Save to legacy directory
        legacy_file = config_manager.apps_dir / "production.yaml"
        with open(legacy_file, "w") as f:
            yaml.dump(sample_legacy_config, f)

        # Load as app (legacy format: host config IS app config)
        app = config_manager.load_app_config("production", "production")
        assert app["database"]["type"] == "mysql"
        assert app["services"]["web"] == "nginx"


class TestConfigManagerSingleton:
    """Test singleton cache/bypass/reset behavior."""

    def test_cache_bypass_and_reset_semantics(self, temp_home):
        config_dir = temp_home / ".navig"

        reset_config_manager()
        set_config_cache_bypass(False)

        cm1 = get_config_manager(config_dir=config_dir)
        cm2 = get_config_manager(config_dir=config_dir)
        assert cm2 is cm1

        set_config_cache_bypass(True)
        cm3 = get_config_manager(config_dir=config_dir)
        assert cm3 is not cm1

        set_config_cache_bypass(False)
        cm4 = get_config_manager(config_dir=config_dir)
        assert cm4 is cm3

        reset_config_manager()
        cm5 = get_config_manager(config_dir=config_dir)
        assert cm5 is not cm4

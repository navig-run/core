"""
Tests for configuration migration utilities.
"""

import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from navig.migration import (
    ConfigMigrationError,
    backup_config,
    detect_format,
    extract_webserver_type,
    migrate_all_configs,
    migrate_config,
    save_config,
)


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    temp = tempfile.mkdtemp()
    yield Path(temp)
    shutil.rmtree(temp)


@pytest.fixture
def old_format_nginx_config():
    """Old format configuration with nginx."""
    return {
        "name": "production",
        "host": "srv.example.com",
        "port": 22,
        "user": "root",
        "ssh_key": "~/.ssh/production",
        "database": {
            "type": "mysql",
            "name": "myapp_db",
            "user": "myapp_user",
            "password": "secret123",
        },
        "services": {"web": "nginx", "php": "php8.2-fpm"},
        "paths": {"web_root": "/var/www/myapp/public"},
    }


@pytest.fixture
def old_format_apache_config():
    """Old format configuration with apache2."""
    return {
        "name": "staging",
        "host": "staging.example.com",
        "port": 2222,
        "user": "deploy",
        "ssh_key": "~/.ssh/staging",
        "services": {"web": "apache2"},
    }


@pytest.fixture
def new_format_config():
    """New format configuration."""
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
                "webserver": {"type": "nginx"},
            }
        },
    }


class TestFormatDetection:
    """Test format detection."""

    def test_detect_old_format(self, temp_dir, old_format_nginx_config):
        """Test detection of old format."""
        config_path = temp_dir / "old.yaml"
        save_config(old_format_nginx_config, config_path)

        assert detect_format(config_path) == "old"

    def test_detect_new_format(self, temp_dir, new_format_config):
        """Test detection of new format."""
        config_path = temp_dir / "new.yaml"
        save_config(new_format_config, config_path)

        assert detect_format(config_path) == "new"

    def test_detect_format_missing_file(self, temp_dir):
        """Test detection with missing file."""
        config_path = temp_dir / "missing.yaml"

        with pytest.raises(FileNotFoundError):
            detect_format(config_path)

    def test_detect_format_empty_file(self, temp_dir):
        """Test detection with empty file."""
        config_path = temp_dir / "empty.yaml"
        config_path.write_text("")

        with pytest.raises(ConfigMigrationError):
            detect_format(config_path)


class TestWebserverTypeExtraction:
    """Test webserver type extraction."""

    def test_extract_nginx(self, old_format_nginx_config):
        """Test extraction of nginx type."""
        assert extract_webserver_type(old_format_nginx_config) == "nginx"

    def test_extract_apache(self, old_format_apache_config):
        """Test extraction of apache2 type."""
        assert extract_webserver_type(old_format_apache_config) == "apache2"

    def test_extract_missing_services(self):
        """Test extraction with missing services field."""
        config = {"name": "test", "host": "test.com"}

        with pytest.raises(ConfigMigrationError) as exc_info:
            extract_webserver_type(config)

        assert "Unable to determine webserver type" in str(exc_info.value)

    def test_extract_missing_web_service(self):
        """Test extraction with missing web service."""
        config = {"services": {"php": "php8.2-fpm"}}

        with pytest.raises(ConfigMigrationError) as exc_info:
            extract_webserver_type(config)

        assert "Unable to determine webserver type" in str(exc_info.value)

    def test_extract_invalid_web_service(self):
        """Test extraction with invalid web service."""
        config = {"services": {"web": "lighttpd"}}

        with pytest.raises(ConfigMigrationError) as exc_info:
            extract_webserver_type(config)

        assert "Unable to determine webserver type" in str(exc_info.value)


class TestConfigMigration:
    """Test configuration migration."""

    def test_migrate_nginx_config(self, temp_dir, old_format_nginx_config):
        """Test migration of nginx configuration."""
        old_path = temp_dir / "production.yaml"
        new_path = temp_dir / "production_new.yaml"
        save_config(old_format_nginx_config, old_path)

        old_config, new_config = migrate_config(old_path, new_path)

        # Verify host-level fields
        assert new_config["name"] == "production"
        assert new_config["host"] == "srv.example.com"
        assert new_config["port"] == 22
        assert new_config["user"] == "root"
        assert new_config["ssh_key"] == "~/.ssh/production"
        assert new_config["default_app"] == "production"

        # Verify apps structure
        assert "apps" in new_config
        assert "production" in new_config["apps"]

        # Verify app fields
        app = new_config["apps"]["production"]
        assert app["database"]["type"] == "mysql"
        assert app["database"]["name"] == "myapp_db"
        assert app["services"]["web"] == "nginx"
        assert app["paths"]["web_root"] == "/var/www/myapp/public"

        # Verify webserver type extracted
        assert "webserver" in app
        assert app["webserver"]["type"] == "nginx"

    def test_migrate_apache_config(self, temp_dir, old_format_apache_config):
        """Test migration of apache2 configuration."""
        old_path = temp_dir / "staging.yaml"
        new_path = temp_dir / "staging_new.yaml"
        save_config(old_format_apache_config, old_path)

        old_config, new_config = migrate_config(old_path, new_path)

        # Verify webserver type extracted
        app = new_config["apps"]["staging"]
        assert app["webserver"]["type"] == "apache2"

    def test_migrate_missing_webserver_type(self, temp_dir):
        """Test migration fails when webserver type cannot be determined."""
        config = {
            "name": "test",
            "host": "test.com",
            "user": "root",
            "ssh_key": "~/.ssh/test",
        }
        old_path = temp_dir / "test.yaml"
        new_path = temp_dir / "test_new.yaml"
        save_config(config, old_path)

        with pytest.raises(ConfigMigrationError) as exc_info:
            migrate_config(old_path, new_path)

        assert "Failed to migrate" in str(exc_info.value)
        assert "Unable to determine webserver type" in str(exc_info.value)


class TestBackup:
    """Test backup functionality."""

    def test_backup_config(self, temp_dir, old_format_nginx_config):
        """Test backup creation."""
        config_path = temp_dir / "production.yaml"
        save_config(old_format_nginx_config, config_path)

        backup_path = backup_config(config_path)

        assert backup_path.exists()
        assert backup_path.parent == config_path.parent
        assert "production.backup." in backup_path.name
        assert backup_path.suffix == ".yaml"

        # Verify backup content matches original
        with open(config_path, "r") as f:
            original = yaml.safe_load(f)
        with open(backup_path, "r") as f:
            backup = yaml.safe_load(f)

        assert original == backup

    def test_backup_missing_file(self, temp_dir):
        """Test backup with missing file."""
        config_path = temp_dir / "missing.yaml"

        with pytest.raises(FileNotFoundError):
            backup_config(config_path)


class TestMigrateAll:
    """Test batch migration."""

    def test_migrate_all_configs(self, temp_dir, old_format_nginx_config, old_format_apache_config):
        """Test migrating all configurations."""
        old_dir = temp_dir / "apps"
        new_dir = temp_dir / "hosts"
        old_dir.mkdir()

        # Create old format configs
        save_config(old_format_nginx_config, old_dir / "production.yaml")
        save_config(old_format_apache_config, old_dir / "staging.yaml")

        # Migrate
        results = migrate_all_configs(old_dir, new_dir, dry_run=False, backup=True)

        # Verify results
        assert len(results["migrated"]) == 2
        assert len(results["failed"]) == 0
        assert len(results["backups"]) == 2

        # Verify new configs exist
        assert (new_dir / "production.yaml").exists()
        assert (new_dir / "staging.yaml").exists()

        # Verify backups exist
        assert len(list(old_dir.glob("*.backup.*.yaml"))) == 2

    def test_migrate_all_dry_run(self, temp_dir, old_format_nginx_config):
        """Test dry run migration."""
        old_dir = temp_dir / "apps"
        new_dir = temp_dir / "hosts"
        old_dir.mkdir()

        save_config(old_format_nginx_config, old_dir / "production.yaml")

        # Dry run
        results = migrate_all_configs(old_dir, new_dir, dry_run=True, backup=True)

        # Verify no files created
        assert not new_dir.exists()
        assert len(list(old_dir.glob("*.backup.*.yaml"))) == 0

        # Verify results show what would be migrated
        assert len(results["migrated"]) == 1
        assert results["migrated"][0]["dry_run"] is True

    def test_migrate_all_skip_new_format(self, temp_dir, new_format_config):
        """Test skipping already migrated configs."""
        old_dir = temp_dir / "apps"
        new_dir = temp_dir / "hosts"
        old_dir.mkdir()

        save_config(new_format_config, old_dir / "myhost.yaml")

        results = migrate_all_configs(old_dir, new_dir, dry_run=False, backup=True)

        # Verify skipped
        assert len(results["skipped"]) == 1
        assert results["skipped"][0]["reason"] == "Already in new format"

    def test_migrate_all_empty_directory(self, temp_dir):
        """Test migration with empty directory."""
        old_dir = temp_dir / "apps"
        new_dir = temp_dir / "hosts"

        results = migrate_all_configs(old_dir, new_dir, dry_run=False, backup=True)

        # Verify no results
        assert len(results["migrated"]) == 0
        assert len(results["skipped"]) == 0
        assert len(results["failed"]) == 0

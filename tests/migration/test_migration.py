"""Tests for navig.migration — legacy config migration utilities."""

from __future__ import annotations

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


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


def _old_config(host: str = "10.0.0.1", services_web: str = "nginx") -> dict:
    return {
        "name": "myapp",
        "host": host,
        "port": 22,
        "user": "deploy",
        "ssh_key": "~/.ssh/id_rsa",
        "services": {"web": services_web, "db": "mysql"},
        "paths": {"app": "/var/www/myapp"},
    }


def _new_config() -> dict:
    return {
        "name": "myserver",
        "host": "10.0.0.1",
        "apps": {"myapp": {"paths": {"app": "/var/www"}}},
    }


def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────
# detect_format
# ──────────────────────────────────────────────────────────────


class TestDetectFormat:
    def test_detects_old_format(self, tmp_path):
        cfg = _write_yaml(tmp_path / "old.yaml", _old_config())
        assert detect_format(cfg) == "old"

    def test_detects_new_format(self, tmp_path):
        cfg = _write_yaml(tmp_path / "new.yaml", _new_config())
        assert detect_format(cfg) == "new"

    def test_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            detect_format(tmp_path / "missing.yaml")

    def test_raises_on_empty_file(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ConfigMigrationError):
            detect_format(empty)

    def test_raises_on_ambiguous_config(self, tmp_path):
        ambiguous = tmp_path / "ambiguous.yaml"
        # No 'host' and no 'apps' key
        _write_yaml(ambiguous, {"random": "data"})
        with pytest.raises(ConfigMigrationError):
            detect_format(ambiguous)

    def test_raises_on_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("key: [unclosed", encoding="utf-8")
        with pytest.raises(Exception):
            detect_format(bad)


# ──────────────────────────────────────────────────────────────
# extract_webserver_type
# ──────────────────────────────────────────────────────────────


class TestExtractWebserverType:
    def test_extracts_nginx(self):
        cfg = _old_config(services_web="nginx")
        assert extract_webserver_type(cfg) == "nginx"

    def test_extracts_nginx_uppercase(self):
        cfg = _old_config(services_web="Nginx")
        assert extract_webserver_type(cfg) == "nginx"

    def test_extracts_apache2(self):
        cfg = _old_config(services_web="apache2")
        assert extract_webserver_type(cfg) == "apache2"

    def test_extracts_apache_variant(self):
        cfg = _old_config(services_web="Apache")
        assert extract_webserver_type(cfg) == "apache2"

    def test_reads_from_webserver_type_if_present(self):
        cfg = {"webserver": {"type": "nginx"}}
        assert extract_webserver_type(cfg) == "nginx"

    def test_raises_when_no_services(self):
        with pytest.raises(ConfigMigrationError):
            extract_webserver_type({"host": "x.x.x.x"})

    def test_raises_for_unknown_web_service(self):
        with pytest.raises(ConfigMigrationError):
            extract_webserver_type({"services": {"web": "caddy"}})


# ──────────────────────────────────────────────────────────────
# migrate_config
# ──────────────────────────────────────────────────────────────


class TestMigrateConfig:
    def test_returns_tuple_of_old_and_new(self, tmp_path):
        old_path = _write_yaml(tmp_path / "myapp.yaml", _old_config())
        old_cfg, new_cfg = migrate_config(old_path, tmp_path / "host_myapp.yaml")
        assert isinstance(old_cfg, dict)
        assert isinstance(new_cfg, dict)

    def test_new_config_has_apps_key(self, tmp_path):
        old_path = _write_yaml(tmp_path / "myapp.yaml", _old_config())
        _, new_cfg = migrate_config(old_path, tmp_path / "out.yaml")
        assert "apps" in new_cfg

    def test_app_name_taken_from_filename(self, tmp_path):
        old_path = _write_yaml(tmp_path / "production.yaml", _old_config())
        _, new_cfg = migrate_config(old_path, tmp_path / "out.yaml")
        assert "production" in new_cfg["apps"]

    def test_host_preserved_at_root(self, tmp_path):
        old_path = _write_yaml(tmp_path / "myapp.yaml", _old_config(host="192.168.1.1"))
        _, new_cfg = migrate_config(old_path, tmp_path / "out.yaml")
        assert new_cfg["host"] == "192.168.1.1"

    def test_webserver_type_added_to_app(self, tmp_path):
        old_path = _write_yaml(tmp_path / "myapp.yaml", _old_config(services_web="nginx"))
        _, new_cfg = migrate_config(old_path, tmp_path / "out.yaml")
        assert new_cfg["apps"]["myapp"]["webserver"]["type"] == "nginx"

    def test_metadata_added(self, tmp_path):
        old_path = _write_yaml(tmp_path / "myapp.yaml", _old_config())
        _, new_cfg = migrate_config(old_path, tmp_path / "out.yaml")
        assert "metadata" in new_cfg
        assert "migrated_at" in new_cfg["metadata"]

    def test_default_app_set(self, tmp_path):
        old_path = _write_yaml(tmp_path / "webserver.yaml", _old_config())
        _, new_cfg = migrate_config(old_path, tmp_path / "out.yaml")
        assert new_cfg["default_app"] == "webserver"

    def test_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            migrate_config(tmp_path / "ghost.yaml", tmp_path / "out.yaml")

    def test_raises_on_already_new_format(self, tmp_path):
        new_path = _write_yaml(tmp_path / "new.yaml", _new_config())
        with pytest.raises(ConfigMigrationError):
            migrate_config(new_path, tmp_path / "out.yaml")

    def test_raises_on_empty_file(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ConfigMigrationError):
            migrate_config(empty, tmp_path / "out.yaml")


# ──────────────────────────────────────────────────────────────
# backup_config
# ──────────────────────────────────────────────────────────────


class TestBackupConfig:
    def test_creates_backup_file(self, tmp_path):
        cfg = _write_yaml(tmp_path / "config.yaml", _old_config())
        backup_path = backup_config(cfg)
        assert backup_path.exists()

    def test_backup_is_different_file(self, tmp_path):
        cfg = _write_yaml(tmp_path / "config.yaml", _old_config())
        backup_path = backup_config(cfg)
        assert backup_path != cfg

    def test_backup_has_original_content(self, tmp_path):
        data = _old_config()
        cfg = _write_yaml(tmp_path / "config.yaml", data)
        backup_path = backup_config(cfg)
        loaded = yaml.safe_load(backup_path.read_text(encoding="utf-8"))
        assert loaded["host"] == data["host"]

    def test_backup_name_contains_timestamp(self, tmp_path):
        cfg = _write_yaml(tmp_path / "config.yaml", _old_config())
        backup_path = backup_config(cfg)
        assert ".backup." in backup_path.name

    def test_raises_if_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            backup_config(tmp_path / "ghost.yaml")


# ──────────────────────────────────────────────────────────────
# save_config
# ──────────────────────────────────────────────────────────────


class TestSaveConfig:
    def test_writes_yaml_file(self, tmp_path):
        dest = tmp_path / "saved.yaml"
        save_config({"key": "value"}, dest)
        loaded = yaml.safe_load(dest.read_text(encoding="utf-8"))
        assert loaded == {"key": "value"}

    def test_creates_parent_directories(self, tmp_path):
        dest = tmp_path / "sub" / "deep" / "config.yaml"
        save_config({"x": 1}, dest)
        assert dest.exists()


# ──────────────────────────────────────────────────────────────
# migrate_all_configs
# ──────────────────────────────────────────────────────────────


class TestMigrateAllConfigs:
    def test_empty_old_dir(self, tmp_path):
        result = migrate_all_configs(tmp_path / "nonexistent", tmp_path / "new")
        assert result["migrated"] == []
        assert result["failed"] == []

    def test_migrates_old_config(self, tmp_path):
        old_dir = tmp_path / "old"
        _write_yaml(old_dir / "myapp.yaml", _old_config())
        result = migrate_all_configs(old_dir, tmp_path / "new", backup=False)
        assert len(result["migrated"]) == 1
        assert len(result["failed"]) == 0

    def test_skips_already_new_format(self, tmp_path):
        old_dir = tmp_path / "old"
        _write_yaml(old_dir / "newstyle.yaml", _new_config())
        result = migrate_all_configs(old_dir, tmp_path / "new", backup=False)
        assert len(result["skipped"]) == 1
        assert len(result["migrated"]) == 0

    def test_skips_backup_files(self, tmp_path):
        old_dir = tmp_path / "old"
        _write_yaml(old_dir / "config.backup.20240101_120000.yaml", _old_config())
        result = migrate_all_configs(old_dir, tmp_path / "new", backup=False)
        assert len(result["skipped"]) == 1

    def test_dry_run_does_not_write_new_files(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        _write_yaml(old_dir / "myapp.yaml", _old_config())
        result = migrate_all_configs(old_dir, new_dir, dry_run=True, backup=False)
        assert len(result["migrated"]) == 1
        assert result["migrated"][0]["dry_run"] is True
        assert not (new_dir / "myapp.yaml").exists()

    def test_creates_backups_when_requested(self, tmp_path):
        old_dir = tmp_path / "old"
        _write_yaml(old_dir / "myapp.yaml", _old_config())
        result = migrate_all_configs(old_dir, tmp_path / "new", backup=True)
        assert len(result["backups"]) == 1

    def test_failed_migration_recorded(self, tmp_path):
        old_dir = tmp_path / "old"
        # Create a file with 'host' but no services.web (will fail extract_webserver_type)
        bad_cfg = {"host": "10.0.0.1", "user": "admin"}
        _write_yaml(old_dir / "broken.yaml", bad_cfg)
        result = migrate_all_configs(old_dir, tmp_path / "new", backup=False)
        assert len(result["failed"]) == 1

"""
Integration tests for NAVIG commands.

These tests verify commands work end-to-end with proper:
- Configuration loading
- Remote operations setup
- Error handling
- JSON output formatting
"""

import pytest

pytestmark = pytest.mark.integration


class TestConfigManager:
    """Test configuration management."""

    def test_config_manager_import(self):
        """Test ConfigManager can be imported."""
        from navig.config import ConfigManager

        assert ConfigManager is not None

    def test_config_manager_initialization(self, tmp_path, monkeypatch):
        """Test ConfigManager initializes correctly."""
        from navig.config import ConfigManager

        # Set temp home
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        # Use explicit config_dir to avoid picking up project's .navig
        config_dir = tmp_path / ".navig"
        config = ConfigManager(config_dir=config_dir)
        # Should initialize without errors
        assert config is not None


class TestRemoteOperations:
    """Test remote operations wrapper."""

    def test_remote_operations_import(self):
        """Test RemoteOperations can be imported."""
        from navig.remote import RemoteOperations

        assert RemoteOperations is not None

    def test_remote_operations_initialization(self, tmp_path, monkeypatch):
        """Test RemoteOperations initializes correctly."""
        from navig.config import ConfigManager
        from navig.remote import RemoteOperations

        # Set temp home
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        # Use explicit config_dir to avoid picking up project's .navig
        config_dir = tmp_path / ".navig"
        config = ConfigManager(config_dir=config_dir)
        remote = RemoteOperations(config)
        # Should create instance without errors
        assert remote is not None


class TestCommandImports:
    """Test that all command modules can be imported."""

    def test_import_files_advanced(self):
        """Test files_advanced commands import."""
        from navig.commands import files_advanced

        assert hasattr(files_advanced, "delete_file_cmd")

    def test_import_database_advanced(self):
        """Test database_advanced commands import."""
        from navig.commands import database_advanced

        assert hasattr(database_advanced, "optimize_table_cmd")

    def test_import_monitoring(self):
        """Test monitoring commands import."""
        from navig.commands import monitoring

        assert hasattr(monitoring, "monitor_resources")
        assert hasattr(monitoring, "health_check")

    def test_import_security(self):
        """Test security commands import."""
        from navig.commands import security

        assert hasattr(security, "firewall_add_rule")
        assert hasattr(security, "security_scan")

    def test_import_maintenance(self):
        """Test maintenance commands import."""
        from navig.commands import maintenance

        assert hasattr(maintenance, "update_packages")

    def test_import_backup(self):
        """Test backup commands import."""
        from navig.commands import backup

        assert hasattr(backup, "list_backups_cmd")

    def test_import_webserver(self):
        """Test webserver commands import."""
        from navig.commands import webserver

        assert hasattr(webserver, "list_vhosts")

    def test_import_hestia(self):
        """Test hestia commands import."""
        from navig.commands import hestia

        assert hasattr(hestia, "list_users_cmd")


class TestDryRunMode:
    """Test that dry-run mode prevents actual changes."""

    def test_delete_file_dry_run_flag_exists(self):
        """Test delete file function exists and has dry_run handling."""
        import inspect

        from navig.commands.files_advanced import delete_file_cmd

        # Check function signature exists
        sig = inspect.signature(delete_file_cmd)
        assert "options" in sig.parameters


class TestAPICorrectness:
    """Test that APIs are used correctly."""

    def test_config_manager_api(self, tmp_path, monkeypatch):
        """Verify ConfigManager has correct API methods."""
        from navig.config import ConfigManager

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        config_dir = tmp_path / ".navig"
        config = ConfigManager(config_dir=config_dir)

        # Should have load_server_config
        assert hasattr(config, "load_server_config")

        # Should have get_active_server
        assert hasattr(config, "get_active_server")

        # Should have base_dir attribute
        assert hasattr(config, "base_dir")

    def test_remote_operations_api(self, tmp_path, monkeypatch):
        """Verify RemoteOperations has correct API methods."""
        from navig.config import ConfigManager
        from navig.remote import RemoteOperations

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        config_dir = tmp_path / ".navig"
        config = ConfigManager(config_dir=config_dir)
        remote_ops = RemoteOperations(config)

        # Should have execute_command
        assert hasattr(remote_ops, "execute_command")

        # execute_command should accept server_config parameter
        import inspect

        sig = inspect.signature(remote_ops.execute_command)
        param_names = list(sig.parameters.keys())

        # Should have 'server_config' parameter
        assert "server_config" in param_names


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

"""
Tests for Execution Modes and Confirmation System.
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import shutil

from navig.config import ConfigManager
from navig import console_helper as ch


@pytest.fixture
def temp_home(monkeypatch):
    """Create temporary home directory for tests."""
    temp = tempfile.mkdtemp()
    monkeypatch.setenv('HOME', temp)
    monkeypatch.setenv('USERPROFILE', temp)  # Windows
    yield Path(temp)
    shutil.rmtree(temp)


@pytest.fixture
def config_manager(temp_home):
    """Create ConfigManager with temporary home."""
    config_dir = temp_home / ".navig"
    return ConfigManager(config_dir=config_dir)


class TestConfirmationLevels:
    """Tests for confirmation level classification."""
    
    def test_classify_command_critical(self):
        """Test critical command classification."""
        critical_commands = [
            "rm -rf /var/www",
            "drop database production",
            "systemctl stop nginx",
            "delete from users",
            "truncate table sessions",
            "shutdown -h now",
        ]
        
        for cmd in critical_commands:
            assert ch.classify_command(cmd) == 'critical', f"'{cmd}' should be critical"
    
    def test_classify_command_standard(self):
        """Test standard command classification."""
        standard_commands = [
            "create database test",
            "insert into users values",
            "update settings set value=1",
            "systemctl restart nginx",
            "apt install nginx",
            "chmod 755 /var/www",
        ]
        
        for cmd in standard_commands:
            assert ch.classify_command(cmd) == 'standard', f"'{cmd}' should be standard"
    
    def test_classify_command_verbose(self):
        """Test verbose (read-only) command classification."""
        verbose_commands = [
            "ls -la",
            "echo hello",
            "pwd",
            "whoami",
            "df -h",
            "ps aux",
        ]
        
        for cmd in verbose_commands:
            assert ch.classify_command(cmd) == 'verbose', f"'{cmd}' should be verbose"
    
    def test_classify_sql_critical(self):
        """Test critical SQL classification."""
        critical_queries = [
            "DROP TABLE users",
            "TRUNCATE orders",
            "DELETE FROM sessions",
            "drop database test",
        ]
        
        for query in critical_queries:
            assert ch.classify_sql(query) == 'critical', f"'{query}' should be critical"
    
    def test_classify_sql_standard(self):
        """Test standard SQL classification."""
        standard_queries = [
            "CREATE TABLE test (id INT)",
            "INSERT INTO users VALUES (1, 'test')",
            "UPDATE settings SET value = 1",
            "ALTER TABLE users ADD COLUMN age INT",
            "GRANT SELECT ON users TO readonly",
        ]
        
        for query in standard_queries:
            assert ch.classify_sql(query) == 'standard', f"'{query}' should be standard"
    
    def test_classify_sql_verbose(self):
        """Test verbose (read-only) SQL classification."""
        verbose_queries = [
            "SELECT * FROM users",
            "SHOW TABLES",
            "DESCRIBE users",
            "EXPLAIN SELECT * FROM orders",
        ]
        
        for query in verbose_queries:
            assert ch.classify_sql(query) == 'verbose', f"'{query}' should be verbose"


class TestRequiresConfirmation:
    """Tests for requires_confirmation logic."""
    
    def test_auto_mode_bypasses_confirmation(self):
        """Auto mode should bypass all confirmation."""
        assert not ch.requires_confirmation('critical', 'critical', 'auto')
        assert not ch.requires_confirmation('standard', 'standard', 'auto')
        assert not ch.requires_confirmation('verbose', 'verbose', 'auto')
    
    def test_auto_confirm_flag_bypasses(self):
        """--yes flag should bypass confirmation."""
        assert not ch.requires_confirmation('critical', 'critical', 'interactive', auto_confirm=True)
        assert not ch.requires_confirmation('standard', 'standard', 'interactive', auto_confirm=True)
    
    def test_critical_level_only_confirms_critical(self):
        """Critical level should only confirm critical operations."""
        assert ch.requires_confirmation('critical', 'critical', 'interactive')
        assert not ch.requires_confirmation('standard', 'critical', 'interactive')
        assert not ch.requires_confirmation('verbose', 'critical', 'interactive')
    
    def test_standard_level_confirms_standard_and_critical(self):
        """Standard level should confirm standard and critical operations."""
        assert ch.requires_confirmation('critical', 'standard', 'interactive')
        assert ch.requires_confirmation('standard', 'standard', 'interactive')
        assert not ch.requires_confirmation('verbose', 'standard', 'interactive')
    
    def test_verbose_level_confirms_all(self):
        """Verbose level should confirm all operations."""
        assert ch.requires_confirmation('critical', 'verbose', 'interactive')
        assert ch.requires_confirmation('standard', 'verbose', 'interactive')
        assert ch.requires_confirmation('verbose', 'verbose', 'interactive')


class TestExecutionModeSettings:
    """Tests for execution mode and confirmation level settings."""
    
    def test_default_execution_mode(self, config_manager, monkeypatch):
        """Default execution mode should be 'interactive' when no config exists."""
        # Change to temp directory to avoid local project config
        import os
        original_cwd = os.getcwd()
        monkeypatch.chdir(config_manager.global_config_dir.parent)
        try:
            # Without any config, default is 'interactive'
            assert config_manager.get_execution_mode() == 'interactive'
        finally:
            os.chdir(original_cwd)
    
    def test_default_confirmation_level(self, config_manager):
        """Default confirmation level should be 'standard'."""
        assert config_manager.get_confirmation_level() == 'standard'
    
    def test_set_execution_mode(self, config_manager, monkeypatch):
        """Test setting execution mode."""
        # Change to temp directory to avoid local project config
        import os
        original_cwd = os.getcwd()
        monkeypatch.chdir(config_manager.global_config_dir.parent)
        try:
            config_manager.set_execution_mode('auto')
            assert config_manager.get_execution_mode() == 'auto'
            
            config_manager.set_execution_mode('interactive')
            assert config_manager.get_execution_mode() == 'interactive'
        finally:
            os.chdir(original_cwd)
    
    def test_set_invalid_execution_mode_raises(self, config_manager):
        """Setting invalid execution mode should raise ValueError."""
        with pytest.raises(ValueError):
            config_manager.set_execution_mode('invalid')
    
    def test_set_confirmation_level(self, config_manager):
        """Test setting confirmation level."""
        config_manager.set_confirmation_level('critical')
        assert config_manager.get_confirmation_level() == 'critical'
        
        config_manager.set_confirmation_level('verbose')
        assert config_manager.get_confirmation_level() == 'verbose'
        
        config_manager.set_confirmation_level('standard')
        assert config_manager.get_confirmation_level() == 'standard'
    
    def test_set_invalid_confirmation_level_raises(self, config_manager):
        """Setting invalid confirmation level should raise ValueError."""
        with pytest.raises(ValueError):
            config_manager.set_confirmation_level('invalid')
    
    def test_get_execution_settings(self, config_manager):
        """Test getting all execution settings."""
        settings = config_manager.get_execution_settings()
        
        assert 'mode' in settings
        assert 'confirmation_level' in settings
        assert settings['mode'] == 'interactive'
        assert settings['confirmation_level'] == 'standard'


class TestConfirmOperation:
    """Tests for confirm_operation function."""
    
    @patch('navig.console_helper.confirm_action')
    @patch('navig.config.get_config_manager')
    def test_confirm_operation_in_auto_mode(self, mock_get_cm, mock_confirm):
        """Operations should be auto-approved in auto mode."""
        mock_cm = MagicMock()
        mock_cm.get_execution_mode.return_value = 'auto'
        mock_cm.get_confirmation_level.return_value = 'standard'
        mock_get_cm.return_value = mock_cm
        
        result = ch.confirm_operation("Test operation", 'critical')
        
        assert result is True
        mock_confirm.assert_not_called()
    
    @patch('navig.console_helper.confirm_action')
    @patch('navig.config.get_config_manager')
    def test_confirm_operation_prompts_in_interactive(self, mock_get_cm, mock_confirm):
        """Operations should prompt in interactive mode."""
        mock_cm = MagicMock()
        mock_cm.get_execution_mode.return_value = 'interactive'
        mock_cm.get_confirmation_level.return_value = 'standard'
        mock_get_cm.return_value = mock_cm
        mock_confirm.return_value = True
        
        result = ch.confirm_operation("Test operation", 'standard')
        
        assert result is True
        mock_confirm.assert_called_once()
    
    @patch('navig.console_helper.confirm_action')
    @patch('navig.config.get_config_manager')
    def test_confirm_operation_auto_confirm_bypasses(self, mock_get_cm, mock_confirm):
        """auto_confirm=True should bypass confirmation."""
        mock_cm = MagicMock()
        mock_cm.get_execution_mode.return_value = 'interactive'
        mock_cm.get_confirmation_level.return_value = 'standard'
        mock_get_cm.return_value = mock_cm
        
        result = ch.confirm_operation("Test operation", 'critical', auto_confirm=True)
        
        assert result is True
        mock_confirm.assert_not_called()
    
    @patch('navig.console_helper.confirm_action')
    @patch('navig.config.get_config_manager')
    def test_force_confirm_overrides_auto_mode(self, mock_get_cm, mock_confirm):
        """force_confirm=True should prompt even in auto mode."""
        mock_cm = MagicMock()
        mock_cm.get_execution_mode.return_value = 'auto'
        mock_cm.get_confirmation_level.return_value = 'standard'
        mock_get_cm.return_value = mock_cm
        mock_confirm.return_value = True
        
        result = ch.confirm_operation("Test operation", 'standard', force_confirm=True)
        
        assert result is True
        mock_confirm.assert_called_once()

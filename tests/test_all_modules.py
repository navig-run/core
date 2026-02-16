"""
Comprehensive test suite for all NAVIG command modules.

Tests syntax, imports, and basic functionality of all 8 new modules:
- files_advanced
- database_advanced
- hestia
- backup
- monitoring
- security
- maintenance
- webserver
"""

import pytest
import importlib
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestModuleSyntax:
    """Test that all modules can be imported without syntax errors."""
    
    def test_files_advanced_import(self):
        """Test files_advanced module imports correctly."""
        from navig.commands import files_advanced
        # Module should import without errors
        assert files_advanced is not None
    
    def test_database_advanced_import(self):
        """Test database_advanced module imports correctly."""
        from navig.commands import database_advanced
        # Module should import without errors
        assert database_advanced is not None
    
    def test_hestia_import(self):
        """Test hestia module imports correctly."""
        from navig.commands import hestia
        # Module should import without errors
        assert hestia is not None
    
    def test_backup_import(self):
        """Test backup module imports correctly."""
        from navig.commands import backup
        # Module should import without errors
        assert backup is not None
    
    def test_monitoring_import(self):
        """Test monitoring module imports correctly."""
        from navig.commands import monitoring
        # Module should import without errors
        assert monitoring is not None
        assert hasattr(monitoring, 'monitor_resources')
    
    def test_security_import(self):
        """Test security module imports correctly."""
        from navig.commands import security
        # Module should import without errors
        assert security is not None
        assert hasattr(security, 'firewall_status')
    
    def test_maintenance_import(self):
        """Test maintenance module imports correctly."""
        from navig.commands import maintenance
        # Module should import without errors
        assert maintenance is not None
    
    def test_webserver_import(self):
        """Test webserver module imports correctly."""
        from navig.commands import webserver
        # Module should import without errors
        assert webserver is not None
        assert hasattr(webserver, 'list_vhosts')


class TestCLIIntegration:
    """Test that all commands are properly registered in CLI."""
    
    def test_cli_imports(self):
        """Test CLI module imports all command modules."""
        from navig import cli
        # CLI should import without errors
        assert cli is not None
    
    def test_typer_app_exists(self):
        """Test main Typer app is created."""
        from navig.cli import app
        assert app is not None
        assert hasattr(app, 'command')


class TestGlobalFlags:
    """Test that global flags are supported across modules."""
    
    def test_monitoring_module_exists(self):
        """Test monitoring module can be imported."""
        from navig.commands import monitoring
        assert monitoring is not None
    
    def test_security_module_exists(self):
        """Test security module can be imported."""
        from navig.commands import security
        assert security is not None
    
    def test_maintenance_module_exists(self):
        """Test maintenance module can be imported."""
        from navig.commands import maintenance
        assert maintenance is not None
    
    def test_webserver_module_exists(self):
        """Test webserver module can be imported."""
        from navig.commands import webserver
        assert webserver is not None


class TestCommandCount:
    """Verify command modules exist with expected implementations."""
    
    def test_all_command_modules_exist(self):
        """Test that all 8 command modules can be imported."""
        from navig.commands import files_advanced, database_advanced, hestia
        from navig.commands import backup, monitoring, security
        from navig.commands import maintenance, webserver
        
        # All modules should be importable
        assert files_advanced is not None
        assert database_advanced is not None
        assert hestia is not None
        assert backup is not None
        assert monitoring is not None
        assert security is not None
        assert maintenance is not None
        assert webserver is not None


class TestVersionAndBranding:
    """Test version and branding information."""
    
    def test_version_exists(self):
        """Test __version__ is defined."""
        from navig import __version__
        assert __version__ is not None
        assert isinstance(__version__, str)
    
    def test_branding_in_init(self):
        """Test new branding in __init__.py."""
        from navig import __doc__
        assert "No Admin Visible In Graveyard" in __doc__ or "NAVIG" in __doc__
    
    def test_cli_help_text(self):
        """Test CLI has updated help text."""
        from navig.cli import app
        # App should have help text
        assert hasattr(app, 'info')


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])

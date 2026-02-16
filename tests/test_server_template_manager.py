"""
Tests for Server Template Configuration System

Tests cover:
- Template auto-detection (n8n, HestiaCP, Gitea)
- Per-server initialization from templates
- Configuration customization and merging
- Template syncing with preserve/overwrite logic
- Enable/disable per server
"""

import pytest
import json
import yaml
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from navig.server_template_manager import ServerTemplateManager
from navig.config import ConfigManager
from navig.template_manager import TemplateManager
from navig.discovery import ServerDiscovery


@pytest.fixture
def temp_navig_dir():
    """Create temporary .navig directory."""
    temp_dir = Path(tempfile.gettempdir()) / f"navig_test_{datetime.now().timestamp()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    yield temp_dir
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def temp_templates_dir():
    """Create temporary templates directory with test template templates."""
    temp_dir = Path(tempfile.gettempdir()) / f"templates_test_{datetime.now().timestamp()}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Create test template: n8n
    n8n_dir = temp_dir / "n8n"
    n8n_dir.mkdir()
    n8n_metadata = {
        "name": "n8n",
        "version": "1.0.0",
        "description": "n8n workflow automation",
        "author": "test",
        "enabled": False,
        "dependencies": [],
        "paths": {
            "n8n_home": "/root/.n8n",
            "workflows_dir": "/root/.n8n/workflows"
        },
        "services": {
            "automation": "n8n.service"
        },
        "env_vars": {
            "N8N_PORT": "5678"
        }
    }
    (n8n_dir / "template.json").write_text(json.dumps(n8n_metadata, indent=2))
    
    # Create test template: gitea
    gitea_dir = temp_dir / "gitea"
    gitea_dir.mkdir()
    gitea_metadata = {
        "name": "gitea",
        "version": "1.5.0",
        "description": "Gitea Git service",
        "author": "test",
        "enabled": False,
        "dependencies": [],
        "paths": {
            "gitea_root": "/var/lib/gitea",
            "repositories": "/var/lib/gitea/repositories"
        },
        "services": {
            "git": "gitea.service"
        }
    }
    (gitea_dir / "template.json").write_text(json.dumps(gitea_metadata, indent=2))
    
    yield temp_dir
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def config_manager(temp_navig_dir, monkeypatch):
    """Create ConfigManager with temporary directory."""
    monkeypatch.setattr('navig.config.Path.home', lambda: temp_navig_dir.parent)
    monkeypatch.setenv('HOME', str(temp_navig_dir.parent))
    
    # Override base_dir
    cm = ConfigManager()
    cm.base_dir = temp_navig_dir
    cm.config_file = temp_navig_dir / "config.yaml"
    cm.apps_dir = temp_navig_dir / "apps"
    cm.cache_dir = temp_navig_dir / "cache"
    cm.backups_dir = temp_navig_dir / "backups"
    cm._ensure_directories()
    
    return cm


@pytest.fixture
def template_manager(temp_templates_dir):
    """Create TemplateManager with test templates."""
    am = TemplateManager(templates_dir=temp_templates_dir)
    am.discover_templates()
    return am


@pytest.fixture
def server_template_manager(config_manager, template_manager):
    """Create ServerTemplateManager with test fixtures."""
    return ServerTemplateManager(config_manager, template_manager)


@pytest.fixture
def test_server(config_manager):
    """Create a test server configuration."""
    server_name = "test_server"
    config = config_manager.create_server_config(
        name=server_name,
        host="10.0.0.10",
        port=22,
        user="testuser",
        ssh_key="~/.ssh/id_rsa"
    )
    return server_name


# ============================================================================
# DETECTION TESTS
# ============================================================================

class TestTemplateDetection:
    """Test template auto-detection logic."""
    
    def test_n8n_detection_structure(self):
        """Test that n8n detection returns correct structure."""
        discovery = ServerDiscovery({
            'host': 'test.example.com',
            'port': 22,
            'user': 'test',
            'ssh_key': '~/.ssh/id_rsa'
        })
        
        # Mock SSH execution to simulate n8n installed
        def mock_execute(cmd):
            if "systemctl is-active n8n" in cmd:
                return (True, "active", "")
            if "n8n --version" in cmd:
                return (True, "1.18.2", "")
            if "ss -tlnp" in cmd and ":5678" in cmd:
                return (True, "LISTEN :5678", "")
            if "test -d ~/.n8n" in cmd:
                return (True, "exists", "")
            if "echo $HOME" in cmd:
                return (True, "/root", "")
            return (False, "", "")
        
        discovery._execute_ssh = mock_execute
        info = discovery._detect_n8n()
        
        assert info['detected'] == True
        assert info['version'] == '1.18.2'
        assert 'n8n.service' in info['services']
        assert 5678 in info['ports']
        assert '/root/.n8n' in info['paths'].get('n8n_home', '')
    
    def test_hestiacp_detection_structure(self):
        """Test that HestiaCP detection returns correct structure."""
        discovery = ServerDiscovery({
            'host': 'test.example.com',
            'port': 22,
            'user': 'test',
            'ssh_key': '~/.ssh/id_rsa'
        })
        
        # Mock SSH execution to simulate HestiaCP installed
        def mock_execute(cmd):
            if "test -d /usr/local/hestia" in cmd:
                return (True, "exists", "")
            if "which v-list-users" in cmd:
                return (True, "/usr/local/hestia/bin/v-list-users", "")
            if "v-list-sys-info" in cmd:
                return (True, '"version":"1.8.5"', "")
            if "systemctl is-active hestia" in cmd:
                return (True, "active", "")
            if "ss -tlnp" in cmd and ":8083" in cmd:
                return (True, "LISTEN :8083", "")
            return (False, "", "")
        
        discovery._execute_ssh = mock_execute
        info = discovery._detect_hestiacp()
        
        assert info['detected'] == True
        assert info['version'] == '1.8.5'
        assert 'hestia' in info['services']
        assert 8083 in info['ports']
        assert '/usr/local/hestia' in info['paths'].get('hestia_root', '')
    
    def test_gitea_detection_structure(self):
        """Test that Gitea detection returns correct structure."""
        discovery = ServerDiscovery({
            'host': 'test.example.com',
            'port': 22,
            'user': 'test',
            'ssh_key': '~/.ssh/id_rsa'
        })
        
        # Mock SSH execution to simulate Gitea installed
        def mock_execute(cmd):
            if "systemctl is-active gitea" in cmd:
                return (True, "active", "")
            if "gitea --version" in cmd:
                return (True, "Gitea version 1.21.3", "")
            if "test -f /usr/local/bin/gitea" in cmd:
                return (True, "exists", "")
            if "ss -tlnp" in cmd and ":3000" in cmd:
                return (True, "LISTEN :3000", "")
            if "test -d /var/lib/gitea" in cmd:
                return (True, "exists", "")
            return (False, "", "")
        
        discovery._execute_ssh = mock_execute
        info = discovery._detect_gitea()
        
        assert info['detected'] == True
        assert info['version'] == '1.21.3'
        assert 'gitea.service' in info['services']
        assert 3000 in info['ports']
        assert '/usr/local/bin/gitea' in info['paths'].get('gitea_binary', '')


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================

class TestServerTemplateInitialization:
    """Test per-server template initialization."""
    
    def test_initialize_from_detection(self, server_template_manager, test_server):
        """Test initializing templates from detection results."""
        detected_templates = {
            'n8n': {
                'detected': True,
                'version': '1.18.2',
                'paths': {
                    'n8n_home': '/custom/.n8n',
                    'workflows_dir': '/custom/.n8n/workflows'
                },
                'services': ['n8n.service'],
                'ports': [5678]
            }
        }
        
        results = server_template_manager.initialize_templates_from_detection(test_server, detected_templates)
        
        assert results['n8n'] == True
        
        # Verify server config was updated
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        assert 'templates' in server_config
        assert 'n8n' in server_config['templates']
        
        template_state = server_config['templates']['n8n']
        assert template_state['enabled'] == True
        assert template_state['auto_detected'] == True
        assert template_state['template_version'] == '1.0.0'
        assert template_state['detection_info']['version'] == '1.18.2'
    
    def test_initialize_manually(self, server_template_manager, test_server):
        """Test manual template initialization."""
        success = server_template_manager.initialize_template_manually(test_server, 'gitea', enabled=True)
        
        assert success == True
        
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        assert 'gitea' in server_config['templates']
        
        template_state = server_config['templates']['gitea']
        assert template_state['enabled'] == True
        assert template_state['auto_detected'] == False
        assert template_state['template_version'] == '1.5.0'
    
    def test_initialize_nonexistent_template(self, server_template_manager, test_server):
        """Test initializing template that doesn't have template."""
        success = server_template_manager.initialize_template_manually(test_server, 'nonexistent', enabled=False)
        
        assert success == False


# ============================================================================
# CONFIGURATION MERGING TESTS
# ============================================================================

class TestTemplateConfigMerging:
    """Test configuration merging logic."""
    
    def test_get_config_template_only(self, server_template_manager, test_server):
        """Test getting config from template only (no customization)."""
        # Initialize template
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        
        # Get config
        config = server_template_manager.get_template_config(test_server, 'n8n', include_template=True)
        
        assert config is not None
        assert config['paths']['n8n_home'] == '/root/.n8n'
        assert config['services']['automation'] == 'n8n.service'
        assert config['env_vars']['N8N_PORT'] == '5678'
    
    def test_get_config_with_detection_override(self, server_template_manager, test_server):
        """Test config merging with detection info override."""
        # Initialize from detection
        detected_templates = {
            'n8n': {
                'detected': True,
                'version': '1.18.2',
                'paths': {
                    'n8n_home': '/custom/.n8n',  # Different from template
                    'workflows_dir': '/custom/.n8n/workflows'
                },
                'services': ['n8n.service'],
                'ports': [5678]
            }
        }
        server_template_manager.initialize_templates_from_detection(test_server, detected_templates)
        
        # Get merged config
        config = server_template_manager.get_template_config(test_server, 'n8n', include_template=True)
        
        assert config['paths']['n8n_home'] == '/custom/.n8n'  # Detection override
        assert config['paths']['workflows_dir'] == '/custom/.n8n/workflows'  # Detection override
        assert config['env_vars']['N8N_PORT'] == '5678'  # From template
    
    def test_get_config_with_custom_override(self, server_template_manager, test_server):
        """Test config merging with custom file override."""
        # Initialize template
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        
        # Set custom value
        server_template_manager.set_template_custom_value(test_server, 'n8n', 'env_vars.N8N_PORT', '9999')
        
        # Get merged config
        config = server_template_manager.get_template_config(test_server, 'n8n', include_template=True)
        
        assert config['env_vars']['N8N_PORT'] == '9999'  # Custom override
        assert config['paths']['n8n_home'] == '/root/.n8n'  # From template
    
    def test_deep_merge_nested_configs(self, server_template_manager):
        """Test deep merge logic with nested dictionaries."""
        base = {
            'paths': {
                'root': '/base/root',
                'logs': '/base/logs'
            },
            'services': {
                'web': 'nginx'
            }
        }
        
        overlay = {
            'paths': {
                'logs': '/custom/logs',  # Override
                'data': '/custom/data'   # New key
            },
            'env': {
                'NEW_VAR': 'value'  # New section
            }
        }
        
        merged = server_template_manager._deep_merge(base, overlay)
        
        assert merged['paths']['root'] == '/base/root'  # Preserved
        assert merged['paths']['logs'] == '/custom/logs'  # Overridden
        assert merged['paths']['data'] == '/custom/data'  # Added
        assert merged['services']['web'] == 'nginx'  # Preserved
        assert merged['env']['NEW_VAR'] == 'value'  # Added


# ============================================================================
# CUSTOMIZATION TESTS
# ============================================================================

class TestTemplateCustomization:
    """Test per-server template customization."""
    
    def test_set_custom_value_simple(self, server_template_manager, test_server):
        """Test setting a simple custom value."""
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        
        success = server_template_manager.set_template_custom_value(
            test_server, 'n8n', 'env_vars.N8N_PORT', '9999'
        )
        
        assert success == True
        
        # Verify custom config file created (now uses YAML format)
        custom_file = server_template_manager._get_server_template_dir(test_server) / "n8n.yaml"
        assert custom_file.exists()
        
        with open(custom_file, 'r') as f:
            custom_config = yaml.safe_load(f)
        
        assert custom_config['env_vars']['N8N_PORT'] == '9999'
        
        # Verify marked as customized
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        assert server_config['templates']['n8n']['customized'] == True
    
    def test_set_custom_value_nested(self, server_template_manager, test_server):
        """Test setting nested custom value."""
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        
        success = server_template_manager.set_template_custom_value(
            test_server, 'n8n', 'paths.workflows_dir', '/new/workflows'
        )
        
        assert success == True
        
        config = server_template_manager.get_template_config(test_server, 'n8n')
        assert config['paths']['workflows_dir'] == '/new/workflows'
    
    def test_enable_disable_template(self, server_template_manager, test_server):
        """Test enabling and disabling template."""
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=False)
        
        # Enable
        success = server_template_manager.enable_template(test_server, 'n8n')
        assert success == True
        
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        assert server_config['templates']['n8n']['enabled'] == True
        assert 'last_enabled' in server_config['templates']['n8n']
        
        # Disable
        success = server_template_manager.disable_template(test_server, 'n8n')
        assert success == True
        
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        assert server_config['templates']['n8n']['enabled'] == False
        assert 'last_disabled' in server_config['templates']['n8n']
    
    def test_list_server_templates(self, server_template_manager, test_server):
        """Test listing templates for a server."""
        # Initialize multiple templates
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        server_template_manager.initialize_template_manually(test_server, 'gitea', enabled=False)
        
        # List all
        all_templates = server_template_manager.list_server_templates(test_server, enabled_only=False)
        assert len(all_templates) == 2
        
        # List enabled only
        enabled_templates = server_template_manager.list_server_templates(test_server, enabled_only=True)
        assert len(enabled_templates) == 1
        assert enabled_templates[0]['name'] == 'n8n'


# ============================================================================
# SYNC TESTS
# ============================================================================

class TestTemplateSync:
    """Test template syncing logic."""
    
    def test_sync_preserves_custom_values(self, server_template_manager, test_server):
        """Test that sync preserves custom values by default."""
        # Initialize and customize
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        server_template_manager.set_template_custom_value(test_server, 'n8n', 'env_vars.N8N_PORT', '9999')
        
        # Sync from template
        success = server_template_manager.sync_template_from_template(test_server, 'n8n', preserve_custom=True)
        assert success == True
        
        # Verify custom value still present
        config = server_template_manager.get_template_config(test_server, 'n8n')
        assert config['env_vars']['N8N_PORT'] == '9999'  # Custom preserved
    
    def test_sync_updates_version(self, server_template_manager, test_server, temp_templates_dir):
        """Test that sync updates template version."""
        # Initialize template
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        
        # Manually set old version
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        server_config['templates']['n8n']['template_version'] = '0.9.0'
        server_template_manager.config_manager.save_server_config(test_server, server_config)
        
        # Update template version
        n8n_template_file = temp_templates_dir / "n8n" / "template.json"
        with open(n8n_template_file, 'r') as f:
            metadata = json.load(f)
        metadata['version'] = '2.0.0'
        with open(n8n_template_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        # Reload template manager to pick up new version
        server_template_manager.template_manager.discover_templates()
        
        # Sync
        success = server_template_manager.sync_template_from_template(test_server, 'n8n')
        assert success == True
        
        # Verify version updated
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        assert server_config['templates']['n8n']['template_version'] == '2.0.0'
    
    def test_sync_without_preserve_resets_custom(self, server_template_manager, test_server):
        """Test that sync without preserve flag resets to template."""
        # Initialize and customize
        server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        server_template_manager.set_template_custom_value(test_server, 'n8n', 'env_vars.N8N_PORT', '9999')
        
        # Verify custom value exists
        config = server_template_manager.get_template_config(test_server, 'n8n')
        assert config['env_vars']['N8N_PORT'] == '9999'
        
        # Sync without preserving custom (note: custom file still exists, but version updated)
        success = server_template_manager.sync_template_from_template(test_server, 'n8n', preserve_custom=False)
        assert success == True
        
        # Custom file still exists (sync doesn't delete it)
        # But timestamp updated to indicate sync occurred
        server_config = server_template_manager.config_manager.load_server_config(test_server)
        assert 'last_synced' in server_config['templates']['n8n']


# ============================================================================
# EDGE CASES TESTS
# ============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_get_config_uninitialized_template(self, server_template_manager, test_server):
        """Test getting config for uninitialized template."""
        config = server_template_manager.get_template_config(test_server, 'n8n')
        assert config is None
    
    def test_enable_uninitialized_template(self, server_template_manager, test_server):
        """Test enabling template that isn't initialized."""
        success = server_template_manager.enable_template(test_server, 'n8n')
        assert success == False
    
    def test_set_value_uninitialized_template(self, server_template_manager, test_server):
        """Test setting value for uninitialized template."""
        success = server_template_manager.set_template_custom_value(test_server, 'n8n', 'paths.test', '/test')
        assert success == False
    
    def test_sync_uninitialized_template(self, server_template_manager, test_server):
        """Test syncing uninitialized template."""
        success = server_template_manager.sync_template_from_template(test_server, 'n8n')
        assert success == False
    
    def test_initialize_duplicate_template(self, server_template_manager, test_server):
        """Test initializing template twice."""
        success1 = server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        assert success1 == True
        
        success2 = server_template_manager.initialize_template_manually(test_server, 'n8n', enabled=True)
        assert success2 == True  # Should succeed but warn (already initialized)

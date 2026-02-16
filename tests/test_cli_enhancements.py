"""
Tests for CLI enhancements: auto-detection, enhanced commands, list flags.
"""

import pytest
import yaml
from pathlib import Path
import tempfile
import shutil

from navig.config import ConfigManager


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
    """Create ConfigManager with temporary home, isolated from project's .navig."""
    # Pass explicit config_dir to skip auto-detection from cwd
    # which would find the project's actual .navig directory
    config_dir = temp_home / ".navig"
    return ConfigManager(config_dir=config_dir)


@pytest.fixture
def multi_host_setup(config_manager):
    """Create multiple hosts with apps for testing."""
    # Host 1: myhost with 3 apps
    host1_config = {
        'name': 'myhost',
        'host': 'example.host',
        'port': 22,
        'user': 'root',
        'ssh_key': '~/.ssh/myhost',
        'default_app': 'myapp',
        'apps': {
            'myapp': {
                'webserver': {'type': 'apache2'},
                'database': {'type': 'mysql', 'name': 'myapp_db'}
            },
            'staging': {
                'webserver': {'type': 'apache2'},
                'database': {'type': 'mysql', 'name': 'staging_db'}
            },
            'ai': {
                'webserver': {'type': 'nginx'},
                'database': {'type': 'postgresql', 'name': 'ai_db'}
            }
        }
    }
    
    # Host 2: example-vps with 2 apps
    host2_config = {
        'name': 'example-vps',
        'host': 'example-vps.com',
        'port': 22,
        'user': 'admin',
        'ssh_key': '~/.ssh/example-vps',
        'default_app': 'portfolio',
        'apps': {
            'portfolio': {
                'webserver': {'type': 'nginx'},
                'database': {'type': 'mysql', 'name': 'portfolio_db'}
            },
            'blog': {
                'webserver': {'type': 'nginx'},
                'database': {'type': 'mysql', 'name': 'blog_db'}
            }
        }
    }
    
    # Host 3: local with 1 app named "staging" (duplicate name)
    host3_config = {
        'name': 'local',
        'host': 'localhost',
        'port': 22,
        'user': 'dev',
        'ssh_key': '~/.ssh/local',
        'default_app': 'staging',
        'apps': {
            'staging': {
                'webserver': {'type': 'nginx'},
                'database': {'type': 'mysql', 'name': 'local_staging_db'}
            }
        }
    }
    
    config_manager.save_host_config('myhost', host1_config)
    config_manager.save_host_config('example-vps', host2_config)
    config_manager.save_host_config('local', host3_config)
    
    return config_manager


class TestAppAutoDetection:
    """Test --app flag with auto-detection of host."""
    
    def test_find_hosts_with_app_single_match(self, multi_host_setup):
        """Test finding app that exists on only one host."""
        hosts = multi_host_setup.find_hosts_with_app('myapp')
        assert hosts == ['myhost']
    
    def test_find_hosts_with_app_multiple_matches(self, multi_host_setup):
        """Test finding app that exists on multiple hosts."""
        hosts = multi_host_setup.find_hosts_with_app('staging')
        assert set(hosts) == {'myhost', 'local'}
    
    def test_find_hosts_with_app_no_match(self, multi_host_setup):
        """Test finding app that doesn't exist on any host."""
        hosts = multi_host_setup.find_hosts_with_app('nonexistent')
        assert hosts == []
    
    def test_find_hosts_with_app_case_sensitive(self, multi_host_setup):
        """Test that app search is case-sensitive."""
        hosts = multi_host_setup.find_hosts_with_app('myapp')
        assert hosts == []  # Should not match 'myapp'


class TestListCommandEnhancements:
    """Test enhanced list commands with --all and --format flags."""
    
    def test_list_hosts_returns_all_hosts(self, multi_host_setup):
        """Test that list_hosts returns all configured hosts."""
        hosts = multi_host_setup.list_hosts()
        assert set(hosts) == {'myhost', 'example-vps', 'local'}
    
    def test_list_apps_single_host(self, multi_host_setup):
        """Test listing apps on a single host."""
        apps = multi_host_setup.list_apps('myhost')
        assert set(apps) == {'myapp', 'staging', 'ai'}
    
    def test_list_apps_empty_host(self, config_manager):
        """Test listing apps on host with no apps."""
        config = {
            'name': 'empty-host',
            'host': 'empty.com',
            'port': 22,
            'user': 'root',
            'ssh_key': '~/.ssh/empty',
            'apps': {}
        }
        config_manager.save_host_config('empty-host', config)
        
        apps = config_manager.list_apps('empty-host')
        assert apps == []


class TestAppSearch:
    """Test app search functionality."""
    
    def test_search_finds_matching_apps(self, multi_host_setup):
        """Test that search finds apps matching query."""
        # This would be tested via the search_apps function
        # For now, we test the underlying find_hosts_with_app
        hosts_with_ai = multi_host_setup.find_hosts_with_app('ai')
        assert hosts_with_ai == ['myhost']
    
    def test_search_partial_match(self, multi_host_setup):
        """Test that search can find partial matches."""
        # Search for apps containing 'port'
        all_apps = []
        for host in multi_host_setup.list_hosts():
            apps = multi_host_setup.list_apps(host)
            all_apps.extend([(host, p) for p in apps if 'port' in p.lower()])
        
        # Should find 'portfolio' on example-vps
        assert ('example-vps', 'portfolio') in all_apps



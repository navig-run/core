"""
Shared pytest fixtures for NAVIG test suite.

This module provides common fixtures used across all test files:
- Mock configurations
- Console mocks
- Temporary directories
- Sample data factories
"""

import os
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, Mock

import pytest
import yaml


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config():
    """Mock NAVIG configuration."""
    return {
        'current_host': 'test-server',
        'hosts': {
            'test-server': {
                'host': 'test.example.com',
                'user': 'testuser',
                'port': 22,
                'key_file': None,
                'password': None,
            }
        },
        'settings': {
            'auto_confirm': False,
            'color_output': True,
            'verbose': False,
        }
    }


@pytest.fixture
def mock_config_file(temp_dir, mock_config):
    """Create a temporary config file with mock data."""
    config_file = temp_dir / "config.yaml"
    with open(config_file, 'w') as f:
        yaml.dump(mock_config, f)
    return config_file


@pytest.fixture
def mock_console():
    """Mock Rich console for output testing."""
    console = Mock()
    console.print = Mock()
    console.log = Mock()
    return console


@pytest.fixture
def mock_ssh_client():
    """Mock paramiko SSH client."""
    client = MagicMock()
    client.connect = Mock()
    client.exec_command = Mock(return_value=(Mock(), Mock(), Mock()))
    client.close = Mock()
    return client


@pytest.fixture
def sample_host_config() -> Dict[str, Any]:
    """Sample host configuration."""
    return {
        'host': '10.0.0.10',
        'user': 'admin',
        'port': 22,
        'key_file': '/path/to/key.pem',
        'password': None,
        'tunnel_port': 3307,
    }


@pytest.fixture
def sample_app_config() -> Dict[str, Any]:
    """Sample application configuration."""
    return {
        'name': 'test-app',
        'host': 'test-server',
        'domain': 'test-app.example.com',
        'path': '/var/www/test-app',
        'type': 'php',
        'database': 'test_app_db',
        'db_user': 'test_app_user',
    }


@pytest.fixture
def mock_subprocess_run(monkeypatch):
    """Mock subprocess.run for command execution tests."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "Command executed successfully"
    mock_result.stderr = ""
    
    mock_run = Mock(return_value=mock_result)
    monkeypatch.setattr('subprocess.run', mock_run)
    
    return mock_run


@pytest.fixture
def mock_paramiko_client(monkeypatch):
    """Mock paramiko SSHClient for remote operation tests."""
    mock_client = MagicMock()
    
    # Mock exec_command to return stdin, stdout, stderr
    mock_stdout = Mock()
    mock_stdout.read = Mock(return_value=b"Command output")
    mock_stdout.channel.recv_exit_status = Mock(return_value=0)
    
    mock_stderr = Mock()
    mock_stderr.read = Mock(return_value=b"")
    
    mock_stdin = Mock()
    
    mock_client.exec_command = Mock(return_value=(mock_stdin, mock_stdout, mock_stderr))
    mock_client.connect = Mock()
    mock_client.close = Mock()
    
    def mock_ssh_client_factory():
        return mock_client
    
    monkeypatch.setattr('paramiko.SSHClient', mock_ssh_client_factory)
    
    return mock_client


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    test_env = {
        'NAVIG_CONFIG_DIR': '/tmp/navig-test',
        'NAVIG_AUTO_CONFIRM': 'false',
        'OPENROUTER_API_KEY': 'test-api-key',
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)
    return test_env


@pytest.fixture
def sample_template_yaml() -> str:
    """Sample template.yaml content for scaffold tests."""
    return """meta:
  name: Test Template
  description: A test template
  version: 1.0.0
  author: Test Author

files:
  - path: config/app.conf
    type: file
    mode: "0644"
    content: |
      # Application config
      APP_NAME={{ app_name }}
      APP_ENV={{ app_env }}
  
  - path: scripts
    type: directory
    mode: "0755"
  
  - path: scripts/setup.sh
    type: file
    mode: "0755"
    content: |
      #!/bin/bash
      echo "Setting up {{ app_name }}"
"""


@pytest.fixture
def capture_output(monkeypatch):
    """Capture printed output for assertion."""
    output = []
    
    def mock_print(*args, **kwargs):
        output.append(' '.join(str(arg) for arg in args))
    
    monkeypatch.setattr('builtins.print', mock_print)
    
    return output

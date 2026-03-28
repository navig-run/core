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
        "current_host": "test-server",
        "hosts": {
            "test-server": {
                "host": "test.example.com",
                "user": "testuser",
                "port": 22,
                "key_file": None,
                "password": None,
            }
        },
        "settings": {
            "auto_confirm": False,
            "color_output": True,
            "verbose": False,
        },
    }


@pytest.fixture
def mock_config_file(temp_dir, mock_config):
    """Create a temporary config file with mock data."""
    config_file = temp_dir / "config.yaml"
    with open(config_file, "w") as f:
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
        "host": "10.0.0.10",
        "user": "admin",
        "port": 22,
        "key_file": "/path/to/key.pem",
        "password": None,
        "tunnel_port": 3307,
    }


@pytest.fixture
def sample_app_config() -> Dict[str, Any]:
    """Sample application configuration."""
    return {
        "name": "test-app",
        "host": "test-server",
        "domain": "test-app.example.com",
        "path": "/var/www/test-app",
        "type": "php",
        "database": "test_app_db",
        "db_user": "test_app_user",
    }


@pytest.fixture
def mock_subprocess_run(monkeypatch):
    """Mock subprocess.run for command execution tests."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "Command executed successfully"
    mock_result.stderr = ""

    mock_run = Mock(return_value=mock_result)
    monkeypatch.setattr("subprocess.run", mock_run)

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

    monkeypatch.setattr("paramiko.SSHClient", mock_ssh_client_factory)

    return mock_client


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    test_env = {
        "NAVIG_CONFIG_DIR": "/tmp/navig-test",
        "NAVIG_AUTO_CONFIRM": "false",
        "OPENROUTER_API_KEY": "test-api-key",
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
        output.append(" ".join(str(arg) for arg in args))

    monkeypatch.setattr("builtins.print", mock_print)

    return output


@pytest.fixture
def log_messages():
    """Capture log records emitted by the navig logger tree.

    Because ``navig.core.logging`` configures the root ``navig`` logger with
    ``propagate = False`` and stores ``sys.stderr`` at handler-creation time,
    neither ``caplog``, ``capsys``, nor ``capfd`` see these messages.  This
    fixture attaches a temporary ``ListHandler`` directly to ``logging.getLogger
    ("navig")`` so tests can assert on log text without relying on fd-level or
    sys.stderr-level capture.
    """
    import logging

    records: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _ListHandler(level=logging.DEBUG)
    # Attach to both "navig" and "navig.daemon": the supervisor sets
    # navig.daemon.propagate = False via _make_logger(), so if any test
    # creates a NavigDaemon instance before this fixture runs the message
    # from navig.daemon.telegram_worker would never reach the "navig"
    # handler.  Attaching directly to "navig.daemon" covers that path.
    navig_logger = logging.getLogger("navig")
    daemon_logger = logging.getLogger("navig.daemon")
    # Ensure a permissive level so INFO records are not silently dropped when
    # _configure_root_logger() has not yet been called (e.g., when these tests
    # run in isolation the effective level would otherwise fall back to the
    # root logger's WARNING default).
    original_level = navig_logger.level
    if navig_logger.level == logging.NOTSET:
        navig_logger.setLevel(logging.DEBUG)
    navig_logger.addHandler(handler)
    daemon_logger.addHandler(handler)
    try:
        yield records
    finally:
        navig_logger.removeHandler(handler)
        daemon_logger.removeHandler(handler)
        navig_logger.setLevel(original_level)


# Keep navig_log_capture as an alias so existing tests using either name work.
navig_log_capture = log_messages


# ---------------------------------------------------------------------------
# Test isolation: prevent ConfigManager singleton and platform path cache from
# leaking real project config (~/.navig or .navig/) into unit tests.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _isolate_navig_config_dir(tmp_path_factory):
    """Point NAVIG_CONFIG_DIR at an empty temp directory for the whole session.

    Without this, ConfigManager._find_app_root() walks up from CWD and finds the
    real project .navig/ directory, causing tests that run after a config-loading
    test to receive production config in their ConfigManager instances.
    """
    isolated = tmp_path_factory.mktemp("navig_cfg_isolated")
    old_value = os.environ.get("NAVIG_CONFIG_DIR")
    os.environ["NAVIG_CONFIG_DIR"] = str(isolated)
    yield isolated
    # Restore previous value (or remove if it wasn't set before the session).
    if old_value is None:
        os.environ.pop("NAVIG_CONFIG_DIR", None)
    else:
        os.environ["NAVIG_CONFIG_DIR"] = old_value


@pytest.fixture(autouse=True)
def _reset_navig_singletons():
    """Reset module-level singletons before and after every test.

    - ConfigManager singleton: prevents a real ConfigManager created by test A
      from being returned by ``get_config_manager()`` in test B.
    - ``navig.platform.paths._DETECTED_OS``: prevents the OS-detection cache
      set during one test from affecting path calculations in subsequent tests.
    """
    try:
        from navig.config import reset_config_manager

        reset_config_manager()
    except Exception:  # noqa: BLE001 — never block test collection
        pass
    try:
        import navig.platform.paths as _paths

        _paths._DETECTED_OS = None
    except Exception:  # noqa: BLE001
        pass
    yield
    try:
        from navig.config import reset_config_manager

        reset_config_manager()
    except Exception:  # noqa: BLE001
        pass
    try:
        import navig.platform.paths as _paths

        _paths._DETECTED_OS = None
    except Exception:  # noqa: BLE001
        pass

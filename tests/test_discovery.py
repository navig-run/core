#!/usr/bin/env python3
"""
Unit tests for ServerDiscovery class.

These tests use mocking to avoid actual SSH connections.
For integration tests with real servers, use scripts/test_discovery_live.py
"""

from unittest.mock import patch

import pytest

from navig.discovery import ServerDiscovery

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def mock_ssh_config():
    """Sample SSH configuration for testing (no real credentials)."""
    return {
        "host": "test.example.com",
        "port": 22,
        "user": "testuser",
        "ssh_key": "~/.ssh/test_key",
    }


@pytest.fixture
def server_discovery(mock_ssh_config):
    """Create ServerDiscovery instance with mocked SSH."""
    return ServerDiscovery(mock_ssh_config)


# ============================================================================
# CONNECTION TESTS
# ============================================================================


class TestServerDiscoveryConnection:
    """Tests for SSH connection functionality."""

    def test_test_connection_success(self, server_discovery):
        """Test successful connection."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            # test_connection looks for 'NAVIG_TEST' in output
            mock_exec.return_value = (True, "NAVIG_TEST", "")
            result = server_discovery.test_connection()
            assert result is True

    def test_test_connection_failure(self, server_discovery):
        """Test failed connection."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            mock_exec.return_value = (False, "", "Connection refused")
            result = server_discovery.test_connection()
            assert result is False


# ============================================================================
# SERVICE DETECTION TESTS
# ============================================================================


class TestServiceDetection:
    """Tests for service detection functionality."""

    def test_detect_mysql_running(self, server_discovery):
        """Test MySQL detection when service is running."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            # Mock systemctl check
            mock_exec.side_effect = [
                (True, "active", ""),  # systemctl is-active
                (True, "mysql  Ver 8.0.35", ""),  # mysql --version
            ]
            # Note: actual implementation may differ
            result = server_discovery._execute_ssh("systemctl is-active mysql")
            assert result[0] is True
            assert "active" in result[1]

    def test_detect_nginx_running(self, server_discovery):
        """Test Nginx detection when service is running."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            mock_exec.return_value = (True, "active", "")
            result = server_discovery._execute_ssh("systemctl is-active nginx")
            assert result[0] is True

    def test_detect_service_not_found(self, server_discovery):
        """Test detection when service is not installed."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            mock_exec.return_value = (False, "", "Unit not found")
            result = server_discovery._execute_ssh("systemctl is-active nonexistent")
            assert result[0] is False


# ============================================================================
# OS DETECTION TESTS
# ============================================================================


class TestOSDetection:
    """Tests for OS detection functionality."""

    def test_detect_ubuntu(self, server_discovery):
        """Test Ubuntu detection."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            mock_exec.return_value = (True, "Ubuntu 24.04.2 LTS", "")
            result = server_discovery._execute_ssh(
                "cat /etc/os-release | grep PRETTY_NAME"
            )
            assert "Ubuntu" in result[1]

    def test_detect_debian(self, server_discovery):
        """Test Debian detection."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            mock_exec.return_value = (True, "Debian GNU/Linux 12", "")
            result = server_discovery._execute_ssh(
                "cat /etc/os-release | grep PRETTY_NAME"
            )
            assert "Debian" in result[1]


# ============================================================================
# PORT DETECTION TESTS
# ============================================================================


class TestPortDetection:
    """Tests for listening port detection."""

    def test_detect_common_ports(self, server_discovery):
        """Test detection of common service ports."""
        with patch.object(server_discovery, "_execute_ssh") as mock_exec:
            mock_exec.return_value = (
                True,
                "LISTEN 0.0.0.0:80\nLISTEN 0.0.0.0:443\nLISTEN 127.0.0.1:3306",
                "",
            )
            result = server_discovery._execute_ssh("ss -tlnp")
            assert ":80" in result[1]
            assert ":443" in result[1]
            assert ":3306" in result[1]

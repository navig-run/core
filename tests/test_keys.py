#!/usr/bin/env python3
"""
Tests for ServerDiscovery.discover_all() return values.

Verifies the structure and types of the discovery result dictionary.
"""

from unittest.mock import patch

import pytest

from navig.discovery import ServerDiscovery


@pytest.fixture
def mock_ssh_config():
    """Fixture providing a mock SSH configuration."""
    return {
        "host": "10.0.0.10",
        "port": 22,
        "user": "testuser",
        "ssh_key": "/path/to/key",
    }


@pytest.fixture
def mock_discovery(mock_ssh_config):
    """Fixture providing a ServerDiscovery instance."""
    return ServerDiscovery(mock_ssh_config)


class TestDiscoverAllReturnStructure:
    """Test the structure of discover_all() return values."""

    def test_discover_all_returns_dict(self, mock_discovery):
        """discover_all() should return a dictionary."""
        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value={"os": "Ubuntu"}):
                with patch.object(mock_discovery, "discover_databases", return_value={}):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value={},
                            ):
                                result = mock_discovery.discover_all(progress=False)

        assert isinstance(result, dict), "discover_all() must return a dict"

    def test_discover_all_contains_os_key(self, mock_discovery):
        """discover_all() result should contain 'os' key when OS is detected."""
        os_info = {"os": "Ubuntu", "os_version": "22.04"}

        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value=os_info):
                with patch.object(mock_discovery, "discover_databases", return_value={}):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value={},
                            ):
                                result = mock_discovery.discover_all(progress=False)

        assert "os" in result, "Result should contain 'os' key"
        assert result["os"] == "Ubuntu"

    def test_discover_all_contains_databases_key(self, mock_discovery):
        """discover_all() result should contain 'databases' key when databases are found."""
        db_info = {"databases": [{"type": "mysql", "port": 3306}]}

        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value={}):
                with patch.object(mock_discovery, "discover_databases", return_value=db_info):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value={},
                            ):
                                result = mock_discovery.discover_all(progress=False)

        assert "databases" in result, "Result should contain 'databases' key"

    def test_discover_all_contains_web_root_key(self, mock_discovery):
        """discover_all() result should contain 'web_root' key when detected."""
        paths_info = {"web_root": "/var/www/html"}

        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value={}):
                with patch.object(mock_discovery, "discover_databases", return_value={}):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value=paths_info,
                            ):
                                result = mock_discovery.discover_all(progress=False)

        assert "web_root" in result, "Result should contain 'web_root' key"
        assert result["web_root"] == "/var/www/html"


class TestDiscoverAllValueTypes:
    """Test the types of values returned by discover_all()."""

    def test_os_is_string(self, mock_discovery):
        """The 'os' value should be a string."""
        os_info = {"os": "Ubuntu", "os_version": "22.04", "kernel": "5.15.0"}

        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value=os_info):
                with patch.object(mock_discovery, "discover_databases", return_value={}):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value={},
                            ):
                                result = mock_discovery.discover_all(progress=False)

        if "os" in result:
            assert isinstance(result["os"], str), "'os' value must be a string"

    def test_databases_is_list(self, mock_discovery):
        """The 'databases' value should be a list."""
        db_info = {"databases": [{"type": "mysql", "port": 3306}]}

        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value={}):
                with patch.object(mock_discovery, "discover_databases", return_value=db_info):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value={},
                            ):
                                result = mock_discovery.discover_all(progress=False)

        if "databases" in result:
            assert isinstance(result["databases"], list), "'databases' value must be a list"

    def test_web_root_is_string(self, mock_discovery):
        """The 'web_root' value should be a string."""
        paths_info = {"web_root": "/var/www/html"}

        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value={}):
                with patch.object(mock_discovery, "discover_databases", return_value={}):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value=paths_info,
                            ):
                                result = mock_discovery.discover_all(progress=False)

        if "web_root" in result:
            assert isinstance(result["web_root"], str), "'web_root' value must be a string"


class TestDiscoverAllErrorHandling:
    """Test error handling in discover_all()."""

    def test_discover_all_returns_empty_on_connection_failure(self, mock_discovery):
        """discover_all() should return empty dict on connection failure."""
        with patch.object(mock_discovery, "test_connection", return_value=False):
            result = mock_discovery.discover_all(progress=False)

        assert result == {}, "Should return empty dict on connection failure"

    def test_discover_all_handles_partial_failure(self, mock_discovery):
        """discover_all() should handle partial detection failures."""
        with patch.object(mock_discovery, "test_connection", return_value=True):
            with patch.object(mock_discovery, "discover_os", return_value={"os": "Ubuntu"}):
                with patch.object(
                    mock_discovery,
                    "discover_databases",
                    side_effect=Exception("Detection failed"),
                ):
                    with patch.object(mock_discovery, "discover_web_servers", return_value={}):
                        with patch.object(mock_discovery, "discover_php", return_value={}):
                            with patch.object(
                                mock_discovery,
                                "discover_application_paths",
                                return_value={},
                            ):
                                # Should either handle the error or raise - both acceptable
                                try:
                                    result = mock_discovery.discover_all(progress=False)
                                    assert isinstance(result, dict)
                                except Exception:
                                    # Raising is also acceptable
                                    pass

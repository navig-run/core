"""
Tests for SSH Connection Pool

These tests verify the connection pool functionality without
requiring actual SSH connections.
"""

import time
from unittest.mock import Mock, patch

from navig.connection_pool import PooledSSHConnection as SSHConnection
from navig.connection_pool import SSHConnectionPool


class TestSSHConnection:
    """Test SSHConnection wrapper class."""

    def test_connection_key(self):
        """Test connection key generation."""
        mock_client = Mock()
        conn = SSHConnection(mock_client, "example.com", 22, "admin")
        assert conn.key == "admin@example.com:22"

    def test_connection_key_custom_port(self):
        """Test connection key with custom port."""
        mock_client = Mock()
        conn = SSHConnection(mock_client, "example.com", 2222, "root")
        assert conn.key == "root@example.com:2222"

    def test_age_tracking(self):
        """Test connection age tracking."""
        mock_client = Mock()
        conn = SSHConnection(mock_client, "example.com", 22, "admin")

        # Age should be very small initially
        assert conn.age_seconds < 1

        # Simulate time passing
        conn.created_at = time.time() - 60
        assert abs(conn.age_seconds - 60) < 1

    def test_idle_tracking(self):
        """Test connection idle time tracking."""
        mock_client = Mock()
        conn = SSHConnection(mock_client, "example.com", 22, "admin")

        # Idle should be very small initially
        assert conn.idle_seconds < 1

        # Simulate time passing without use
        conn.last_used = time.time() - 30
        assert abs(conn.idle_seconds - 30) < 1

    def test_is_alive_active_transport(self):
        """Test is_alive with active transport."""
        mock_client = Mock()
        mock_transport = Mock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport

        conn = SSHConnection(mock_client, "example.com", 22, "admin")
        assert conn.is_alive() is True

    def test_is_alive_inactive_transport(self):
        """Test is_alive with inactive transport."""
        mock_client = Mock()
        mock_transport = Mock()
        mock_transport.is_active.return_value = False
        mock_client.get_transport.return_value = mock_transport

        conn = SSHConnection(mock_client, "example.com", 22, "admin")
        assert conn.is_alive() is False

    def test_is_alive_no_transport(self):
        """Test is_alive with no transport."""
        mock_client = Mock()
        mock_client.get_transport.return_value = None

        conn = SSHConnection(mock_client, "example.com", 22, "admin")
        assert conn.is_alive() is False

    def test_execute_success(self):
        """Test successful command execution."""
        mock_client = Mock()
        mock_stdin = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()

        mock_stdout.read.return_value = b"output text"
        mock_stderr.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0

        mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

        conn = SSHConnection(mock_client, "example.com", 22, "admin")
        success, stdout, stderr = conn.execute("uptime")

        assert success is True
        assert stdout == "output text"
        assert stderr == ""
        assert conn.use_count == 1

    def test_execute_failure(self):
        """Test failed command execution."""
        mock_client = Mock()
        mock_stdin = Mock()
        mock_stdout = Mock()
        mock_stderr = Mock()

        mock_stdout.read.return_value = b""
        mock_stderr.read.return_value = b"command not found"
        mock_stdout.channel.recv_exit_status.return_value = 1

        mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

        conn = SSHConnection(mock_client, "example.com", 22, "admin")
        success, stdout, stderr = conn.execute("invalid_command")

        assert success is False
        assert stdout == ""
        assert stderr == "command not found"

    def test_execute_exception(self):
        """Test command execution with exception."""
        mock_client = Mock()
        mock_client.exec_command.side_effect = Exception("Connection lost")

        conn = SSHConnection(mock_client, "example.com", 22, "admin")
        success, stdout, stderr = conn.execute("uptime")

        assert success is False
        assert stdout == ""
        assert "Connection lost" in stderr

    def test_close(self):
        """Test connection close."""
        mock_client = Mock()
        conn = SSHConnection(mock_client, "example.com", 22, "admin")

        conn.close()
        mock_client.close.assert_called_once()


class TestSSHConnectionPool:
    """Test SSHConnectionPool class."""

    def setup_method(self):
        """Reset singleton before each test."""
        SSHConnectionPool.reset_instance()

    def teardown_method(self):
        """Clean up after each test."""
        SSHConnectionPool.reset_instance()

    def test_singleton_pattern(self):
        """Test that get_instance returns the same instance."""
        pool1 = SSHConnectionPool.get_instance()
        pool2 = SSHConnectionPool.get_instance()
        assert pool1 is pool2

    def test_make_key(self):
        """Test connection key generation."""
        pool = SSHConnectionPool()

        config = {"host": "server.com", "port": 22, "user": "admin"}
        key = pool._make_key(config)
        assert key == "admin@server.com:22"

        config = {"host": "server.com", "user": "root"}  # Default port
        key = pool._make_key(config)
        assert key == "root@server.com:22"

    def test_initial_stats(self):
        """Test initial pool statistics."""
        pool = SSHConnectionPool()
        stats = pool.stats

        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["connections_created"] == 0
        assert stats["active_connections"] == 0
        assert stats["hit_rate"] == 0.0

    @patch("navig.connection_pool._get_paramiko")
    def test_get_connection_creates_new(self, mock_get_paramiko):
        """Test that get_connection creates new connection on first call."""
        mock_paramiko = Mock()
        mock_client = Mock()
        mock_transport = Mock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = Mock()
        mock_get_paramiko.return_value = mock_paramiko

        pool = SSHConnectionPool()
        config = {
            "host": "server.com",
            "port": 22,
            "user": "admin",
            "ssh_password": "secret",
        }

        conn = pool.get_connection(config)

        assert conn is not None
        assert pool.stats["misses"] == 1
        assert pool.stats["connections_created"] == 1
        assert pool.active_count == 1

    @patch("navig.connection_pool._get_paramiko")
    def test_get_connection_reuses_existing(self, mock_get_paramiko):
        """Test that get_connection reuses existing connection."""
        mock_paramiko = Mock()
        mock_client = Mock()
        mock_transport = Mock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = Mock()
        mock_get_paramiko.return_value = mock_paramiko

        pool = SSHConnectionPool()
        config = {
            "host": "server.com",
            "port": 22,
            "user": "admin",
            "ssh_password": "secret",
        }

        conn1 = pool.get_connection(config)
        conn2 = pool.get_connection(config)

        assert conn1 is conn2
        assert pool.stats["misses"] == 1  # Only first was a miss
        assert pool.stats["hits"] == 1  # Second was a hit
        assert pool.stats["connections_created"] == 1

    @patch("navig.connection_pool._get_paramiko")
    def test_connection_eviction_on_max(self, mock_get_paramiko):
        """Test that oldest connection is evicted when pool is full."""
        mock_paramiko = Mock()

        def create_mock_client():
            client = Mock()
            transport = Mock()
            transport.is_active.return_value = True
            client.get_transport.return_value = transport
            return client

        mock_paramiko.SSHClient.side_effect = create_mock_client
        mock_paramiko.AutoAddPolicy.return_value = Mock()
        mock_get_paramiko.return_value = mock_paramiko

        pool = SSHConnectionPool(max_connections=2)

        # Create 3 connections (should evict first one)
        pool.get_connection({"host": "server1.com", "user": "admin"})
        pool.get_connection({"host": "server2.com", "user": "admin"})
        pool.get_connection({"host": "server3.com", "user": "admin"})

        assert pool.active_count == 2
        assert pool.stats["connections_closed"] == 1

    @patch("navig.connection_pool._get_paramiko")
    def test_dead_connection_replaced(self, mock_get_paramiko):
        """Test that dead connections are replaced."""
        mock_paramiko = Mock()
        mock_client = Mock()
        mock_transport = Mock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.AutoAddPolicy.return_value = Mock()
        mock_get_paramiko.return_value = mock_paramiko

        pool = SSHConnectionPool()
        config = {"host": "server.com", "user": "admin"}

        # Get first connection
        conn1 = pool.get_connection(config)

        # Simulate connection death
        mock_transport.is_active.return_value = False

        # Get connection again - should create new one
        conn2 = pool.get_connection(config)

        assert conn1 is not conn2
        assert pool.stats["connections_created"] == 2

    def test_close_all(self):
        """Test closing all connections."""
        pool = SSHConnectionPool()

        # Add some mock connections directly
        mock_conn1 = Mock(spec=SSHConnection)
        mock_conn1.age_seconds = 0
        mock_conn1.idle_seconds = 0
        mock_conn1.is_alive.return_value = True

        mock_conn2 = Mock(spec=SSHConnection)
        mock_conn2.age_seconds = 0
        mock_conn2.idle_seconds = 0
        mock_conn2.is_alive.return_value = True

        pool._connections["user@host1:22"] = mock_conn1
        pool._connections["user@host2:22"] = mock_conn2

        pool.close_all()

        assert pool.active_count == 0
        mock_conn1.close.assert_called_once()
        mock_conn2.close.assert_called_once()

    def test_get_connection_info(self):
        """Test getting connection info."""
        pool = SSHConnectionPool()

        # Add a mock connection
        mock_client = Mock()
        mock_transport = Mock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport

        conn = SSHConnection(mock_client, "server.com", 22, "admin")
        conn.use_count = 5
        pool._connections["admin@server.com:22"] = conn

        info = pool.get_connection_info()

        assert len(info) == 1
        assert info[0]["key"] == "admin@server.com:22"
        assert info[0]["use_count"] == 5
        assert info[0]["alive"] is True

    def test_hit_rate_calculation(self):
        """Test hit rate calculation."""
        pool = SSHConnectionPool()
        pool._stats["hits"] = 7
        pool._stats["misses"] = 3

        stats = pool.stats
        assert stats["hit_rate"] == 0.7

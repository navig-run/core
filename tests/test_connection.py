# Tests for NAVIG Connection Adapters
"""
Test suite for ConnectionAdapter classes (LocalConnection, SSHConnection).
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from navig.core.connection import (
    CommandResult,
    LocalConnection,
    SSHConnection,
    get_connection,
)


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_command_result_creation(self):
        """Test creating a CommandResult."""
        result = CommandResult(
            stdout="hello world", stderr="", exit_code=0, duration=1.5
        )
        assert result.stdout == "hello world"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.duration == 1.5

    def test_command_result_to_dict(self):
        """Test CommandResult to_dict method."""
        result = CommandResult(
            stdout="output", stderr="error", exit_code=1, duration=2.0
        )
        d = result.to_dict()
        assert d["stdout"] == "output"
        assert d["stderr"] == "error"
        assert d["exit_code"] == 1
        assert d["duration"] == 2.0

    def test_command_result_success_property(self):
        """Test success property of CommandResult."""
        success_result = CommandResult("", "", 0, 0.1)
        assert success_result.success is True

        fail_result = CommandResult("", "", 1, 0.1)
        assert fail_result.success is False


class TestLocalConnection:
    """Tests for LocalConnection class."""

    def test_local_connection_creation(self):
        """Test creating a LocalConnection."""
        conn = LocalConnection()
        # LocalConnection doesn't have is_connected - it's always connected
        assert conn is not None

    def test_local_connection_with_os_type(self):
        """Test LocalConnection with explicit OS type."""
        conn = LocalConnection(os_type="linux")
        assert conn._os_type == "linux"

    def test_run_simple_command(self):
        """Test running a simple command."""
        conn = LocalConnection()

        # Test echo command (works on all platforms)
        if sys.platform == "win32":
            result = conn.run("echo hello")
        else:
            result = conn.run("echo hello")

        assert result.exit_code == 0
        assert "hello" in result.stdout.lower()
        assert result.duration >= 0

    def test_run_command_with_timeout(self):
        """Test command timeout handling."""
        conn = LocalConnection()

        # Very short timeout should work for fast commands
        result = conn.run("echo test", timeout=30.0)
        assert result.exit_code == 0

    def test_run_failing_command(self):
        """Test running a command that fails."""
        conn = LocalConnection()

        # Command that should fail on any platform
        if sys.platform == "win32":
            result = conn.run("exit 1", timeout=5.0)
        else:
            result = conn.run("exit 1", timeout=5.0)

        assert result.exit_code == 1

    def test_detect_os(self):
        """Test OS detection."""
        conn = LocalConnection()
        detected_os = conn.detect_os()

        assert detected_os in ["windows", "linux", "macos", "darwin"]

        # Verify it matches platform
        if sys.platform == "win32":
            assert detected_os == "windows"
        elif sys.platform == "darwin":
            assert detected_os in ["macos", "darwin"]
        else:
            assert detected_os == "linux"

    def test_close(self):
        """Test closing connection."""
        conn = LocalConnection()
        conn.close()
        # Should not raise any errors
        assert True

    def test_upload_creates_copy(self, tmp_path):
        """Test that upload copies file locally."""
        conn = LocalConnection()

        # Create source file
        src = tmp_path / "source.txt"
        src.write_text("test content")

        dest = tmp_path / "dest.txt"

        result = conn.upload(src, dest)

        # Should succeed and create the dest file
        assert result is True
        assert dest.exists()
        assert dest.read_text() == "test content"

    def test_download_creates_copy(self, tmp_path):
        """Test that download copies file locally."""
        conn = LocalConnection()

        # Create source file
        src = tmp_path / "source.txt"
        src.write_text("test content")

        dest = tmp_path / "dest.txt"

        result = conn.download(src, dest)

        # Should succeed and create the dest file
        assert result is True
        assert dest.exists()


class TestSSHConnection:
    """Tests for SSHConnection class."""

    def test_ssh_connection_creation(self):
        """Test creating an SSHConnection."""
        config = {"host": "example.com", "user": "testuser", "port": 22}
        conn = SSHConnection(config)
        assert conn is not None

    def test_ssh_connection_stores_config(self):
        """Test SSHConnection stores config properly."""
        config = {
            "host": "server.example.com",
            "user": "admin",
            "port": 2222,
            "ssh_key": "/home/user/.ssh/id_rsa",
        }
        conn = SSHConnection(config)
        assert conn.host_config == config

    def test_ssh_run_executes_command(self):
        """Test that run attempts to execute via SSH (will fail without actual SSH)."""
        config = {"host": "localhost", "user": "testuser", "port": 22}
        conn = SSHConnection(config)

        # This will fail because no SSH server, but should return a result
        result = conn.run("echo hello", timeout=2.0)
        assert isinstance(result, CommandResult)
        # Will have non-zero exit code because SSH fails
        assert result.exit_code != 0


class TestGetConnection:
    """Tests for get_connection factory function."""

    def test_get_connection_local(self):
        """Test getting a local connection."""
        config = {"type": "local"}
        conn = get_connection(config)

        assert isinstance(conn, LocalConnection)

    def test_get_connection_ssh(self):
        """Test getting an SSH connection."""
        config = {"type": "ssh", "host": "server.example.com", "user": "admin"}
        conn = get_connection(config)

        assert isinstance(conn, SSHConnection)
        assert conn.host_config["host"] == "server.example.com"

    def test_get_connection_ssh_with_all_options(self):
        """Test getting SSH connection with all options."""
        config = {
            "type": "ssh",
            "host": "server.example.com",
            "user": "admin",
            "port": 2222,
            "ssh_key": "/path/to/key",
        }
        conn = get_connection(config)

        assert isinstance(conn, SSHConnection)
        assert conn.host_config["port"] == 2222
        assert conn.host_config["ssh_key"] == "/path/to/key"

    def test_get_connection_default_to_ssh(self):
        """Test that unspecified type defaults to SSH."""
        config = {"host": "server.example.com", "user": "admin"}
        conn = get_connection(config)

        # Should default to SSH for configs with hostname
        assert isinstance(conn, SSHConnection)


class TestConnectionAdapterInterface:
    """Tests for ConnectionAdapter interface compliance."""

    def test_local_connection_implements_interface(self):
        """Verify LocalConnection implements all required methods."""
        conn = LocalConnection()

        # Check all abstract methods exist
        assert hasattr(conn, "run")
        assert hasattr(conn, "upload")
        assert hasattr(conn, "download")
        assert hasattr(conn, "detect_os")
        assert hasattr(conn, "close")

    def test_ssh_connection_implements_interface(self):
        """Verify SSHConnection implements all required methods."""
        config = {"host": "example.com", "user": "user"}
        conn = SSHConnection(config)

        # Check all abstract methods exist
        assert hasattr(conn, "run")
        assert hasattr(conn, "upload")
        assert hasattr(conn, "download")
        assert hasattr(conn, "detect_os")
        assert hasattr(conn, "close")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

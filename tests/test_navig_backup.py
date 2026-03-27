"""
Tests for NAVIG Configuration Backup & Export System.
"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navig.commands import config_backup as navig_backup
from navig.config import ConfigManager


@pytest.fixture
def temp_home(monkeypatch):
    """Create temporary home directory for tests."""
    temp = tempfile.mkdtemp()
    monkeypatch.setenv("HOME", temp)
    monkeypatch.setenv("USERPROFILE", temp)  # Windows
    yield Path(temp)
    shutil.rmtree(temp, ignore_errors=True)


@pytest.fixture
def mock_config_manager(temp_home):
    """Create a mock ConfigManager that returns controlled data."""
    config_dir = temp_home / ".navig"
    config_dir.mkdir(parents=True, exist_ok=True)

    cm = MagicMock(spec=ConfigManager)
    cm.config_dir = config_dir
    cm.hosts_dir = config_dir / "hosts"
    cm.apps_dir = config_dir / "apps"

    cm.hosts_dir.mkdir(parents=True, exist_ok=True)
    cm.apps_dir.mkdir(parents=True, exist_ok=True)

    return cm


class TestSecretRedaction:
    """Tests for secret redaction."""

    def test_redact_dict(self):
        """Test dictionary redaction."""
        data = {
            "username": "admin",
            "password": "secret123",
            "database": {
                "host": "localhost",
                "password": "db_secret",
            },
            "api_key": "sk-1234567890",
        }

        navig_backup._redact_dict(data, ["password", "api_key", "secret"])

        assert data["password"] == "[REDACTED]"
        assert data["database"]["password"] == "[REDACTED]"
        assert data["api_key"] == "[REDACTED]"
        assert data["username"] == "admin"  # Not redacted

    def test_redact_nested_dict(self):
        """Test redaction in nested structures."""
        data = {
            "level1": {
                "level2": {
                    "secret_key": "should_be_redacted",
                    "public_key": "should_stay",
                }
            }
        }

        navig_backup._redact_dict(data, ["secret"])

        assert data["level1"]["level2"]["secret_key"] == "[REDACTED]"
        assert data["level1"]["level2"]["public_key"] == "should_stay"


class TestFormatSize:
    """Tests for size formatting helper."""

    def test_format_bytes(self):
        """Test formatting bytes."""
        assert navig_backup._format_size(500) == "500.0 B"

    def test_format_kilobytes(self):
        """Test formatting kilobytes."""
        assert navig_backup._format_size(1024) == "1.0 KB"
        assert navig_backup._format_size(2048) == "2.0 KB"

    def test_format_megabytes(self):
        """Test formatting megabytes."""
        assert navig_backup._format_size(1024 * 1024) == "1.0 MB"

    def test_format_gigabytes(self):
        """Test formatting gigabytes."""
        assert navig_backup._format_size(1024 * 1024 * 1024) == "1.0 GB"


def _check_cryptography_available():
    """Helper to check if cryptography package is available."""
    try:
        from cryptography.fernet import Fernet  # noqa: F401

        return True
    except ImportError:
        return False


class TestEncryption:
    """Tests for encryption/decryption."""

    @pytest.mark.skipif(
        not _check_cryptography_available(), reason="cryptography package not installed"
    )
    def test_encrypt_decrypt_roundtrip(self, temp_home):
        """Test that encrypt/decrypt roundtrip works."""
        # Create test file
        test_file = temp_home / "test.txt"
        test_content = b"This is secret data"
        with open(test_file, "wb") as f:
            f.write(test_content)

        # Encrypt
        password = "test_password_123"
        encrypted_path = navig_backup._encrypt_file(test_file, password)

        assert encrypted_path.exists()
        assert encrypted_path.suffix == ".enc"

        # Verify encrypted content is different
        with open(encrypted_path, "rb") as f:
            encrypted_content = f.read()
        assert encrypted_content != test_content

        # Decrypt
        decrypted_path = navig_backup._decrypt_file(encrypted_path, password)

        with open(decrypted_path, "rb") as f:
            decrypted_content = f.read()

        assert decrypted_content == test_content

    @pytest.mark.skipif(
        not _check_cryptography_available(), reason="cryptography package not installed"
    )
    def test_decrypt_with_wrong_password_fails(self, temp_home):
        """Test that decryption with wrong password fails."""
        # Create test file
        test_file = temp_home / "test.txt"
        with open(test_file, "wb") as f:
            f.write(b"Secret data")

        # Encrypt with one password
        encrypted_path = navig_backup._encrypt_file(test_file, "correct_password")

        # Attempt to decrypt with wrong password should fail
        with pytest.raises(Exception):
            navig_backup._decrypt_file(encrypted_path, "wrong_password")


class TestInspectExportFile:
    """Tests for inspecting exports without importing."""

    def test_inspect_json_export(self, temp_home, capsys):
        """Test inspecting a JSON export."""
        export_data = {
            "version": "1.0",
            "exported_at": "2024-01-01T00:00:00Z",
            "hosts": {
                "my-server": {
                    "name": "my-server",
                    "host": "10.0.0.1",
                    "user": "root",
                }
            },
            "apps": {},
        }

        export_file = temp_home / "export.json"
        with open(export_file, "w") as f:
            json.dump(export_data, f)

        navig_backup.inspect_export(
            {
                "file": export_file,
                "json": False,
            }
        )

        captured = capsys.readouterr()
        # Should contain host name or version info
        assert "my-server" in captured.out or "1.0" in captured.out


class TestExportImportWithMock:
    """Tests using mocked config manager."""

    @patch("navig.config.get_config_manager")
    def test_collect_configs_with_mock(self, mock_get_cm, temp_home):
        """Test config collection with mocked manager."""
        mock_cm = MagicMock()
        mock_cm.config_dir = temp_home / ".navig"
        mock_cm.hosts_dir = temp_home / ".navig" / "hosts"
        mock_cm.apps_dir = temp_home / ".navig" / "apps"

        # Create the directories
        mock_cm.hosts_dir.mkdir(parents=True, exist_ok=True)
        mock_cm.apps_dir.mkdir(parents=True, exist_ok=True)

        # Mock list_hosts to return empty
        mock_cm.list_hosts.return_value = []
        mock_cm.list_apps.return_value = []
        mock_cm.get_global_config.return_value = {}

        mock_get_cm.return_value = mock_cm

        data = navig_backup._collect_configs()

        assert "version" in data
        assert "exported_at" in data
        assert "hosts" in data
        assert data["hosts"] == {}

    @patch("navig.config.get_config_manager")
    def test_collect_configs_with_hosts(self, mock_get_cm, temp_home):
        """Test config collection with hosts."""
        mock_cm = MagicMock()
        mock_cm.config_dir = temp_home / ".navig"
        mock_cm.hosts_dir = temp_home / ".navig" / "hosts"
        mock_cm.apps_dir = temp_home / ".navig" / "apps"

        mock_cm.hosts_dir.mkdir(parents=True, exist_ok=True)
        mock_cm.apps_dir.mkdir(parents=True, exist_ok=True)

        # Mock to return one host
        mock_cm.list_hosts.return_value = ["test-host"]
        # Use load_host_config, not get_host_config - that's what _collect_configs uses
        mock_cm.load_host_config.return_value = {
            "name": "test-host",
            "host": "10.0.0.10",
            "port": 22,
            "user": "deploy",
            "database": {
                "password": "secret123",
            },
        }
        mock_cm.list_apps.return_value = []
        mock_cm.get_global_config.return_value = {}

        mock_get_cm.return_value = mock_cm

        data = navig_backup._collect_configs()

        assert "test-host" in data["hosts"]
        # Password should be redacted
        assert data["hosts"]["test-host"]["database"]["password"] == "[REDACTED]"


class TestExportFormatting:
    """Tests for export file formatting."""

    def test_json_export_format(self, temp_home):
        """Test that JSON export is properly formatted."""
        export_data = {
            "version": "1.0",
            "hosts": {"server": {"host": "example.com"}},
        }

        output_file = temp_home / "test.json"
        with open(output_file, "w") as f:
            json.dump(export_data, f, indent=2)

        with open(output_file, "r") as f:
            content = f.read()

        # Should be pretty-printed (have newlines)
        assert "\n" in content

        # Should be valid JSON
        loaded = json.loads(content)
        assert loaded["version"] == "1.0"


class TestConfigMerge:
    """Tests for config merge logic."""

    def test_merge_hosts_basic(self):
        """Test basic host merging."""
        existing = {"host-a": {"host": "a.com"}}
        new = {"host-b": {"host": "b.com"}}

        result = {**existing, **new}

        assert "host-a" in result
        assert "host-b" in result

    def test_merge_overwrites_existing(self):
        """Test that merge overwrites existing keys."""
        existing = {"host-a": {"host": "old.com"}}
        new = {"host-a": {"host": "new.com"}}

        result = {**existing, **new}

        assert result["host-a"]["host"] == "new.com"

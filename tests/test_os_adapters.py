# Tests for NAVIG OS Adapters
"""
Test suite for OS adapter classes (Windows, Linux, macOS).
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from navig.adapters.os import OSAdapter, detect_os, get_os_adapter
from navig.adapters.os.base import PackageInfo, SecurityCheck
from navig.adapters.os.factory import get_os_adapter_for_remote


class TestOSDetection:
    """Tests for OS detection functions."""

    def test_detect_os_returns_valid_os(self):
        """Test that detect_os returns a valid OS name."""
        os_name = detect_os()
        assert os_name in ["windows", "linux", "macos"]

    def test_detect_os_matches_platform(self):
        """Test that detect_os matches the actual platform."""
        os_name = detect_os()

        if sys.platform == "win32":
            assert os_name == "windows"
        elif sys.platform == "darwin":
            assert os_name == "macos"
        else:
            assert os_name == "linux"

    def test_get_os_adapter_returns_adapter(self):
        """Test that get_os_adapter returns an OSAdapter instance."""
        adapter = get_os_adapter()
        assert isinstance(adapter, OSAdapter)

    def test_get_os_adapter_with_os_name(self):
        """Test getting adapter for specific OS."""
        for os_name in ["windows", "linux", "macos"]:
            adapter = get_os_adapter(os_name)
            assert isinstance(adapter, OSAdapter)
            assert adapter.name == os_name

    def test_get_os_adapter_invalid_os(self):
        """Test that invalid OS name raises error."""
        with pytest.raises(ValueError):
            get_os_adapter("invalid_os")


class TestPackageInfo:
    """Tests for PackageInfo dataclass."""

    def test_package_info_creation(self):
        """Test creating a PackageInfo."""
        pkg = PackageInfo(
            name="python",
            version="3.11.0",
            description="Python interpreter",
            source="apt",
        )
        assert pkg.name == "python"
        assert pkg.version == "3.11.0"
        assert pkg.description == "Python interpreter"
        assert pkg.source == "apt"

    def test_package_info_to_dict(self):
        """Test PackageInfo to_dict method."""
        pkg = PackageInfo(name="git", version="2.40.0", source="brew")
        d = pkg.to_dict()

        assert d["name"] == "git"
        assert d["version"] == "2.40.0"
        assert d["source"] == "brew"


class TestSecurityCheck:
    """Tests for SecurityCheck dataclass."""

    def test_security_check_creation(self):
        """Test creating a SecurityCheck."""
        check = SecurityCheck(
            category="firewall", status="ok", message="Firewall is active"
        )
        assert check.category == "firewall"
        assert check.status == "ok"
        assert check.message == "Firewall is active"

    def test_security_check_with_details(self):
        """Test SecurityCheck with details."""
        check = SecurityCheck(
            category="ports",
            status="warning",
            message="Many open ports",
            details={"count": 50},
        )
        assert check.details == {"count": 50}

    def test_security_check_to_dict(self):
        """Test SecurityCheck to_dict method."""
        check = SecurityCheck(
            category="users", status="critical", message="Root login enabled"
        )
        d = check.to_dict()

        assert d["category"] == "users"
        assert d["status"] == "critical"


class TestOSAdapterInterface:
    """Tests for OSAdapter interface compliance."""

    @pytest.fixture
    def adapter(self):
        """Get an adapter for the current OS."""
        return get_os_adapter()

    def test_adapter_has_name(self, adapter):
        """Test adapter has name property."""
        assert hasattr(adapter, "name")
        assert adapter.name in ["windows", "linux", "macos"]

    def test_adapter_has_display_name(self, adapter):
        """Test adapter has display_name property."""
        assert hasattr(adapter, "display_name")
        assert isinstance(adapter.display_name, str)
        assert len(adapter.display_name) > 0

    def test_get_package_list_command(self, adapter):
        """Test get_package_list_command returns a string."""
        cmd = adapter.get_package_list_command()
        assert isinstance(cmd, str)
        assert len(cmd) > 0

    def test_get_hosts_file_path(self, adapter):
        """Test get_hosts_file_path returns a Path."""
        path = adapter.get_hosts_file_path()
        assert isinstance(path, Path)
        # Should contain 'hosts' in the path
        assert "hosts" in str(path).lower()

    def test_get_home_directory(self, adapter):
        """Test get_home_directory returns a valid path."""
        path = adapter.get_home_directory()
        assert isinstance(path, Path)
        # Home directory should exist
        assert path.exists()

    def test_get_temp_directory(self, adapter):
        """Test get_temp_directory returns a valid path."""
        path = adapter.get_temp_directory()
        assert isinstance(path, Path)
        # Temp directory should exist
        assert path.exists()

    def test_check_admin_privileges(self, adapter):
        """Test check_admin_privileges returns boolean."""
        result = adapter.check_admin_privileges()
        assert isinstance(result, bool)

    def test_get_firewall_status_command(self, adapter):
        """Test firewall status command."""
        cmd = adapter.get_firewall_status_command()
        assert isinstance(cmd, str)

    def test_get_open_ports_command(self, adapter):
        """Test open ports command."""
        cmd = adapter.get_open_ports_command()
        assert isinstance(cmd, str)

    def test_default_shell(self, adapter):
        """Test default_shell property."""
        shell = adapter.default_shell
        assert isinstance(shell, str)
        assert len(shell) > 0

    def test_path_separator(self, adapter):
        """Test path_separator property."""
        sep = adapter.path_separator
        assert sep in ["/", "\\"]

    def test_line_ending(self, adapter):
        """Test line_ending property."""
        ending = adapter.line_ending
        assert ending in ["\n", "\r\n"]

    def test_get_ping_command(self, adapter):
        """Test ping command generation."""
        cmd = adapter.get_ping_command("google.com", count=2)
        assert isinstance(cmd, str)
        # Use exact token membership rather than substring containment to avoid
        # false-positive URL-sanitization warnings (CodeQL py/incomplete-url-substring-sanitization).
        tokens = cmd.split()
        assert "google.com" in tokens
        assert any(tok in ("2", "-c", "-n") for tok in tokens)


class TestWindowsAdapter:
    """Windows-specific adapter tests."""

    @pytest.fixture
    def adapter(self):
        """Get Windows adapter."""
        return get_os_adapter("windows")

    def test_windows_hosts_path(self, adapter):
        """Test Windows hosts file path."""
        path = adapter.get_hosts_file_path()
        assert "System32" in str(path) or "system32" in str(path)
        assert "hosts" in str(path)

    def test_windows_package_command(self, adapter):
        """Test Windows package list uses winget."""
        cmd = adapter.get_package_list_command()
        assert "winget" in cmd.lower()

    def test_windows_path_separator(self, adapter):
        """Test Windows path separator."""
        assert adapter.path_separator == "\\"

    def test_windows_line_ending(self, adapter):
        """Test Windows line ending."""
        assert adapter.line_ending == "\r\n"

    def test_parse_winget_output(self, adapter):
        """Test parsing winget list output."""
        sample_output = """Name                            Id                           Version
----------------------------------------------------------------------------------------------------------
Python 3.11.0                   Python.Python.3.11           3.11.0
Git                             Git.Git                      2.40.1
Visual Studio Code              Microsoft.VisualStudioCode   1.85.0
"""
        packages = adapter.parse_package_list(sample_output)
        # Should parse some packages
        assert len(packages) > 0


class TestLinuxAdapter:
    """Linux-specific adapter tests."""

    @pytest.fixture
    def adapter(self):
        """Get Linux adapter."""
        return get_os_adapter("linux")

    def test_linux_hosts_path(self, adapter):
        """Test Linux hosts file path."""
        path = adapter.get_hosts_file_path()
        # Compare parts to handle cross-platform Path behavior
        assert path.parts[-2:] == ("etc", "hosts")

    def test_linux_path_separator(self, adapter):
        """Test Linux path separator."""
        assert adapter.path_separator == "/"

    def test_linux_line_ending(self, adapter):
        """Test Linux line ending."""
        assert adapter.line_ending == "\n"

    def test_linux_package_manager_detection(self, adapter):
        """Test package manager detection."""
        pm = adapter.package_manager
        assert pm in ["apt", "dnf", "yum", "pacman", "zypper", "apk"]

    def test_parse_dpkg_output(self, adapter):
        """Test parsing dpkg output."""
        sample_output = """python3\t3.11.2\tPython interpreter
git\t2.39.2\tVersion control system
curl\t7.88.1\tCommand line tool for transferring data
"""
        packages = adapter.parse_package_list(sample_output)
        assert len(packages) == 3
        assert packages[0].name == "python3"
        assert packages[0].version == "3.11.2"


class TestMacOSAdapter:
    """macOS-specific adapter tests."""

    @pytest.fixture
    def adapter(self):
        """Get macOS adapter."""
        return get_os_adapter("macos")

    def test_macos_hosts_path(self, adapter):
        """Test macOS hosts file path."""
        path = adapter.get_hosts_file_path()
        # Compare parts to handle cross-platform Path behavior
        assert path.parts[-2:] == ("etc", "hosts")

    def test_macos_package_command(self, adapter):
        """Test macOS package list uses brew."""
        cmd = adapter.get_package_list_command()
        assert "brew" in cmd

    def test_macos_path_separator(self, adapter):
        """Test macOS path separator."""
        assert adapter.path_separator == "/"

    def test_macos_line_ending(self, adapter):
        """Test macOS line ending."""
        assert adapter.line_ending == "\n"

    def test_macos_default_shell(self, adapter):
        """Test macOS default shell (usually zsh)."""
        shell = adapter.default_shell
        # Should be zsh or bash
        assert "sh" in shell

    def test_parse_brew_output(self, adapter):
        """Test parsing brew list output."""
        sample_output = """python@3.11 3.11.7
git 2.43.0
node 21.5.0
"""
        packages = adapter.parse_package_list(sample_output)
        assert len(packages) == 3
        assert packages[0].name == "python@3.11"
        assert packages[0].version == "3.11.7"


class TestGetOSAdapterForRemote:
    """Tests for remote OS detection."""

    def test_detect_windows_from_uname(self):
        """Test detecting Windows from remote info."""
        adapter = get_os_adapter_for_remote("MINGW64_NT-10.0")
        assert adapter.name == "windows"

    def test_detect_linux_from_uname(self):
        """Test detecting Linux from remote info."""
        adapter = get_os_adapter_for_remote("Linux")
        assert adapter.name == "linux"

    def test_detect_macos_from_uname(self):
        """Test detecting macOS from remote info."""
        adapter = get_os_adapter_for_remote("Darwin")
        assert adapter.name == "macos"

    def test_default_to_linux(self):
        """Test that unknown OS defaults to Linux."""
        adapter = get_os_adapter_for_remote("FreeBSD")
        assert adapter.name == "linux"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

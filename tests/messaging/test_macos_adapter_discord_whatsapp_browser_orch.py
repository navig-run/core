"""
Batch 116: tests for
  navig/adapters/os/macos.py
  navig/messaging/adapters/discord_adapter.py
  navig/messaging/adapters/whatsapp_cloud.py
  navig/integrations/browser_orchestrator.py  (_daemon_base only)
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# navig/adapters/os/macos.py
# ---------------------------------------------------------------------------

from navig.adapters.os.macos import MacOSAdapter


@pytest.fixture
def adapter():
    return MacOSAdapter()


def test_macos_adapter_name(adapter):
    assert adapter.name == "macos"


def test_macos_adapter_display_name(adapter):
    assert adapter.display_name == "macOS"


def test_get_package_list_command(adapter):
    cmd = adapter.get_package_list_command()
    assert "brew" in cmd
    assert "list" in cmd


def test_parse_package_list_normal(adapter):
    output = "git 2.39.0\ncurl 7.88.1\n"
    pkgs = adapter.parse_package_list(output)
    assert len(pkgs) == 2
    assert pkgs[0].name == "git"
    assert pkgs[0].version == "2.39.0"
    assert pkgs[0].source == "brew"


def test_parse_package_list_no_version(adapter):
    output = "somepackage\n"
    pkgs = adapter.parse_package_list(output)
    assert len(pkgs) == 1
    assert pkgs[0].name == "somepackage"
    assert pkgs[0].version == "unknown"


def test_parse_package_list_empty(adapter):
    pkgs = adapter.parse_package_list("")
    assert pkgs == []


def test_get_package_install_command(adapter):
    cmd = adapter.get_package_install_command("wget")
    assert cmd == "brew install wget"


def test_get_package_remove_command(adapter):
    cmd = adapter.get_package_remove_command("curl")
    assert cmd == "brew uninstall curl"


def test_get_package_update_command(adapter):
    cmd = adapter.get_package_update_command()
    assert "brew update" in cmd


def test_get_hosts_file_path(adapter):
    assert adapter.get_hosts_file_path() == Path("/etc/hosts")


def test_get_temp_directory_env(adapter, monkeypatch):
    monkeypatch.setenv("TMPDIR", "/var/folders/tmp")
    assert adapter.get_temp_directory() == Path("/var/folders/tmp")


def test_get_temp_directory_default(adapter, monkeypatch):
    monkeypatch.delenv("TMPDIR", raising=False)
    assert adapter.get_temp_directory() == Path("/tmp")


def test_get_home_directory_env(adapter, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/testuser")
    assert adapter.get_home_directory() == Path("/Users/testuser")


def test_get_home_directory_default(adapter, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    assert adapter.get_home_directory() == Path("/Users/Shared")


def test_get_config_directory(adapter, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/alice")
    cfg_dir = adapter.get_config_directory()
    assert cfg_dir == Path("/Users/alice/.navig")


def test_get_system_info_command(adapter):
    cmd = adapter.get_system_info_command()
    assert "sw_vers" in cmd or "uname" in cmd


def test_parse_system_info(adapter):
    output = "ProductName:\tmacOS\nProductVersion:\t14.0\nArchitecture: ARM"
    info = adapter.parse_system_info(output)
    assert "ProductName" in info
    assert info["ProductName"] == "macOS"


def test_parse_system_info_kernel_line(adapter):
    """Lines without colon go under 'kernel'."""
    info = adapter.parse_system_info("Darwin kernel 23.0")
    assert "kernel" in info


def test_check_admin_privileges_non_root(adapter):
    """In test env, we're not root (usually returns False)."""
    import sys
    if sys.platform == "win32":
        pytest.skip("geteuid not available on Windows")
    result = adapter.check_admin_privileges()
    assert isinstance(result, bool)


def test_get_firewall_status_command(adapter):
    cmd = adapter.get_firewall_status_command()
    assert "socketfilterfw" in cmd


def test_get_open_ports_command(adapter):
    cmd = adapter.get_open_ports_command()
    assert "lsof" in cmd


def test_get_running_services_command(adapter):
    cmd = adapter.get_running_services_command()
    assert "launchctl" in cmd


def test_get_file_editor_command_default(adapter, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    cmd = adapter.get_file_editor_command(Path("/etc/hosts"))
    assert "open -e" in cmd
    # Path string may use OS-specific separators; just check path is present
    assert "hosts" in cmd


def test_get_file_editor_command_env(adapter, monkeypatch):
    monkeypatch.setenv("EDITOR", "vim")
    cmd = adapter.get_file_editor_command(Path("/etc/hosts"))
    assert cmd.startswith("vim")


def test_get_file_permissions_command(adapter):
    cmd = adapter.get_file_permissions_command(Path("/etc/hosts"))
    assert "ls -la" in cmd


def test_get_set_permissions_command(adapter):
    p = Path("/tmp/x")
    cmd = adapter.get_set_permissions_command(p, "755")
    assert "chmod 755" in cmd
    assert "x" in cmd


def test_get_process_list_command(adapter):
    assert adapter.get_process_list_command() == "ps aux"


def test_get_kill_process_command(adapter):
    assert adapter.get_kill_process_command(1234) == "kill -9 1234"


def test_get_network_interfaces_command(adapter):
    assert "ifconfig" in adapter.get_network_interfaces_command()


def test_get_dns_lookup_command(adapter):
    cmd = adapter.get_dns_lookup_command("example.com")
    assert "dig example.com" == cmd


def test_get_ping_command_default(adapter):
    cmd = adapter.get_ping_command("8.8.8.8")
    assert "ping -c 4 8.8.8.8" == cmd


def test_get_ping_command_custom_count(adapter):
    cmd = adapter.get_ping_command("1.1.1.1", count=2)
    assert "ping -c 2 1.1.1.1" == cmd


def test_default_shell(adapter, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert adapter.default_shell == "/bin/bash"


def test_default_shell_fallback(adapter, monkeypatch):
    monkeypatch.delenv("SHELL", raising=False)
    assert adapter.default_shell == "/bin/zsh"


def test_path_separator(adapter):
    assert adapter.path_separator == "/"


def test_line_ending(adapter):
    assert adapter.line_ending == "\n"


# ---------------------------------------------------------------------------
# navig/messaging/adapters/discord_adapter.py
# ---------------------------------------------------------------------------

from navig.messaging.adapters.discord_adapter import (
    DISCORD_AVAILABLE,
    DiscordMessagingAdapter,
)


def test_discord_adapter_name():
    a = DiscordMessagingAdapter()
    assert a.name == "discord"


def test_discord_adapter_capabilities():
    a = DiscordMessagingAdapter()
    assert "text" in a.capabilities
    assert "media" in a.capabilities


def test_discord_adapter_identity():
    a = DiscordMessagingAdapter()
    assert a.identity_mode == "bot"


def test_discord_adapter_compliance():
    a = DiscordMessagingAdapter()
    assert a.compliance == "official"


def test_discord_adapter_init_with_config():
    a = DiscordMessagingAdapter({"bot_token": "abc123"})
    assert a._bot_token == "abc123"


def test_discord_adapter_init_empty():
    a = DiscordMessagingAdapter()
    assert a._bot_token == ""
    assert a._client is None


def test_discord_available_is_bool():
    assert isinstance(DISCORD_AVAILABLE, bool)


# ---------------------------------------------------------------------------
# navig/messaging/adapters/whatsapp_cloud.py
# ---------------------------------------------------------------------------

from navig.messaging.adapters.whatsapp_cloud import WhatsAppCloudAdapter


def test_whatsapp_adapter_name():
    a = WhatsAppCloudAdapter()
    assert a.name == "whatsapp"


def test_whatsapp_adapter_init_defaults():
    a = WhatsAppCloudAdapter()
    assert a._phone_number_id == ""
    assert a._access_token == ""
    assert a._api_version == "v18.0"
    assert a._session is None


def test_whatsapp_adapter_init_with_config():
    cfg = {
        "phone_number_id": "999888777",
        "access_token": "tok123",
        "api_version": "v20.0",
    }
    a = WhatsAppCloudAdapter(cfg)
    assert a._phone_number_id == "999888777"
    assert a._access_token == "tok123"
    assert a._api_version == "v20.0"


def test_whatsapp_adapter_init_partial_config():
    a = WhatsAppCloudAdapter({"phone_number_id": "12345"})
    assert a._phone_number_id == "12345"
    assert a._access_token == ""


# ---------------------------------------------------------------------------
# navig/integrations/browser_orchestrator.py  (_daemon_base)
# ---------------------------------------------------------------------------

from navig.integrations.browser_orchestrator import _daemon_base


def test_daemon_base_default_port():
    mock_cm = MagicMock()
    mock_cm.get.return_value = 7421
    with patch("navig.config.get_config_manager", return_value=mock_cm):
        base = _daemon_base()
    assert base == "http://127.0.0.1:7421"


def test_daemon_base_custom_port():
    mock_cm = MagicMock()
    mock_cm.get.return_value = 9000
    with patch("navig.config.get_config_manager", return_value=mock_cm):
        base = _daemon_base()
    assert base == "http://127.0.0.1:9000"


def test_daemon_base_returns_string():
    mock_cm = MagicMock()
    mock_cm.get.return_value = 7421
    with patch("navig.config.get_config_manager", return_value=mock_cm):
        result = _daemon_base()
    assert isinstance(result, str)
    assert result.startswith("http://")

"""
Tests for navig.adapters.os.linux.LinuxAdapter — pure methods (no subprocesses).
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.adapters.os.linux import LinuxAdapter


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_name():
    assert LinuxAdapter().name == "linux"


def test_display_name_without_distro():
    assert LinuxAdapter().display_name == "Linux"


def test_display_name_with_distro():
    assert LinuxAdapter(distro="Ubuntu").display_name == "Linux (Ubuntu)"


def test_package_manager_uses_cached_value():
    adapter = LinuxAdapter()
    adapter._package_manager = "dnf"
    assert adapter.package_manager == "dnf"


def test_package_manager_defaults_to_apt_when_nothing_found():
    adapter = LinuxAdapter()
    adapter._package_manager = None
    with patch("os.path.exists", return_value=False):
        pm = adapter.package_manager
    assert pm == "apt"


def test_package_manager_detects_apt():
    adapter = LinuxAdapter()
    adapter._package_manager = None

    def fake_exists(path: str) -> bool:
        return path in {"/usr/bin/apt", "/bin/apt"}

    with patch("os.path.exists", side_effect=fake_exists):
        pm = adapter.package_manager
    assert pm == "apt"


# ---------------------------------------------------------------------------
# get_package_list_command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pm, expected_fragment",
    [
        ("apt", "dpkg-query"),
        ("dnf", "dnf list installed"),
        ("yum", "yum list installed"),
        ("pacman", "pacman -Q"),
        ("apk", "apk list --installed"),
    ],
)
def test_get_package_list_command(pm, expected_fragment):
    adapter = LinuxAdapter()
    adapter._package_manager = pm
    assert expected_fragment in adapter.get_package_list_command()


# ---------------------------------------------------------------------------
# parse_package_list
# ---------------------------------------------------------------------------


def test_parse_package_list_apt():
    adapter = LinuxAdapter()
    adapter._package_manager = "apt"
    output = "curl\t7.81.0\tA tool for transferring data\ngit\t2.34.1\tFast VCS"
    packages = adapter.parse_package_list(output)
    assert len(packages) == 2
    assert packages[0].name == "curl"
    assert packages[0].version == "7.81.0"
    assert packages[0].source == "apt"


def test_parse_package_list_apt_no_description():
    adapter = LinuxAdapter()
    adapter._package_manager = "apt"
    output = "bash\t5.1.16"
    packages = adapter.parse_package_list(output)
    assert len(packages) == 1
    assert packages[0].name == "bash"


def test_parse_package_list_dnf():
    adapter = LinuxAdapter()
    adapter._package_manager = "dnf"
    output = (
        "curl.x86_64  7.65.3-14.el8  @baseos\n"
        "git.x86_64   2.31.1-2.el8   @appstream\n"
    )
    packages = adapter.parse_package_list(output)
    assert len(packages) == 2
    assert packages[0].name == "curl"
    assert packages[0].source == "dnf"


def test_parse_package_list_pacman():
    adapter = LinuxAdapter()
    adapter._package_manager = "pacman"
    output = "curl 7.81.0-1\ngit 2.36.1-1\n"
    packages = adapter.parse_package_list(output)
    assert len(packages) == 2
    assert packages[0].name == "curl"
    assert packages[0].source == "pacman"


def test_parse_package_list_apk():
    adapter = LinuxAdapter()
    adapter._package_manager = "apk"
    output = "curl-7.81.0-r0 {curl} (MIT) [installed]\nbash-5.1.16-r2 {bash} (GPL) [installed]\n"
    packages = adapter.parse_package_list(output)
    assert len(packages) == 2
    assert packages[0].name == "curl"
    assert packages[0].version.startswith("7.81.0")


def test_parse_package_list_empty_input():
    adapter = LinuxAdapter()
    adapter._package_manager = "apt"
    packages = adapter.parse_package_list("")
    assert packages == []


def test_parse_package_list_yum():
    adapter = LinuxAdapter()
    adapter._package_manager = "yum"
    output = "python3.x86_64  3.9.7-2.el8  @baseos\n"
    packages = adapter.parse_package_list(output)
    assert len(packages) == 1
    assert packages[0].name == "python3"


# ---------------------------------------------------------------------------
# Package command generators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pm, pkg, expected",
    [
        ("apt", "nginx", "apt-get install -y nginx"),
        ("dnf", "nginx", "dnf install -y nginx"),
        ("pacman", "nginx", "pacman -S --noconfirm nginx"),
        ("apk", "nginx", "apk add nginx"),
    ],
)
def test_get_package_install_command(pm, pkg, expected):
    adapter = LinuxAdapter()
    adapter._package_manager = pm
    assert adapter.get_package_install_command(pkg) == expected


@pytest.mark.parametrize(
    "pm, pkg, expected",
    [
        ("apt", "nginx", "apt-get remove -y nginx"),
        ("dnf", "nginx", "dnf remove -y nginx"),
        ("pacman", "nginx", "pacman -R --noconfirm nginx"),
    ],
)
def test_get_package_remove_command(pm, pkg, expected):
    adapter = LinuxAdapter()
    adapter._package_manager = pm
    assert adapter.get_package_remove_command(pkg) == expected


@pytest.mark.parametrize(
    "pm, expected",
    [
        ("apt", "apt-get update"),
        ("dnf", "dnf upgrade -y"),
        ("pacman", "pacman -Syu --noconfirm"),
    ],
)
def test_get_package_update_command(pm, expected):
    adapter = LinuxAdapter()
    adapter._package_manager = pm
    assert expected in adapter.get_package_update_command()


# ---------------------------------------------------------------------------
# System paths
# ---------------------------------------------------------------------------


def test_get_hosts_file_path():
    assert LinuxAdapter().get_hosts_file_path() == Path("/etc/hosts")


def test_get_temp_directory_default():
    env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
    with patch.dict("os.environ", env, clear=True):
        result = LinuxAdapter().get_temp_directory()
    assert result == Path("/tmp")


def test_get_temp_directory_from_env():
    with patch.dict("os.environ", {"TMPDIR": "/custom/tmp"}):
        result = LinuxAdapter().get_temp_directory()
    assert result == Path("/custom/tmp")


def test_get_home_directory_from_env():
    with patch.dict("os.environ", {"HOME": "/home/navig"}):
        result = LinuxAdapter().get_home_directory()
    assert result == Path("/home/navig")


def test_get_config_directory_xdg():
    with patch.dict("os.environ", {"XDG_CONFIG_HOME": "/custom/config", "HOME": "/home/x"}):
        result = LinuxAdapter().get_config_directory()
    assert result == Path("/custom/config/navig")


def test_get_config_directory_fallback_to_home():
    env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
    env["HOME"] = "/home/tester"
    with patch.dict("os.environ", env, clear=True):
        result = LinuxAdapter().get_config_directory()
    assert result == Path("/home/tester/.navig")


# ---------------------------------------------------------------------------
# System info parsing
# ---------------------------------------------------------------------------


def test_parse_system_info_kernel():
    adapter = LinuxAdapter()
    output = "Linux hostname 5.15.0 #1 SMP Wed Jan 1 00:00:00 UTC 2025\nNAME=Ubuntu\nVERSION=22.04"
    info = adapter.parse_system_info(output)
    assert "Linux hostname" in info["kernel"]
    assert info.get("NAME") == "Ubuntu"
    assert info.get("VERSION") == "22.04"


def test_parse_system_info_strips_quotes():
    adapter = LinuxAdapter()
    output = "Linux 5.15\nPRETTY_NAME=\"Ubuntu 22.04 LTS\""
    info = adapter.parse_system_info(output)
    assert info["PRETTY_NAME"] == "Ubuntu 22.04 LTS"


def test_parse_system_info_empty():
    adapter = LinuxAdapter()
    info = adapter.parse_system_info("")
    assert isinstance(info, dict)


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def test_path_separator():
    assert LinuxAdapter().path_separator == "/"


def test_line_ending():
    assert LinuxAdapter().line_ending == "\n"


def test_default_shell_from_env():
    with patch.dict("os.environ", {"SHELL": "/bin/zsh"}):
        assert LinuxAdapter().default_shell == "/bin/zsh"


def test_get_ping_command():
    cmd = LinuxAdapter().get_ping_command("8.8.8.8", count=3)
    assert "ping" in cmd
    assert "8.8.8.8" in cmd
    assert "3" in cmd


def test_get_kill_process_command():
    cmd = LinuxAdapter().get_kill_process_command(1234)
    assert "1234" in cmd


def test_get_file_editor_command():
    with patch.dict("os.environ", {"EDITOR": "vim"}):
        cmd = LinuxAdapter().get_file_editor_command(Path("/etc/nginx/nginx.conf"))
    assert "vim" in cmd
    assert "nginx.conf" in cmd

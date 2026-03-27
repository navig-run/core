# NAVIG Linux OS Adapter
"""
Linux-specific implementation of OS operations.
Supports Debian/Ubuntu (apt) and RHEL/CentOS (yum/dnf).
"""

import os
import re
from pathlib import Path
from typing import Any

from navig.adapters.os.base import OSAdapter, PackageInfo


class LinuxAdapter(OSAdapter):
    """
    Linux OS adapter.

    Automatically detects and uses:
    - apt/dpkg for Debian/Ubuntu
    - dnf/yum for RHEL/CentOS/Fedora
    - pacman for Arch Linux
    """

    def __init__(self, distro: str | None = None):
        """
        Initialize Linux adapter.

        Args:
            distro: Linux distribution name (auto-detected if not provided)
        """
        self._distro = distro
        self._package_manager: str | None = None

    @property
    def name(self) -> str:
        return "linux"

    @property
    def display_name(self) -> str:
        if self._distro:
            return f"Linux ({self._distro})"
        return "Linux"

    @property
    def package_manager(self) -> str:
        """Detect and cache the package manager."""
        if self._package_manager is None:
            # Check for common package managers
            for pm in ["apt", "dnf", "yum", "pacman", "zypper", "apk"]:
                if os.path.exists(f"/usr/bin/{pm}") or os.path.exists(f"/bin/{pm}"):
                    self._package_manager = pm
                    break
            else:
                self._package_manager = "apt"  # Default fallback
        return self._package_manager

    # ==================== Package Management ====================

    def get_package_list_command(self) -> str:
        """List installed packages based on detected package manager."""
        commands = {
            "apt": "dpkg-query -W -f='${Package}\t${Version}\t${Description}\n'",
            "dnf": "dnf list installed --quiet",
            "yum": "yum list installed --quiet",
            "pacman": "pacman -Q",
            "zypper": "zypper packages --installed-only",
            "apk": "apk list --installed",
        }
        return commands.get(self.package_manager, commands["apt"])

    def parse_package_list(self, output: str) -> list[PackageInfo]:
        """Parse package list output based on package manager format."""
        packages = []

        if self.package_manager in ["apt", "dpkg"]:
            # dpkg-query format: name\tversion\tdescription
            for line in output.strip().split("\n"):
                parts = line.split("\t")
                if len(parts) >= 2:
                    packages.append(
                        PackageInfo(
                            name=parts[0],
                            version=parts[1],
                            description=parts[2] if len(parts) > 2 else None,
                            source="apt",
                        )
                    )

        elif self.package_manager in ["dnf", "yum"]:
            # Format: package-name.arch  version  repo
            for line in output.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2:
                    name = parts[0].rsplit(".", 1)[0]  # Remove arch suffix
                    packages.append(
                        PackageInfo(
                            name=name, version=parts[1], source=self.package_manager
                        )
                    )

        elif self.package_manager == "pacman":
            # Format: package-name version
            for line in output.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2:
                    packages.append(
                        PackageInfo(name=parts[0], version=parts[1], source="pacman")
                    )

        elif self.package_manager == "apk":
            # Format: package-name-version - description
            for line in output.strip().split("\n"):
                match = re.match(r"^(\S+)-(\d[\d\.\-_a-zA-Z]*)\s", line)
                if match:
                    packages.append(
                        PackageInfo(
                            name=match.group(1), version=match.group(2), source="apk"
                        )
                    )

        return packages

    def get_package_install_command(self, package: str) -> str:
        commands = {
            "apt": f"apt-get install -y {package}",
            "dnf": f"dnf install -y {package}",
            "yum": f"yum install -y {package}",
            "pacman": f"pacman -S --noconfirm {package}",
            "zypper": f"zypper install -y {package}",
            "apk": f"apk add {package}",
        }
        return commands.get(self.package_manager, commands["apt"])

    def get_package_remove_command(self, package: str) -> str:
        commands = {
            "apt": f"apt-get remove -y {package}",
            "dnf": f"dnf remove -y {package}",
            "yum": f"yum remove -y {package}",
            "pacman": f"pacman -R --noconfirm {package}",
            "zypper": f"zypper remove -y {package}",
            "apk": f"apk del {package}",
        }
        return commands.get(self.package_manager, commands["apt"])

    def get_package_update_command(self) -> str:
        commands = {
            "apt": "apt-get update && apt-get upgrade -y",
            "dnf": "dnf upgrade -y",
            "yum": "yum update -y",
            "pacman": "pacman -Syu --noconfirm",
            "zypper": "zypper update -y",
            "apk": "apk upgrade",
        }
        return commands.get(self.package_manager, commands["apt"])

    # ==================== System Paths ====================

    def get_hosts_file_path(self) -> Path:
        return Path("/etc/hosts")

    def get_temp_directory(self) -> Path:
        return Path(os.environ.get("TMPDIR", "/tmp"))

    def get_home_directory(self) -> Path:
        return Path(os.environ.get("HOME", "/root"))

    def get_config_directory(self) -> Path:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "navig"
        return self.get_home_directory() / ".navig"

    # ==================== System Information ====================

    def get_system_info_command(self) -> str:
        return "uname -a && cat /etc/os-release 2>/dev/null || cat /etc/*-release 2>/dev/null"

    def parse_system_info(self, output: str) -> dict[str, Any]:
        """Parse Linux system info output."""
        info = {}

        # Parse uname
        lines = output.strip().split("\n")
        if lines:
            info["kernel"] = lines[0]

        # Parse os-release style
        for line in lines[1:]:
            if "=" in line:
                key, _, value = line.partition("=")
                info[key.strip()] = value.strip().strip('"')

        return info

    def get_resource_usage_command(self) -> str:
        return """echo "=== CPU ===" && top -bn1 | head -5 && echo "=== MEMORY ===" && free -h && echo "=== DISK ===" && df -h"""

    # ==================== Security ====================

    def check_admin_privileges(self) -> bool:
        """Check if running as root on Linux."""
        return os.geteuid() == 0

    def get_firewall_status_command(self) -> str:
        # Try multiple firewall tools
        return """command -v ufw >/dev/null && ufw status || command -v firewall-cmd >/dev/null && firewall-cmd --state || iptables -L -n 2>/dev/null | head -20"""

    def get_open_ports_command(self) -> str:
        return "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null"

    def get_running_services_command(self) -> str:
        return "systemctl list-units --type=service --state=running --no-pager 2>/dev/null || service --status-all 2>/dev/null"

    # ==================== File Operations ====================

    def get_file_editor_command(self, file_path: Path) -> str:
        """Open file in nano or vim."""
        editor = os.environ.get("EDITOR", "nano")
        return f'{editor} "{file_path}"'

    def get_file_permissions_command(self, file_path: Path) -> str:
        return f'ls -la "{file_path}" && stat "{file_path}"'

    def get_set_permissions_command(self, file_path: Path, permissions: str) -> str:
        return f'chmod {permissions} "{file_path}"'

    # ==================== Process Management ====================

    def get_process_list_command(self) -> str:
        return "ps aux"

    def get_kill_process_command(self, pid: int) -> str:
        return f"kill -9 {pid}"

    # ==================== Network ====================

    def get_network_interfaces_command(self) -> str:
        return "ip addr show 2>/dev/null || ifconfig -a"

    def get_dns_lookup_command(self, hostname: str) -> str:
        return f"dig {hostname} 2>/dev/null || nslookup {hostname} 2>/dev/null || host {hostname}"

    def get_ping_command(self, host: str, count: int = 4) -> str:
        return f"ping -c {count} {host}"

    # ==================== Shell ====================

    @property
    def default_shell(self) -> str:
        return os.environ.get("SHELL", "/bin/bash")

    @property
    def path_separator(self) -> str:
        return "/"

    @property
    def line_ending(self) -> str:
        return "\n"

# NAVIG macOS Adapter
"""
macOS-specific implementation of OS operations.
Uses Homebrew for package management.
"""

import os
from pathlib import Path
from typing import List, Dict, Any

from navig.adapters.os.base import OSAdapter, PackageInfo


class MacOSAdapter(OSAdapter):
    """
    macOS (Darwin) OS adapter.
    
    Uses:
    - Homebrew for package management
    - launchctl for services
    - pfctl for firewall
    """
    
    @property
    def name(self) -> str:
        return "macos"
    
    @property
    def display_name(self) -> str:
        return "macOS"
    
    # ==================== Package Management ====================
    
    def get_package_list_command(self) -> str:
        """List installed packages using Homebrew."""
        return "brew list --versions"
    
    def parse_package_list(self, output: str) -> List[PackageInfo]:
        """Parse brew list output."""
        packages = []
        for line in output.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                packages.append(PackageInfo(
                    name=parts[0],
                    version=parts[1],
                    source='brew'
                ))
            elif len(parts) == 1:
                packages.append(PackageInfo(
                    name=parts[0],
                    version='unknown',
                    source='brew'
                ))
        return packages
    
    def get_package_install_command(self, package: str) -> str:
        return f'brew install {package}'
    
    def get_package_remove_command(self, package: str) -> str:
        return f'brew uninstall {package}'
    
    def get_package_update_command(self) -> str:
        return 'brew update && brew upgrade'
    
    # ==================== System Paths ====================
    
    def get_hosts_file_path(self) -> Path:
        return Path("/etc/hosts")
    
    def get_temp_directory(self) -> Path:
        return Path(os.environ.get('TMPDIR', '/tmp'))
    
    def get_home_directory(self) -> Path:
        return Path(os.environ.get('HOME', '/Users/Shared'))
    
    def get_config_directory(self) -> Path:
        return self.get_home_directory() / '.navig'
    
    # ==================== System Information ====================
    
    def get_system_info_command(self) -> str:
        return "sw_vers && uname -a"
    
    def parse_system_info(self, output: str) -> Dict[str, Any]:
        """Parse macOS system info output."""
        info = {}
        for line in output.strip().split('\n'):
            if ':' in line:
                key, _, value = line.partition(':')
                info[key.strip()] = value.strip()
            elif line.strip():
                info['kernel'] = line.strip()
        return info
    
    def get_resource_usage_command(self) -> str:
        return '''echo "=== CPU ===" && top -l 1 | head -10 && echo "=== MEMORY ===" && vm_stat && echo "=== DISK ===" && df -h'''
    
    # ==================== Security ====================
    
    def check_admin_privileges(self) -> bool:
        """Check if running as root on macOS."""
        return os.geteuid() == 0
    
    def get_firewall_status_command(self) -> str:
        return "/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate"
    
    def get_open_ports_command(self) -> str:
        return "lsof -iTCP -sTCP:LISTEN -n -P"
    
    def get_running_services_command(self) -> str:
        return "launchctl list | head -50"
    
    # ==================== File Operations ====================
    
    def get_file_editor_command(self, file_path: Path) -> str:
        """Open file in default editor or TextEdit."""
        editor = os.environ.get('EDITOR', 'open -e')
        if editor == 'open -e':
            return f'open -e "{file_path}"'
        return f'{editor} "{file_path}"'
    
    def get_file_permissions_command(self, file_path: Path) -> str:
        return f'ls -la "{file_path}" && stat -f "%Sp %OLp" "{file_path}"'
    
    def get_set_permissions_command(self, file_path: Path, permissions: str) -> str:
        return f'chmod {permissions} "{file_path}"'
    
    # ==================== Process Management ====================
    
    def get_process_list_command(self) -> str:
        return 'ps aux'
    
    def get_kill_process_command(self, pid: int) -> str:
        return f'kill -9 {pid}'
    
    # ==================== Network ====================
    
    def get_network_interfaces_command(self) -> str:
        return 'ifconfig -a'
    
    def get_dns_lookup_command(self, hostname: str) -> str:
        return f'dig {hostname}'
    
    def get_ping_command(self, host: str, count: int = 4) -> str:
        return f'ping -c {count} {host}'
    
    # ==================== Shell ====================
    
    @property
    def default_shell(self) -> str:
        return os.environ.get('SHELL', '/bin/zsh')
    
    @property
    def path_separator(self) -> str:
        return "/"
    
    @property
    def line_ending(self) -> str:
        return "\n"

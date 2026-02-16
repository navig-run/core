# NAVIG OS Adapter Base Class
"""
Abstract base class for OS-specific operations.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass


@dataclass
class PackageInfo:
    """Information about an installed package."""
    name: str
    version: str
    description: Optional[str] = None
    source: Optional[str] = None  # e.g., 'winget', 'apt', 'brew'
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'version': self.version,
            'description': self.description,
            'source': self.source
        }


@dataclass
class SecurityCheck:
    """Result of a security check."""
    category: str  # e.g., 'firewall', 'updates', 'users'
    status: str  # 'ok', 'warning', 'critical'
    message: str
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'category': self.category,
            'status': self.status,
            'message': self.message,
            'details': self.details
        }


class OSAdapter(ABC):
    """
    Abstract base class for OS-specific operations.
    
    Each OS implementation provides platform-specific commands and paths.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """OS name (e.g., 'windows', 'linux', 'macos')."""
        pass
    
    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable OS name (e.g., 'Windows', 'Linux', 'macOS')."""
        pass
    
    # ==================== Package Management ====================
    
    @abstractmethod
    def get_package_list_command(self) -> str:
        """
        Get the command to list installed packages.
        
        Returns:
            Command string to execute
        """
        pass
    
    @abstractmethod
    def parse_package_list(self, output: str) -> List[PackageInfo]:
        """
        Parse the output of the package list command.
        
        Args:
            output: Raw command output
            
        Returns:
            List of PackageInfo objects
        """
        pass
    
    @abstractmethod
    def get_package_install_command(self, package: str) -> str:
        """Get command to install a package."""
        pass
    
    @abstractmethod
    def get_package_remove_command(self, package: str) -> str:
        """Get command to remove a package."""
        pass
    
    @abstractmethod
    def get_package_update_command(self) -> str:
        """Get command to update all packages."""
        pass
    
    # ==================== System Paths ====================
    
    @abstractmethod
    def get_hosts_file_path(self) -> Path:
        """Get the path to the hosts file."""
        pass
    
    @abstractmethod
    def get_temp_directory(self) -> Path:
        """Get the system temp directory."""
        pass
    
    @abstractmethod
    def get_home_directory(self) -> Path:
        """Get the user's home directory."""
        pass
    
    @abstractmethod
    def get_config_directory(self) -> Path:
        """Get the OS-appropriate config directory for NAVIG."""
        pass
    
    # ==================== System Information ====================
    
    @abstractmethod
    def get_system_info_command(self) -> str:
        """Get command to retrieve system information."""
        pass
    
    @abstractmethod
    def parse_system_info(self, output: str) -> Dict[str, Any]:
        """Parse system information output."""
        pass
    
    @abstractmethod
    def get_resource_usage_command(self) -> str:
        """Get command to check CPU, memory, disk usage."""
        pass
    
    # ==================== Security ====================
    
    @abstractmethod
    def check_admin_privileges(self) -> bool:
        """Check if running with admin/root privileges."""
        pass
    
    @abstractmethod
    def get_firewall_status_command(self) -> str:
        """Get command to check firewall status."""
        pass
    
    @abstractmethod
    def get_open_ports_command(self) -> str:
        """Get command to list open ports."""
        pass
    
    @abstractmethod
    def get_running_services_command(self) -> str:
        """Get command to list running services."""
        pass
    
    # ==================== File Operations ====================
    
    @abstractmethod
    def get_file_editor_command(self, file_path: Path) -> str:
        """Get command to open file in the default editor."""
        pass
    
    @abstractmethod
    def get_file_permissions_command(self, file_path: Path) -> str:
        """Get command to check file permissions."""
        pass
    
    @abstractmethod
    def get_set_permissions_command(self, file_path: Path, permissions: str) -> str:
        """Get command to set file permissions."""
        pass
    
    # ==================== Process Management ====================
    
    @abstractmethod
    def get_process_list_command(self) -> str:
        """Get command to list running processes."""
        pass
    
    @abstractmethod
    def get_kill_process_command(self, pid: int) -> str:
        """Get command to kill a process by PID."""
        pass
    
    # ==================== Network ====================
    
    @abstractmethod
    def get_network_interfaces_command(self) -> str:
        """Get command to list network interfaces."""
        pass
    
    @abstractmethod
    def get_dns_lookup_command(self, hostname: str) -> str:
        """Get command for DNS lookup."""
        pass
    
    @abstractmethod
    def get_ping_command(self, host: str, count: int = 4) -> str:
        """Get command to ping a host."""
        pass
    
    # ==================== Shell ====================
    
    @property
    @abstractmethod
    def default_shell(self) -> str:
        """Get the default shell path/name."""
        pass
    
    @property
    @abstractmethod
    def path_separator(self) -> str:
        """Get the path separator for this OS."""
        pass
    
    @property
    @abstractmethod
    def line_ending(self) -> str:
        """Get the line ending for this OS."""
        pass

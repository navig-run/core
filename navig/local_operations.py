# NAVIG Local Operations Module
"""
Unified local machine operations using ConnectionAdapter and OSAdapter.

This module provides a high-level interface for local machine management,
abstracting the underlying OS differences.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from navig.adapters.os import OSAdapter, get_os_adapter
from navig.adapters.os.base import PackageInfo, SecurityCheck
from navig.core.connection import CommandResult, LocalConnection


@dataclass
class LocalSystemInfo:
    """System information for the local machine."""
    hostname: str
    os_name: str
    os_display_name: str
    is_admin: bool
    home_directory: Path
    config_directory: Path

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hostname': self.hostname,
            'os_name': self.os_name,
            'os_display_name': self.os_display_name,
            'is_admin': self.is_admin,
            'home_directory': str(self.home_directory),
            'config_directory': str(self.config_directory)
        }


class LocalOperations:
    """
    High-level interface for local machine operations.
    
    Combines ConnectionAdapter (for command execution) with
    OSAdapter (for OS-specific commands) to provide a unified API.
    """

    def __init__(self, working_directory: Optional[Path] = None):
        """
        Initialize LocalOperations.
        
        Args:
            working_directory: Optional working directory for command execution
        """
        self._connection: Optional[LocalConnection] = None
        self._os_adapter: Optional[OSAdapter] = None
        self._working_directory = working_directory

    @property
    def connection(self) -> LocalConnection:
        """Lazy-load connection adapter."""
        if self._connection is None:
            self._connection = LocalConnection(
                working_directory=self._working_directory
            )
        return self._connection

    @property
    def os_adapter(self) -> OSAdapter:
        """Lazy-load OS adapter."""
        if self._os_adapter is None:
            self._os_adapter = get_os_adapter()
        return self._os_adapter

    # ==================== System Information ====================

    def get_system_info(self) -> LocalSystemInfo:
        """Get comprehensive local system information."""
        import socket

        try:
            hostname = socket.gethostname()
        except Exception:
            hostname = 'localhost'

        return LocalSystemInfo(
            hostname=hostname,
            os_name=self.os_adapter.name,
            os_display_name=self.os_adapter.display_name,
            is_admin=self.os_adapter.check_admin_privileges(),
            home_directory=self.os_adapter.get_home_directory(),
            config_directory=self.os_adapter.get_config_directory()
        )

    def get_resource_usage(self) -> CommandResult:
        """Get CPU, memory, and disk usage."""
        cmd = self.os_adapter.get_resource_usage_command()
        return self.connection.run(cmd)

    # ==================== Package Management ====================

    def list_packages(self) -> List[PackageInfo]:
        """
        List all installed packages.
        
        Returns:
            List of PackageInfo objects
        """
        cmd = self.os_adapter.get_package_list_command()
        result = self.connection.run(cmd, timeout=60.0)

        if result.exit_code != 0:
            # Return empty list on failure
            return []

        return self.os_adapter.parse_package_list(result.stdout)

    def install_package(self, package: str) -> CommandResult:
        """
        Install a package.
        
        Args:
            package: Package name or ID
            
        Returns:
            Command result
        """
        if not self.os_adapter.check_admin_privileges():
            return CommandResult(
                stdout='',
                stderr='Administrator/root privileges required for package installation',
                exit_code=1,
                duration=0.0
            )

        cmd = self.os_adapter.get_package_install_command(package)
        return self.connection.run(cmd, timeout=300.0)

    def remove_package(self, package: str) -> CommandResult:
        """Remove a package."""
        if not self.os_adapter.check_admin_privileges():
            return CommandResult(
                stdout='',
                stderr='Administrator/root privileges required for package removal',
                exit_code=1,
                duration=0.0
            )

        cmd = self.os_adapter.get_package_remove_command(package)
        return self.connection.run(cmd, timeout=120.0)

    def update_packages(self) -> CommandResult:
        """Update all packages."""
        if not self.os_adapter.check_admin_privileges():
            return CommandResult(
                stdout='',
                stderr='Administrator/root privileges required for package updates',
                exit_code=1,
                duration=0.0
            )

        cmd = self.os_adapter.get_package_update_command()
        return self.connection.run(cmd, timeout=600.0)

    # ==================== Hosts File ====================

    def get_hosts_file_path(self) -> Path:
        """Get the path to the system hosts file."""
        return self.os_adapter.get_hosts_file_path()

    def read_hosts_file(self) -> str:
        """
        Read the contents of the hosts file.
        
        Returns:
            Contents of the hosts file
        """
        hosts_path = self.get_hosts_file_path()
        try:
            return hosts_path.read_text()
        except PermissionError:
            return f"Permission denied reading {hosts_path}"
        except FileNotFoundError:
            return f"Hosts file not found at {hosts_path}"

    def can_edit_hosts_file(self) -> bool:
        """Check if we can edit the hosts file (admin required)."""
        return self.os_adapter.check_admin_privileges()

    def open_hosts_in_editor(self) -> CommandResult:
        """
        Open the hosts file in the default editor.
        
        Note: May require admin privileges.
        """
        hosts_path = self.get_hosts_file_path()
        cmd = self.os_adapter.get_file_editor_command(hosts_path)
        return self.connection.run(cmd, timeout=5.0)

    # ==================== Security ====================

    def get_firewall_status(self) -> CommandResult:
        """Get firewall status."""
        cmd = self.os_adapter.get_firewall_status_command()
        return self.connection.run(cmd)

    def get_open_ports(self) -> CommandResult:
        """List open/listening ports."""
        cmd = self.os_adapter.get_open_ports_command()
        return self.connection.run(cmd)

    def get_running_services(self) -> CommandResult:
        """List running services."""
        cmd = self.os_adapter.get_running_services_command()
        return self.connection.run(cmd)

    def run_security_audit(self) -> List[SecurityCheck]:
        """
        Run a basic security audit.
        
        Returns:
            List of SecurityCheck results
        """
        checks = []

        # Check admin privileges
        is_admin = self.os_adapter.check_admin_privileges()
        checks.append(SecurityCheck(
            category='privileges',
            status='warning' if is_admin else 'ok',
            message='Running with admin privileges' if is_admin else 'Running as normal user',
            details={'is_admin': is_admin}
        ))

        # Check firewall
        firewall_result = self.get_firewall_status()
        if firewall_result.exit_code == 0:
            # Try to detect if firewall is enabled
            output_lower = firewall_result.stdout.lower()
            if 'off' in output_lower or 'disabled' in output_lower or 'inactive' in output_lower:
                checks.append(SecurityCheck(
                    category='firewall',
                    status='warning',
                    message='Firewall appears to be disabled',
                    details={'output': firewall_result.stdout[:500]}
                ))
            else:
                checks.append(SecurityCheck(
                    category='firewall',
                    status='ok',
                    message='Firewall is active',
                    details={'output': firewall_result.stdout[:500]}
                ))
        else:
            checks.append(SecurityCheck(
                category='firewall',
                status='warning',
                message='Could not determine firewall status',
                details={'error': firewall_result.stderr}
            ))

        # Check open ports
        ports_result = self.get_open_ports()
        if ports_result.exit_code == 0:
            port_count = len([l for l in ports_result.stdout.split('\n') if l.strip()])
            checks.append(SecurityCheck(
                category='ports',
                status='ok' if port_count < 20 else 'warning',
                message=f'{port_count} listening ports detected',
                details={'port_count': port_count}
            ))

        return checks

    # ==================== Network ====================

    def get_network_interfaces(self) -> CommandResult:
        """List network interfaces."""
        cmd = self.os_adapter.get_network_interfaces_command()
        return self.connection.run(cmd)

    def ping(self, host: str, count: int = 4) -> CommandResult:
        """Ping a host."""
        cmd = self.os_adapter.get_ping_command(host, count)
        return self.connection.run(cmd, timeout=30.0)

    def dns_lookup(self, hostname: str) -> CommandResult:
        """Perform DNS lookup."""
        cmd = self.os_adapter.get_dns_lookup_command(hostname)
        return self.connection.run(cmd)

    # ==================== Processes ====================

    def list_processes(self) -> CommandResult:
        """List running processes."""
        cmd = self.os_adapter.get_process_list_command()
        return self.connection.run(cmd)

    def kill_process(self, pid: int) -> CommandResult:
        """Kill a process by PID."""
        cmd = self.os_adapter.get_kill_process_command(pid)
        return self.connection.run(cmd)

    # ==================== Raw Command Execution ====================

    def run_command(self, command: str, timeout: Optional[float] = None) -> CommandResult:
        """
        Execute a raw command on the local machine.
        
        Args:
            command: Command to execute
            timeout: Optional timeout in seconds
            
        Returns:
            CommandResult with stdout, stderr, exit_code, duration
        """
        return self.connection.run(command, timeout=timeout)

    def close(self):
        """Close the connection (cleanup)."""
        if self._connection:
            self._connection.close()
            self._connection = None


# Convenience function
def get_local_ops(working_directory: Optional[Path] = None) -> LocalOperations:
    """
    Get a LocalOperations instance.
    
    Args:
        working_directory: Optional working directory
        
    Returns:
        LocalOperations instance
    """
    return LocalOperations(working_directory=working_directory)

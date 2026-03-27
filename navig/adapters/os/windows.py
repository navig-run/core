# NAVIG Windows OS Adapter
"""
Windows-specific implementation of OS operations.
Uses PowerShell and winget for package management.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from navig.adapters.os.base import OSAdapter, PackageInfo


class WindowsAdapter(OSAdapter):
    """
    Windows OS adapter.

    Uses:
    - winget for package management
    - PowerShell for system commands
    - netstat for network operations
    """

    @property
    def name(self) -> str:
        return "windows"

    @property
    def display_name(self) -> str:
        return "Windows"

    # ==================== Package Management ====================

    def get_package_list_command(self) -> str:
        """List installed packages using winget."""
        return "winget list --disable-interactivity"

    def parse_package_list(self, output: str) -> List[PackageInfo]:
        """Parse winget list output."""
        packages = []
        lines = output.strip().split("\n")

        # Skip header lines (typically first 2-3 lines)
        data_started = False
        for line in lines:
            # Look for separator line (----) to find where data starts
            if "---" in line:
                data_started = True
                continue

            if not data_started or not line.strip():
                continue

            # winget output is column-based, parse by position
            # Format: Name  Id  Version  Available  Source
            parts = line.split()
            if len(parts) >= 3:
                # Try to extract name (may contain spaces)
                # Find version pattern (x.x.x or similar)
                version_pattern = r"\d+[\.\d]*"
                version_match = None
                version_idx = -1

                for i, part in enumerate(parts):
                    if re.match(version_pattern, part):
                        version_match = part
                        version_idx = i
                        break

                if version_match and version_idx > 0:
                    name = " ".join(parts[:version_idx])
                    packages.append(
                        PackageInfo(name=name, version=version_match, source="winget")
                    )
                elif len(parts) >= 2:
                    packages.append(
                        PackageInfo(
                            name=parts[0],
                            version=parts[-1] if len(parts) > 1 else "unknown",
                            source="winget",
                        )
                    )

        return packages

    def get_package_install_command(self, package: str) -> str:
        return f"winget install --id {package} --accept-source-agreements --accept-package-agreements"

    def get_package_remove_command(self, package: str) -> str:
        return f"winget uninstall --id {package}"

    def get_package_update_command(self) -> str:
        return "winget upgrade --all --accept-source-agreements --accept-package-agreements"

    # ==================== System Paths ====================

    def get_hosts_file_path(self) -> Path:
        return Path(r"C:\Windows\System32\drivers\etc\hosts")

    def get_temp_directory(self) -> Path:
        return Path(os.environ.get("TEMP", r"C:\Windows\Temp"))

    def get_home_directory(self) -> Path:
        return Path(os.environ.get("USERPROFILE", r"C:\Users\Default"))

    def get_config_directory(self) -> Path:
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "navig"
        return self.get_home_directory() / ".navig"

    # ==================== System Information ====================

    def get_system_info_command(self) -> str:
        return "systeminfo"

    def parse_system_info(self, output: str) -> Dict[str, Any]:
        """Parse Windows systeminfo output."""
        info = {}
        for line in output.strip().split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                info[key.strip()] = value.strip()
        return info

    def get_resource_usage_command(self) -> str:
        # PowerShell command to get CPU, memory, disk
        return '''powershell -Command "
            $cpu = (Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples.CookedValue;
            $mem = Get-CimInstance Win32_OperatingSystem;
            $disk = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3';
            Write-Output ('CPU: ' + [math]::Round($cpu, 2) + '%');
            Write-Output ('Memory Total: ' + [math]::Round($mem.TotalVisibleMemorySize/1MB, 2) + ' GB');
            Write-Output ('Memory Free: ' + [math]::Round($mem.FreePhysicalMemory/1MB, 2) + ' GB');
            foreach ($d in $disk) {
                Write-Output ('Disk ' + $d.DeviceID + ' Free: ' + [math]::Round($d.FreeSpace/1GB, 2) + ' GB / ' + [math]::Round($d.Size/1GB, 2) + ' GB')
            }
        "'''

    # ==================== Security ====================

    def check_admin_privileges(self) -> bool:
        """Check if running as Administrator on Windows."""
        try:
            import ctypes

            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    def get_firewall_status_command(self) -> str:
        return "netsh advfirewall show allprofiles state"

    def get_open_ports_command(self) -> str:
        return "netstat -an | findstr LISTENING"

    def get_running_services_command(self) -> str:
        return "powershell -Command \"Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object Name, DisplayName, Status | Format-Table -AutoSize\""

    # ==================== File Operations ====================

    def get_file_editor_command(self, file_path: Path) -> str:
        """Open file in notepad (or VS Code if available)."""
        # Check for VS Code
        return f'notepad "{file_path}"'

    def get_file_permissions_command(self, file_path: Path) -> str:
        return f'icacls "{file_path}"'

    def get_set_permissions_command(self, file_path: Path, permissions: str) -> str:
        return f'icacls "{file_path}" /grant {permissions}'

    # ==================== Process Management ====================

    def get_process_list_command(self) -> str:
        return "tasklist /V /FO CSV"

    def get_kill_process_command(self, pid: int) -> str:
        return f"taskkill /PID {pid} /F"

    # ==================== Network ====================

    def get_network_interfaces_command(self) -> str:
        return "ipconfig /all"

    def get_dns_lookup_command(self, hostname: str) -> str:
        return f"nslookup {hostname}"

    def get_ping_command(self, host: str, count: int = 4) -> str:
        return f"ping -n {count} {host}"

    # ==================== Shell ====================

    @property
    def default_shell(self) -> str:
        return "powershell.exe"

    @property
    def path_separator(self) -> str:
        return "\\"

    @property
    def line_ending(self) -> str:
        return "\r\n"

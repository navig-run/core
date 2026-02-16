"""
Service Installer for NAVIG Agent

Installs NAVIG agent as a system service:
- Linux: systemd unit file
- macOS: launchd plist
- Windows: Windows Service (using nssm)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from navig.debug_logger import DebugLogger


class ServiceInstaller:
    """
    Installs and manages NAVIG agent as a system service.
    
    Supports:
    - Linux (systemd)
    - macOS (launchd)
    - Windows (Windows Service via nssm)
    """
    
    def __init__(self):
        self.logger = DebugLogger()
        self.system = platform.system().lower()
        self.user = os.getenv('USER') or os.getenv('USERNAME')
        self.home = Path.home()
        self.navig_path = self._find_navig_executable()
    
    def _find_navig_executable(self) -> Path:
        """Find the navig executable path."""
        # Try to find navig in PATH
        navig_cmd = shutil.which('navig')
        if navig_cmd:
            return Path(navig_cmd)
        
        # Try python module
        python_exe = sys.executable
        return Path(python_exe).parent / 'navig'
    
    def install(self, start_now: bool = True) -> Tuple[bool, str]:
        """
        Install NAVIG agent as a system service.
        
        Args:
            start_now: Whether to start the service immediately
            
        Returns:
            (success, message) tuple
        """
        if self.system == 'linux':
            return self._install_systemd(start_now)
        elif self.system == 'darwin':
            return self._install_launchd(start_now)
        elif self.system == 'windows':
            return self._install_windows(start_now)
        else:
            return False, f"Unsupported platform: {self.system}"
    
    def uninstall(self) -> Tuple[bool, str]:
        """
        Uninstall NAVIG agent service.
        
        Returns:
            (success, message) tuple
        """
        if self.system == 'linux':
            return self._uninstall_systemd()
        elif self.system == 'darwin':
            return self._uninstall_launchd()
        elif self.system == 'windows':
            return self._uninstall_windows()
        else:
            return False, f"Unsupported platform: {self.system}"
    
    def status(self) -> Tuple[bool, str]:
        """
        Get service status.
        
        Returns:
            (is_running, status_message) tuple
        """
        if self.system == 'linux':
            return self._status_systemd()
        elif self.system == 'darwin':
            return self._status_launchd()
        elif self.system == 'windows':
            return self._status_windows()
        else:
            return False, f"Unsupported platform: {self.system}"
    
    # =========================================================================
    # LINUX - systemd
    # =========================================================================
    
    def _install_systemd(self, start_now: bool) -> Tuple[bool, str]:
        """Install systemd service."""
        service_name = 'navig-agent'
        service_file = f"{service_name}.service"
        
        # Determine if user or system service
        is_user_service = os.geteuid() != 0 if hasattr(os, 'geteuid') else True
        
        if is_user_service:
            service_dir = self.home / '.config' / 'systemd' / 'user'
            systemctl_args = ['systemctl', '--user']
        else:
            service_dir = Path('/etc/systemd/system')
            systemctl_args = ['systemctl']
        
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path = service_dir / service_file
        
        # Generate systemd unit file
        unit_content = self._generate_systemd_unit()
        
        try:
            # Write service file
            service_path.write_text(unit_content)
            self.logger.log_operation("service", {"action": "install", "platform": "systemd", "path": str(service_path)})
            
            # Reload systemd
            subprocess.run(systemctl_args + ['daemon-reload'], check=True, capture_output=True)
            
            # Enable service
            subprocess.run(systemctl_args + ['enable', service_name], check=True, capture_output=True)
            
            # Start if requested
            if start_now:
                subprocess.run(systemctl_args + ['start', service_name], check=True, capture_output=True)
                return True, f"Service installed and started: {service_path}"
            else:
                return True, f"Service installed: {service_path}"
                
        except subprocess.CalledProcessError as e:
            return False, f"Failed to install service: {e.stderr.decode() if e.stderr else str(e)}"
        except Exception as e:
            return False, f"Failed to install service: {e}"
    
    def _uninstall_systemd(self) -> Tuple[bool, str]:
        """Uninstall systemd service."""
        service_name = 'navig-agent'
        
        is_user_service = os.geteuid() != 0 if hasattr(os, 'geteuid') else True
        systemctl_args = ['systemctl', '--user'] if is_user_service else ['systemctl']
        
        try:
            # Stop service
            subprocess.run(systemctl_args + ['stop', service_name], capture_output=True)
            
            # Disable service
            subprocess.run(systemctl_args + ['disable', service_name], capture_output=True)
            
            # Remove service file
            if is_user_service:
                service_path = self.home / '.config' / 'systemd' / 'user' / f"{service_name}.service"
            else:
                service_path = Path('/etc/systemd/system') / f"{service_name}.service"
            
            if service_path.exists():
                service_path.unlink()
            
            # Reload systemd
            subprocess.run(systemctl_args + ['daemon-reload'], capture_output=True)
            
            return True, "Service uninstalled successfully"
            
        except Exception as e:
            return False, f"Failed to uninstall service: {e}"
    
    def _status_systemd(self) -> Tuple[bool, str]:
        """Get systemd service status."""
        service_name = 'navig-agent'
        
        is_user_service = os.geteuid() != 0 if hasattr(os, 'geteuid') else True
        systemctl_args = ['systemctl', '--user'] if is_user_service else ['systemctl']
        
        try:
            result = subprocess.run(
                systemctl_args + ['is-active', service_name],
                capture_output=True,
                text=True
            )
            
            is_running = result.returncode == 0 and result.stdout.strip() == 'active'
            
            # Get detailed status
            status_result = subprocess.run(
                systemctl_args + ['status', service_name],
                capture_output=True,
                text=True
            )
            
            return is_running, status_result.stdout
            
        except Exception as e:
            return False, f"Failed to get status: {e}"
    
    def _generate_systemd_unit(self) -> str:
        """Generate systemd unit file content."""
        python_exe = sys.executable
        navig_module = 'navig'
        
        return f"""[Unit]
Description=NAVIG Autonomous Agent
After=network.target

[Service]
Type=simple
User={self.user}
WorkingDirectory={self.home}
ExecStart={python_exe} -m {navig_module} agent start --foreground
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment
Environment="HOME={self.home}"
Environment="USER={self.user}"

# Security
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
"""
    
    # =========================================================================
    # macOS - launchd
    # =========================================================================
    
    def _install_launchd(self, start_now: bool) -> Tuple[bool, str]:
        """Install launchd service."""
        service_name = 'com.navig.agent'
        plist_file = f"{service_name}.plist"
        
        # User LaunchAgents directory
        launch_dir = self.home / 'Library' / 'LaunchAgents'
        launch_dir.mkdir(parents=True, exist_ok=True)
        plist_path = launch_dir / plist_file
        
        # Generate plist
        plist_content = self._generate_launchd_plist()
        
        try:
            # Write plist file
            plist_path.write_text(plist_content)
            self.logger.log_operation("service", {"action": "install", "platform": "launchd", "path": str(plist_path)})
            
            # Load service
            subprocess.run(['launchctl', 'load', str(plist_path)], check=True, capture_output=True)
            
            # Start if requested
            if start_now:
                subprocess.run(['launchctl', 'start', service_name], check=True, capture_output=True)
                return True, f"Service installed and started: {plist_path}"
            else:
                return True, f"Service installed: {plist_path}"
                
        except subprocess.CalledProcessError as e:
            return False, f"Failed to install service: {e.stderr.decode() if e.stderr else str(e)}"
        except Exception as e:
            return False, f"Failed to install service: {e}"
    
    def _uninstall_launchd(self) -> Tuple[bool, str]:
        """Uninstall launchd service."""
        service_name = 'com.navig.agent'
        plist_path = self.home / 'Library' / 'LaunchAgents' / f"{service_name}.plist"
        
        try:
            # Unload service
            subprocess.run(['launchctl', 'unload', str(plist_path)], capture_output=True)
            
            # Remove plist file
            if plist_path.exists():
                plist_path.unlink()
            
            return True, "Service uninstalled successfully"
            
        except Exception as e:
            return False, f"Failed to uninstall service: {e}"
    
    def _status_launchd(self) -> Tuple[bool, str]:
        """Get launchd service status."""
        service_name = 'com.navig.agent'
        
        try:
            result = subprocess.run(
                ['launchctl', 'list', service_name],
                capture_output=True,
                text=True
            )
            
            is_running = result.returncode == 0
            return is_running, result.stdout if is_running else "Service not loaded"
            
        except Exception as e:
            return False, f"Failed to get status: {e}"
    
    def _generate_launchd_plist(self) -> str:
        """Generate launchd plist file content."""
        python_exe = sys.executable
        navig_module = 'navig'
        log_dir = self.home / '.navig' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.navig.agent</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>-m</string>
        <string>{navig_module}</string>
        <string>agent</string>
        <string>start</string>
        <string>--foreground</string>
    </array>
    
    <key>WorkingDirectory</key>
    <string>{self.home}</string>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    
    <key>StandardOutPath</key>
    <string>{log_dir}/agent.stdout.log</string>
    
    <key>StandardErrorPath</key>
    <string>{log_dir}/agent.stderr.log</string>
    
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>{self.home}</string>
        <key>USER</key>
        <string>{self.user}</string>
    </dict>
</dict>
</plist>
"""
    
    # =========================================================================
    # Windows - Windows Service
    # =========================================================================
    
    def _install_windows(self, start_now: bool) -> Tuple[bool, str]:
        """Install Windows service."""
        # Check if running as administrator
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            is_admin = False
        
        if not is_admin:
            return False, "Administrator privileges required. Run PowerShell as Administrator."
        
        service_name = 'NAVIGAgent'
        python_exe = sys.executable
        
        try:
            # Try using nssm (recommended)
            if shutil.which('nssm'):
                return self._install_windows_nssm(service_name, python_exe, start_now)
            else:
                # Fallback to sc.exe
                return self._install_windows_sc(service_name, python_exe, start_now)
                
        except Exception as e:
            return False, f"Failed to install service: {e}"
    
    def _install_windows_nssm(self, service_name: str, python_exe: str, start_now: bool) -> Tuple[bool, str]:
        """Install Windows service using nssm."""
        navig_cmd = f'"{python_exe}" -m navig agent start --foreground'
        
        try:
            # Install service
            subprocess.run(
                ['nssm', 'install', service_name, python_exe, '-m', 'navig', 'agent', 'start', '--foreground'],
                check=True,
                capture_output=True
            )
            
            # Set working directory
            subprocess.run(
                ['nssm', 'set', service_name, 'AppDirectory', str(self.home)],
                capture_output=True
            )
            
            # Set description
            subprocess.run(
                ['nssm', 'set', service_name, 'Description', 'NAVIG Autonomous Agent'],
                capture_output=True
            )
            
            # Set startup type to automatic
            subprocess.run(
                ['nssm', 'set', service_name, 'Start', 'SERVICE_AUTO_START'],
                capture_output=True
            )
            
            # Start if requested
            if start_now:
                subprocess.run(['nssm', 'start', service_name], check=True, capture_output=True)
                return True, "Service installed and started using nssm"
            else:
                return True, "Service installed using nssm"
                
        except subprocess.CalledProcessError as e:
            return False, f"Failed to install with nssm: {e.stderr.decode() if e.stderr else str(e)}"
    
    def _install_windows_sc(self, service_name: str, python_exe: str, start_now: bool) -> Tuple[bool, str]:
        """Install Windows service using sc.exe."""
        navig_cmd = f'"{python_exe}" -m navig agent start --foreground'
        
        try:
            # Create service
            subprocess.run(
                ['sc', 'create', service_name, f'binPath={navig_cmd}', 'start=auto', 'DisplayName=NAVIG Agent'],
                check=True,
                capture_output=True
            )
            
            # Start if requested
            if start_now:
                subprocess.run(['sc', 'start', service_name], check=True, capture_output=True)
                return True, "Service installed and started using sc.exe"
            else:
                return True, "Service installed using sc.exe"
                
        except subprocess.CalledProcessError as e:
            return False, f"Failed to install with sc.exe: {e.stderr.decode() if e.stderr else str(e)}"
    
    def _uninstall_windows(self) -> Tuple[bool, str]:
        """Uninstall Windows service."""
        service_name = 'NAVIGAgent'
        
        try:
            # Check if running as admin
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            is_admin = False
        
        if not is_admin:
            return False, "Administrator privileges required."
        
        try:
            # Try nssm first
            if shutil.which('nssm'):
                subprocess.run(['nssm', 'stop', service_name], capture_output=True)
                subprocess.run(['nssm', 'remove', service_name, 'confirm'], check=True, capture_output=True)
                return True, "Service uninstalled using nssm"
            else:
                # Use sc.exe
                subprocess.run(['sc', 'stop', service_name], capture_output=True)
                subprocess.run(['sc', 'delete', service_name], check=True, capture_output=True)
                return True, "Service uninstalled using sc.exe"
                
        except subprocess.CalledProcessError as e:
            return False, f"Failed to uninstall: {e.stderr.decode() if e.stderr else str(e)}"
        except Exception as e:
            return False, f"Failed to uninstall: {e}"
    
    def _status_windows(self) -> Tuple[bool, str]:
        """Get Windows service status."""
        service_name = 'NAVIGAgent'
        
        try:
            result = subprocess.run(
                ['sc', 'query', service_name],
                capture_output=True,
                text=True
            )
            
            is_running = 'RUNNING' in result.stdout
            return is_running, result.stdout
            
        except Exception as e:
            return False, f"Failed to get status: {e}"

"""
Connection Adapters for NAVIG

Polymorphic execution layer that abstracts the difference between
SSH (remote) and subprocess (local) command execution.
"""

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


def _resolve_ssh_bin() -> str:
    """Resolve the ssh binary path, with Windows OpenSSH fallback."""
    bin_path = shutil.which("ssh") or shutil.which("ssh.exe")
    if bin_path is None and os.name == "nt":
        _sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        for _candidate in (
            os.path.join(_sysroot, "SysNative", "OpenSSH", "ssh.exe"),
            os.path.join(_sysroot, "System32", "OpenSSH", "ssh.exe"),
        ):
            if os.path.isfile(_candidate):
                bin_path = _candidate
                break
    if bin_path is None:
        raise RuntimeError("SSH client not found on PATH. Install OpenSSH.")
    return bin_path


def _resolve_scp_bin() -> str:
    """Resolve the scp binary path, with Windows OpenSSH fallback."""
    bin_path = shutil.which("scp") or shutil.which("scp.exe")
    if bin_path is None and os.name == "nt":
        _sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        for _candidate in (
            os.path.join(_sysroot, "SysNative", "OpenSSH", "scp.exe"),
            os.path.join(_sysroot, "System32", "OpenSSH", "scp.exe"),
        ):
            if os.path.isfile(_candidate):
                bin_path = _candidate
                break
    if bin_path is None:
        raise RuntimeError("SCP client not found on PATH. Install OpenSSH.")
    return bin_path


@dataclass
class CommandResult:
    """Result of executing a command."""

    stdout: str
    stderr: str
    exit_code: int
    duration: float = 0.0  # Execution duration in seconds

    @property
    def success(self) -> bool:
        """Return True if command executed successfully (exit code 0)."""
        return self.exit_code == 0

    @property
    def output(self) -> str:
        """Return combined stdout and stderr for convenience."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration": self.duration,
            "success": self.success,
        }


class ConnectionAdapter(ABC):
    """
    Abstract base class for connection adapters.

    Provides unified interface for executing commands on both
    local and remote systems.
    """

    @abstractmethod
    def run(
        self, command: str, capture_output: bool = True, timeout: float | None = None
    ) -> CommandResult:
        """
        Execute a command on the target system.

        Args:
            command: Shell command to execute
            capture_output: Whether to capture stdout/stderr
            timeout: Optional timeout in seconds

        Returns:
            CommandResult with stdout, stderr, exit_code, and duration
        """
        pass

    @abstractmethod
    def upload(self, local_path: Path, remote_path: Path) -> bool:
        """
        Transfer file to target system.

        Args:
            local_path: Source file on originating system
            remote_path: Destination path on target system

        Returns:
            True if transfer succeeded
        """
        pass

    @abstractmethod
    def download(self, remote_path: Path, local_path: Path) -> bool:
        """
        Retrieve file from target system.

        Args:
            remote_path: Source path on target system
            local_path: Destination on local system

        Returns:
            True if transfer succeeded
        """
        pass

    @abstractmethod
    def detect_os(self) -> str:
        """
        Detect operating system of target.

        Returns:
            OS type: 'windows', 'linux', or 'darwin'
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Cleanup connection resources.

        For SSH: close the SSH session
        For local: cleanup any subprocess handles
        """
        pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure cleanup."""
        self.close()
        return False


class LocalConnection(ConnectionAdapter):
    """
    Local subprocess-based execution adapter.

    Executes commands on the local machine using subprocess,
    providing the same interface as SSH connections for remote hosts.
    """

    def __init__(
        self,
        os_type: str | None = None,
        working_directory: Path | None = None,
    ):
        """
        Initialize local connection.

        Args:
            os_type: Override OS detection ('windows', 'linux', 'darwin').
                     If None, auto-detect using platform.system()
            working_directory: Optional working directory used for command execution
        """
        import platform

        self._working_directory = working_directory

        if os_type:
            self._os_type = os_type.lower()
        else:
            system = platform.system().lower()
            if system == "darwin":
                self._os_type = "darwin"
            elif system == "windows":
                self._os_type = "windows"
            else:
                self._os_type = "linux"

        self._working_directory = working_directory

    def run(
        self, command: str, capture_output: bool = True, timeout: float | None = None
    ) -> CommandResult:
        """
        Execute command locally via subprocess.

        Args:
            command: Shell command to execute
            capture_output: Whether to capture stdout/stderr
            timeout: Optional timeout in seconds (default: 300)

        Returns:
            CommandResult with execution details
        """
        import time

        start_time = time.time()
        cmd_timeout = timeout if timeout is not None else 300

        try:
            cwd = str(self._working_directory) if self._working_directory else None
            if self._os_type == "windows":
                # On Windows, use PowerShell for better compatibility.
                # Use explicit encoding + errors="replace" instead of text=True to
                # prevent UnicodeDecodeError in subprocess readerthread when
                # PowerShell outputs non-UTF-8 bytes (fixes #48).
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=capture_output,
                    encoding="utf-8",
                    errors="replace",
                    timeout=cmd_timeout,
                    cwd=cwd,
                )
            else:
                # On Unix systems, use bash -c (safer than raw shell=True)
                result = subprocess.run(
                    ["bash", "-c", command],
                    capture_output=capture_output,
                    encoding="utf-8",
                    errors="replace",
                    timeout=cmd_timeout,
                    cwd=cwd,
                )

            return CommandResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.returncode,
                duration=time.time() - start_time,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                stdout="",
                stderr=f"Command timed out after {cmd_timeout} seconds",
                exit_code=124,  # Standard timeout exit code
                duration=time.time() - start_time,
            )
        except Exception as e:
            return CommandResult(
                stdout="", stderr=str(e), exit_code=1, duration=time.time() - start_time
            )

    def upload(self, local_path: Path, remote_path: Path) -> bool:
        """
        Copy file locally (for local "host", upload is just a copy).

        Args:
            local_path: Source file
            remote_path: Destination path

        Returns:
            True if copy succeeded
        """
        try:
            # Ensure destination directory exists
            remote_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, remote_path)
            return True
        except (OSError, PermissionError):
            return False

    def download(self, remote_path: Path, local_path: Path) -> bool:
        """
        Copy file locally (for local "host", download is just a copy).

        Args:
            remote_path: Source path
            local_path: Destination path

        Returns:
            True if copy succeeded
        """
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(remote_path, local_path)
            return True
        except (OSError, PermissionError):
            return False

    def detect_os(self) -> str:
        """Return detected OS type."""
        return self._os_type

    def close(self) -> None:
        """No cleanup needed for local connection."""
        pass


class SSHConnection(ConnectionAdapter):
    """
    SSH-based execution adapter for remote hosts.

    Uses subprocess to call ssh/scp commands for remote execution.
    This preserves existing behavior while providing unified interface.
    """

    def __init__(self, host_config: dict[str, Any]):
        """
        Initialize SSH connection.

        Args:
            host_config: Host configuration dictionary with:
                - host: Hostname or IP address
                - user: SSH username
                - port: SSH port (default 22)
                - ssh_key: Path to SSH key (optional)
        """
        self.host_config = host_config
        self._cached_os: str | None = None

    def _build_ssh_args(self) -> list:
        """Build base SSH command arguments."""
        ssh_args = [_resolve_ssh_bin()]
        ssh_args.extend(["-o", "StrictHostKeyChecking=yes"])
        ssh_args.extend(["-o", "ConnectTimeout=10"])

        if self.host_config.get("port", 22) != 22:
            ssh_args.extend(["-p", str(self.host_config["port"])])

        if self.host_config.get("ssh_key"):
            ssh_args.extend(["-i", self.host_config["ssh_key"]])

        ssh_args.append(f"{self.host_config['user']}@{self.host_config['host']}")
        return ssh_args

    def run(
        self, command: str, capture_output: bool = True, timeout: float | None = None
    ) -> CommandResult:
        """
        Execute command on remote host via SSH.

        Args:
            command: Shell command to execute
            capture_output: Whether to capture stdout/stderr
            timeout: Optional timeout in seconds (default: 300)

        Returns:
            CommandResult with execution details
        """
        import time

        start_time = time.time()
        cmd_timeout = timeout if timeout is not None else 300

        ssh_args = self._build_ssh_args()
        ssh_args.append(command)

        try:
            result = subprocess.run(
                ssh_args,
                capture_output=capture_output,
                text=True,
                timeout=cmd_timeout,
            )

            return CommandResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.returncode,
                duration=time.time() - start_time,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                stdout="",
                stderr=f"SSH command timed out after {cmd_timeout} seconds",
                exit_code=124,
                duration=time.time() - start_time,
            )
        except Exception as e:
            return CommandResult(
                stdout="", stderr=str(e), exit_code=1, duration=time.time() - start_time
            )

    def upload(self, local_path: Path, remote_path: Path) -> bool:
        """
        Upload file to remote host via SCP.

        Args:
            local_path: Source file on local system
            remote_path: Destination path on remote host

        Returns:
            True if transfer succeeded
        """
        scp_args = [_resolve_scp_bin()]

        if self.host_config.get("port", 22) != 22:
            scp_args.extend(["-P", str(self.host_config["port"])])

        if self.host_config.get("ssh_key"):
            scp_args.extend(["-i", self.host_config["ssh_key"]])

        scp_args.append(str(local_path))
        scp_args.append(f"{self.host_config['user']}@{self.host_config['host']}:{remote_path}")

        result = subprocess.run(scp_args, capture_output=True)
        return result.returncode == 0

    def download(self, remote_path: Path, local_path: Path) -> bool:
        """
        Download file from remote host via SCP.

        Args:
            remote_path: Source path on remote host
            local_path: Destination on local system

        Returns:
            True if transfer succeeded
        """
        scp_args = [_resolve_scp_bin()]

        if self.host_config.get("port", 22) != 22:
            scp_args.extend(["-P", str(self.host_config["port"])])

        if self.host_config.get("ssh_key"):
            scp_args.extend(["-i", self.host_config["ssh_key"]])

        scp_args.append(f"{self.host_config['user']}@{self.host_config['host']}:{remote_path}")
        scp_args.append(str(local_path))

        result = subprocess.run(scp_args, capture_output=True)
        return result.returncode == 0

    def detect_os(self) -> str:
        """
        Detect remote OS by running uname command.

        Returns:
            OS type: 'windows', 'linux', or 'darwin'
        """
        if self._cached_os:
            return self._cached_os

        # Try uname first (works on Linux/macOS)
        result = self.run("uname -s", capture_output=True)
        if result.success:
            output = result.stdout.strip().lower()
            if "darwin" in output:
                self._cached_os = "darwin"
            elif "linux" in output:
                self._cached_os = "linux"
            else:
                self._cached_os = "linux"  # Default to Linux for unknown Unix
            return self._cached_os

        # If uname fails, might be Windows
        result = self.run("ver", capture_output=True)
        if result.success and "windows" in result.stdout.lower():
            self._cached_os = "windows"
            return self._cached_os

        # Default to Linux
        self._cached_os = "linux"
        return self._cached_os

    def close(self) -> None:
        """
        Cleanup SSH connection.

        Currently no persistent connection to close since we use
        subprocess for each command. This method exists for interface
        compliance and potential future optimization (connection pooling).
        """
        pass


def get_connection(host_config: dict[str, Any]) -> ConnectionAdapter:
    """
    Factory function to get appropriate connection adapter.

    Examines host configuration to determine whether to use
    local subprocess execution or SSH connection.

    Args:
        host_config: Host configuration dictionary

    Returns:
        Appropriate ConnectionAdapter instance

    Raises:
        ValueError: If host configuration is invalid
    """
    host_type = host_config.get("type", "ssh")

    if host_type == "local":
        os_type = host_config.get("os")
        return LocalConnection(os_type=os_type)
    elif host_type == "ssh":
        # Validate required SSH fields
        required = ["host", "user"]
        missing = [f for f in required if not host_config.get(f)]
        if missing:
            raise ValueError(f"SSH host config missing required fields: {missing}")
        return SSHConnection(host_config)
    else:
        raise ValueError(f"Unknown host type: {host_type}")

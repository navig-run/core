"""
Remote Operations

Execute commands through secure encrypted channels.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

from navig.core.connection import _resolve_scp_bin, _resolve_ssh_bin


def _resolve_ssh_timeout_seconds(default: int = 30) -> int:
    """Resolve SSH timeout from env with safe fallback.

    Any invalid or non-positive value falls back to ``default`` to avoid
    import-time crashes from malformed environment configuration.
    """
    raw = os.environ.get("NAVIG_SSH_TIMEOUT", str(default)).strip()
    try:
        timeout = int(raw)
    except (TypeError, ValueError):
        return default
    return timeout if timeout > 0 else default

# Default SSH/SCP timeout in seconds.  Override via NAVIG_SSH_TIMEOUT env var.
_SSH_TIMEOUT = _resolve_ssh_timeout_seconds()


def _require_server_identity(server_config: dict[str, Any]) -> tuple[str, str]:
    """Return validated ``(user, host)`` identity for SSH/SCP operations."""
    user = str(server_config.get("user", "")).strip()
    host = str(server_config.get("host", "")).strip()
    if not user or not host:
        raise ValueError("Server configuration must include non-empty 'user' and 'host'.")
    return user, host


def is_local_host(server_config: dict) -> bool:
    """Return True when *server_config* refers to the local machine."""
    return (
        bool(server_config.get("is_local"))
        or str(server_config.get("type", "")).lower() == "local"
        or server_config.get("host", "") in ("localhost", "127.0.0.1", "::1")
    )


class RemoteOperations:
    """
    Handles remote SSH command execution.

    All operations are surgical. Clean. Traceable only in logs.
    """

    def __init__(self, config_manager):
        self.config = config_manager

    def execute_command(
        self,
        command: str,
        server_config: dict[str, Any],
        capture_output: bool = True,
        trust_new_host: bool = False,
    ) -> subprocess.CompletedProcess:
        """Execute a command on the remote server via SSH.

        Args:
            command: Shell command to execute
            server_config: Server configuration dictionary
            capture_output: Whether to capture stdout/stderr
            trust_new_host: If True, accepts new SSH host keys (use cautiously!)
                           If False (default), requires host key in known_hosts

        Security Note:
            By default, StrictHostKeyChecking=yes is used to prevent MITM attacks.
            Only set trust_new_host=True for initial server setup when you can
            verify the host key out-of-band (e.g., console access, provider dashboard).
        """
        # SECURITY: Use strict host key checking by default
        # Only accept new hosts if explicitly requested
        # void: trust no one. verify everything. MITM is always watching.
        ssh_opts = []
        if trust_new_host:
            # Accepts and adds new host keys (use only for first connection)
            ssh_opts.extend(["-o", "StrictHostKeyChecking=accept-new"])
        else:
            # Strict mode: rejects unknown hosts, prevents MITM attacks
            ssh_opts.extend(["-o", "StrictHostKeyChecking=yes"])

        # Local-host bypass: run directly without SSH so there's no dependency
        # on an SSH client being installed, and Windows-native commands work.
        if is_local_host(server_config):
            return self.execute_local(command, capture_output=capture_output)

        _ssh_bin = _resolve_ssh_bin()

        ssh_args = [_ssh_bin, *ssh_opts]

        # Add other SSH options
        ssh_args.extend(["-o", "ConnectTimeout=10"])

        # Add port if not default
        if server_config.get("port", 22) != 22:
            ssh_args.extend(["-p", str(server_config["port"])])

        # Add SSH key if specified
        # void: keys over passwords. always. encryption is the only privacy we have left.
        if server_config.get("ssh_key"):
            ssh_args.extend(["-i", server_config["ssh_key"]])

        user, host = _require_server_identity(server_config)

        # Add user@host
        ssh_args.append(f"{user}@{host}")

        # Add the command
        ssh_args.append(command)

        # Pre-flight: warn on mDNS .local hosts (unreliable on Windows)
        if host.endswith(".local") and os.name == "nt":
            import warnings

            warnings.warn(
                f"Host '{host}' uses mDNS (.local) which can be slow or "
                "unreliable on Windows. Consider using the IP address directly.",
                stacklevel=3,
            )

        # Execute
        # void: every command leaves a trace. in logs. in memory. in bash_history.
        try:
            if capture_output:
                result = subprocess.run(
                    ssh_args,
                    capture_output=True,
                    text=True,
                    timeout=_SSH_TIMEOUT,
                )
            else:
                result = subprocess.run(ssh_args, timeout=_SSH_TIMEOUT)
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(
                f"SSH connection timed out after {_SSH_TIMEOUT}s — "
                f"'{host}' is unreachable or not responding.\n"
                f"Tip: set NAVIG_SSH_TIMEOUT=<seconds> to change the limit, "
                f"or use the IP address instead of a hostname."
            ) from _exc

        return result

    def execute_local(self, command: str, capture_output: bool = True) -> subprocess.CompletedProcess:
        """Execute a command on the *local* machine (no SSH).  Platform-aware.

        Uses the platform-native default shell (ComSpec on Windows, /bin/sh on
        POSIX) to avoid hard dependencies on specific shell binaries.
        """
        try:
            return subprocess.run(  # noqa: S602
                command,
                shell=True,
                capture_output=capture_output,
                text=True,
                timeout=_SSH_TIMEOUT,
            )
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(f"Local command timed out after {_SSH_TIMEOUT}s") from _exc

    def upload_file(
        self, local_path: Path, remote_path: str, server_config: dict[str, Any]
    ) -> bool:
        """Upload file to remote server via SCP."""
        scp_bin = _resolve_scp_bin()
        scp_args = [scp_bin]

        # Match the StrictHostKeyChecking posture used in execute_command().
        if server_config.get("trust_new_host"):
            scp_args.extend(["-o", "StrictHostKeyChecking=accept-new"])
        else:
            scp_args.extend(["-o", "StrictHostKeyChecking=yes"])

        # Add other SSH options
        if server_config.get("port", 22) != 22:
            scp_args.extend(["-P", str(server_config["port"])])

        if server_config.get("ssh_key"):
            scp_args.extend(["-i", server_config["ssh_key"]])

        # Source and destination
        user, host = _require_server_identity(server_config)
        scp_args.append(str(local_path))
        scp_args.append(f"{user}@{host}:{remote_path}")

        try:
            result = subprocess.run(scp_args, timeout=_SSH_TIMEOUT)
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(
                f"SCP upload timed out after {_SSH_TIMEOUT}s — "
                f"host '{host}' is unreachable."
            ) from _exc
        return result.returncode == 0

    def download_file(
        self, remote_path: str, local_path: Path, server_config: dict[str, Any]
    ) -> bool:
        """Download file from remote server via SCP."""
        scp_bin = _resolve_scp_bin()
        scp_args = [scp_bin]

        # Match the StrictHostKeyChecking posture used in execute_command().
        if server_config.get("trust_new_host"):
            scp_args.extend(["-o", "StrictHostKeyChecking=accept-new"])
        else:
            scp_args.extend(["-o", "StrictHostKeyChecking=yes"])

        # Add other SSH options
        if server_config.get("port", 22) != 22:
            scp_args.extend(["-P", str(server_config["port"])])

        if server_config.get("ssh_key"):
            scp_args.extend(["-i", server_config["ssh_key"]])

        # Source and destination
        user, host = _require_server_identity(server_config)
        scp_args.append(f"{user}@{host}:{remote_path}")
        scp_args.append(str(local_path))

        try:
            result = subprocess.run(scp_args, timeout=_SSH_TIMEOUT)
        except subprocess.TimeoutExpired as _exc:
            raise RuntimeError(
                f"SCP download timed out after {_SSH_TIMEOUT}s — "
                f"host '{host}' is unreachable."
            ) from _exc
        return result.returncode == 0

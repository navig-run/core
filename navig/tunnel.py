"""
SSH Tunnel Manager for NAVIG

Manages secure encrypted channels. No exposed ports. No traces.
The Schema's preferred method of communication.
"""

import json
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psutil  # We'll add this to requirements if needed

# Platform-specific imports for file locking
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class TunnelManager:
    """
    Manages SSH tunnels for secure database access.

    All traffic is encrypted. The void sees nothing.
    """

    def __init__(self, config_manager):
        self.config = config_manager
        self.tunnels_file = config_manager.tunnels_file
        self.log_file = config_manager.log_file

    @contextmanager
    def _lock_tunnels_file(self):
        """Context manager for file locking to prevent race conditions."""
        lock_file = self.tunnels_file.parent / f"{self.tunnels_file.name}.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_file, "w", encoding="utf-8") as lock:
            try:
                if sys.platform == "win32":
                    # Windows file locking
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    # Unix file locking
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

                yield lock
            finally:
                if sys.platform == "win32":
                    try:
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass  # Cleanup - unlock may fail
                else:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass  # Cleanup - unlock may fail

    def _load_tunnels(self) -> dict[str, Any]:
        """Load active tunnel state from cache (atomic with file locking)."""
        if not self.tunnels_file.exists():
            return {}

        try:
            with self._lock_tunnels_file(), open(self.tunnels_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_tunnels(self, tunnels: dict[str, Any]):
        """Save tunnel state to cache (atomic with file locking)."""
        self.tunnels_file.parent.mkdir(parents=True, exist_ok=True)

        with (
            self._lock_tunnels_file(),
            open(self.tunnels_file, "w", encoding="utf-8") as f,
        ):
            json.dump(tunnels, f, indent=2)

    def _find_available_port(self, start_port: int = 3307, end_port: int = 3399) -> int:
        """
        Find an available port for the tunnel.
        Port conflict detected. Shifting to stealth mode.
        """
        for port in range(start_port, end_port + 1):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue

        raise RuntimeError(f"No available ports in range {start_port}-{end_port}")

    def _test_port(self, port: int, timeout: float = 2.0) -> bool:
        """Test if a port is accessible."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(("127.0.0.1", port))
                return True
        except (TimeoutError, ConnectionRefusedError, OSError):
            return False

    def _is_process_running(self, pid: int) -> bool:
        """Check if a process is still running."""
        try:
            process = psutil.Process(pid)
            return process.is_running()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def get_tunnel_status(self, server_name: str | None = None) -> dict[str, Any] | None:
        """
        Get tunnel status for a server.

        Returns None if no tunnel is running, otherwise returns tunnel info:
        {
            'server': 'remotekit',
            'pid': 12345,
            'local_port': 3307,
            'started_at': '2025-11-20T10:30:00',
            'is_running': True
        }
        """
        if server_name is None:
            server_name = self.config.get_active_server()
            if server_name is None:
                return None

        tunnels = self._load_tunnels()
        tunnel_info = tunnels.get(server_name)

        if tunnel_info is None:
            return None

        # Verify the process is still running
        pid = tunnel_info.get("pid")
        if pid and self._is_process_running(pid):
            tunnel_info["is_running"] = True
            return tunnel_info
        else:
            # Process is dead. Clean up.
            del tunnels[server_name]
            self._save_tunnels(tunnels)
            return None

    def start_tunnel(
        self, server_name: str | None = None, force_port: int | None = None
    ) -> dict[str, Any]:
        """
        Start SSH tunnel for a server.

        Encrypted channel established. We're ghosts now.
        """
        if server_name is None:
            server_name = self.config.get_active_server()
            if server_name is None:
                raise ValueError("No active server. Use 'navig server use <name>' first.")

        # Check if tunnel already exists
        existing_tunnel = self.get_tunnel_status(server_name)
        if existing_tunnel:
            return existing_tunnel

        # Load server configuration
        server_config = self.config.load_server_config(server_name)

        # Determine local port
        if force_port:
            local_port = force_port
        else:
            preferred_port = server_config["database"].get("local_tunnel_port", 3307)
            try:
                # Try preferred port first
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", preferred_port))
                local_port = preferred_port
            except OSError:
                # Port conflict. Shifting to stealth mode.
                port_range = self.config.global_config.get("tunnel_port_range", [3307, 3399])
                local_port = self._find_available_port(port_range[0], port_range[1])

        # Build SSH tunnel command
        remote_host = server_config["database"].get("remote_port", "3306")
        remote_port = server_config["database"].get("remote_port", 3306)

        ssh_args = [
            "ssh",
            "-L",
            f"{local_port}:localhost:{remote_port}",
            "-N",  # Don't execute remote command
            "-f",  # Run in background
            "-o",
            "ServerAliveInterval=60",  # Keep connection alive
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",  # Auto-accept new host keys
        ]

        # Add port if not default
        if server_config.get("port", 22) != 22:
            ssh_args.extend(["-p", str(server_config["port"])])

        # Add SSH key if specified
        if server_config.get("ssh_key"):
            ssh_args.extend(["-i", server_config["ssh_key"]])

        # Add user@host
        ssh_args.append(f"{server_config['user']}@{server_config['host']}")

        # Execute SSH command
        try:
            # Start the SSH process
            process = subprocess.Popen(
                ssh_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Wait a moment for the tunnel to establish
            time.sleep(2)

            # The -f flag causes ssh to fork into the background and the launcher
            # process exits.  Close the inherited pipe handles on the parent side
            # immediately so we don't accumulate file descriptors if the PID
            # search below raises or the process object is never GC-collected.
            for pipe in (process.stdout, process.stderr, process.stdin):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass

            # Get the PID (for -f backgrounded ssh, we need to find it)
            # The process we started will fork and exit, so we need to find the actual tunnel process
            pid = self._find_tunnel_process(local_port, server_config["host"])

            if pid is None:
                raise RuntimeError("Failed to find SSH tunnel process")

            # Test the tunnel
            if not self._test_port(local_port, timeout=3.0):
                raise RuntimeError("Tunnel started but port is not accessible")

            # Save tunnel information
            tunnel_info = {
                "server": server_name,
                "pid": pid,
                "local_port": local_port,
                "started_at": datetime.now().isoformat(),
                "is_running": True,
            }

            tunnels = self._load_tunnels()
            tunnels[server_name] = tunnel_info
            self._save_tunnels(tunnels)

            self._log(f"[SUCCESS] Tunnel established: {server_name} -> 127.0.0.1:{local_port}")

            return tunnel_info

        except Exception as e:
            self._log(f"[ERROR] Failed to start tunnel for {server_name}: {e}")
            raise

    def _find_tunnel_process(
        self, local_port: int, remote_host: str, max_retries: int = 3
    ) -> int | None:
        """Find the SSH tunnel process by port and host (with retry logic)."""
        for attempt in range(max_retries):
            try:
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    try:
                        proc_name = proc.info.get("name", "")
                        if proc_name in ["ssh", "ssh.exe"]:
                            cmdline = proc.info.get("cmdline")
                            if cmdline:
                                cmdline_str = " ".join(cmdline)
                                # More precise matching: look for -L port:localhost:remote_port
                                if (
                                    f"-L {local_port}:localhost:" in cmdline_str
                                    and remote_host in cmdline_str
                                ) or (
                                    f"-L{local_port}:localhost:" in cmdline_str
                                    and remote_host in cmdline_str
                                ):
                                    return proc.info["pid"]
                    except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                        continue  # Process disappeared or no access

                # If not found on first attempt, wait and retry (process may still be starting)
                if attempt < max_retries - 1:
                    time.sleep(0.5)

            except (psutil.Error, OSError) as e:
                self._log(
                    f"[WARNING] Error finding tunnel process (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    time.sleep(0.5)

        return None

    def stop_tunnel(self, server_name: str | None = None) -> bool:
        """
        Stop SSH tunnel for a server.

        Connection severed. The void stares back.
        """
        if server_name is None:
            server_name = self.config.get_active_server()
            if server_name is None:
                raise ValueError("No active server")

        tunnel_info = self.get_tunnel_status(server_name)
        if tunnel_info is None:
            return False  # Already stopped

        pid = tunnel_info["pid"]

        try:
            # Try graceful shutdown first (SIGTERM)
            process = psutil.Process(pid)
            process.terminate()

            # Wait up to 5 seconds for graceful shutdown
            try:
                process.wait(timeout=5)
            except psutil.TimeoutExpired:
                # Force kill if graceful shutdown fails
                process.kill()

            # Remove from tunnels cache
            tunnels = self._load_tunnels()
            if server_name in tunnels:
                del tunnels[server_name]
                self._save_tunnels(tunnels)

            self._log(f"[SUCCESS] Tunnel stopped: {server_name}")
            return True

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            self._log(f"[WARNING] Failed to stop tunnel process: {e}")
            # Clean up anyway
            tunnels = self._load_tunnels()
            if server_name in tunnels:
                del tunnels[server_name]
                self._save_tunnels(tunnels)
            return False

    def restart_tunnel(self, server_name: str | None = None) -> dict[str, Any]:
        """Restart tunnel."""
        if server_name is None:
            server_name = self.config.get_active_server()

        self.stop_tunnel(server_name)
        time.sleep(1)
        return self.start_tunnel(server_name)

    def auto_cleanup(self):
        """
        Clean up stale tunnel references.

        The Schema doesn't leave loose ends.
        """
        tunnels = self._load_tunnels()
        active_tunnels = {}

        for server_name, tunnel_info in tunnels.items():
            pid = tunnel_info.get("pid")
            if pid and self._is_process_running(pid):
                active_tunnels[server_name] = tunnel_info

        if len(active_tunnels) != len(tunnels):
            self._save_tunnels(active_tunnels)
            self._log(f"[INFO] Cleaned up {len(tunnels) - len(active_tunnels)} stale tunnel(s)")

    @contextmanager
    def auto_tunnel(self, server_name: str | None = None, cleanup: bool = False):
        """
        Context manager for automatic tunnel lifecycle.

        Usage:
            with tunnel_manager.auto_tunnel('production') as tunnel:
                # Tunnel is guaranteed to be running
                result = execute_sql_query(...)
            # Tunnel optionally cleaned up on exit if cleanup=True

        Args:
            server_name: Server to create tunnel for (defaults to active server)
            cleanup: If True, stops tunnel on exit. If False, leaves tunnel running.
        """
        if server_name is None:
            server_name = self.config.get_active_server()
            if server_name is None:
                raise ValueError("No active server")

        # Check if tunnel exists, start if needed
        existing_tunnel = self.get_tunnel_status(server_name)
        tunnel_started_by_us = False

        if existing_tunnel is None:
            tunnel_info = self.start_tunnel(server_name)
            tunnel_started_by_us = True
        else:
            tunnel_info = existing_tunnel

        try:
            yield tunnel_info
        finally:
            # Only cleanup if we started it AND cleanup=True
            if cleanup and tunnel_started_by_us:
                try:
                    self.stop_tunnel(server_name)
                except Exception as e:
                    self._log(f"[WARNING] Failed to cleanup tunnel in context manager: {e}")

    def check_tunnel_health(self, server_name: str | None = None) -> dict[str, Any]:
        """
        Comprehensive health check for tunnel.

        Returns:
            {
                'is_healthy': bool,
                'issues': List[str],
                'tunnel_info': Dict or None,
                'port_accessible': bool,
                'process_running': bool
            }
        """
        if server_name is None:
            server_name = self.config.get_active_server()

        health = {
            "is_healthy": True,
            "issues": [],
            "tunnel_info": None,
            "port_accessible": False,
            "process_running": False,
        }

        tunnel_info = self.get_tunnel_status(server_name)

        if tunnel_info is None:
            health["is_healthy"] = False
            health["issues"].append("No tunnel running")
            return health

        health["tunnel_info"] = tunnel_info

        # Check process
        pid = tunnel_info.get("pid")
        if pid and self._is_process_running(pid):
            health["process_running"] = True
        else:
            health["is_healthy"] = False
            health["issues"].append(f"Tunnel process (PID {pid}) not running")

        # Check port
        local_port = tunnel_info.get("local_port")
        if local_port and self._test_port(local_port):
            health["port_accessible"] = True
        else:
            health["is_healthy"] = False
            health["issues"].append(f"Port {local_port} not accessible")

        return health

    def recover_tunnel(self, server_name: str | None = None) -> dict[str, Any]:
        """
        Attempt to recover unhealthy tunnel.

        Strategy:
        1. Check health
        2. If unhealthy, force stop
        3. Clean up zombie processes
        4. Restart tunnel
        """
        if server_name is None:
            server_name = self.config.get_active_server()

        health = self.check_tunnel_health(server_name)

        if health["is_healthy"]:
            return {"recovered": False, "message": "Tunnel is already healthy"}

        self._log(f"[INFO] Recovering unhealthy tunnel for {server_name}: {health['issues']}")

        # Force stop (cleanup zombie processes)
        try:
            self.stop_tunnel(server_name)
        except Exception as e:
            self._log(f"[WARNING] Error during force stop: {e}")

        # Wait for cleanup
        time.sleep(1)

        # Restart
        try:
            tunnel_info = self.start_tunnel(server_name)
            return {
                "recovered": True,
                "message": "Tunnel recovered successfully",
                "tunnel_info": tunnel_info,
            }
        except Exception as e:
            return {"recovered": False, "message": f"Recovery failed: {e}"}

    def _log(self, message: str):
        """Write to log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except OSError:
            pass  # Logging failure should not crash tunnel operations

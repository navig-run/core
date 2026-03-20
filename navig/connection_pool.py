"""
SSH Connection Pool

Provides connection pooling for SSH operations to reduce overhead
when executing multiple commands on the same server.

Performance Impact:
- First connection: ~1-3 seconds (full handshake)
- Pooled connection: ~5-50ms (reuse existing)
- Expected improvement: 2-10x for consecutive operations
"""

import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Lazy paramiko import
_paramiko = None


def _get_paramiko():
    """Lazy import paramiko only when needed."""
    global _paramiko
    if _paramiko is None:
        try:
            import paramiko
            _paramiko = paramiko
        except ImportError:
            _paramiko = False
    return _paramiko


class SSHConnection:
    """Wrapper for a pooled SSH connection with metadata."""

    def __init__(self, client, host: str, port: int, user: str):
        self.client = client
        self.host = host
        self.port = port
        self.user = user
        self.created_at = time.time()
        self.last_used = time.time()
        self.use_count = 0
        self._lock = threading.Lock()

    @property
    def key(self) -> str:
        """Unique identifier for this connection."""
        return f"{self.user}@{self.host}:{self.port}"

    @property
    def age_seconds(self) -> float:
        """How long this connection has existed."""
        return time.time() - self.created_at

    @property
    def idle_seconds(self) -> float:
        """How long since last use."""
        return time.time() - self.last_used

    def is_alive(self) -> bool:
        """Check if the connection is still active."""
        try:
            transport = self.client.get_transport()
            if transport is None:
                return False
            return transport.is_active()
        except Exception:
            return False

    def execute(self, command: str, timeout: int = 30) -> Tuple[bool, str, str]:
        """
        Execute a command on this connection.
        
        Returns:
            Tuple of (success, stdout, stderr)
        """
        with self._lock:
            self.last_used = time.time()
            self.use_count += 1

            try:
                stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
                stdout_text = stdout.read().decode('utf-8', errors='ignore').strip()
                stderr_text = stderr.read().decode('utf-8', errors='ignore').strip()
                exit_status = stdout.channel.recv_exit_status()

                return (exit_status == 0, stdout_text, stderr_text)
            except Exception as e:
                return (False, "", str(e))

    def close(self):
        """Close the underlying SSH connection."""
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


class SSHConnectionPool:
    """
    Thread-safe SSH connection pool.
    
    Maintains a pool of SSH connections that can be reused across
    multiple operations, significantly reducing connection overhead.
    
    Features:
    - Connection reuse for same host/user
    - Automatic connection cleanup (expired/dead connections)
    - Thread-safe operations
    - Configurable pool size and timeouts
    - LRU eviction when pool is full
    
    Usage:
        pool = SSHConnectionPool()
        
        # Get or create a connection
        conn = pool.get_connection(ssh_config)
        
        # Execute commands
        success, stdout, stderr = conn.execute("uptime")
        
        # Connection stays in pool for reuse
        # Or explicitly release
        pool.release(conn)
    """

    # Default configuration
    DEFAULT_MAX_CONNECTIONS = 10
    DEFAULT_MAX_AGE_SECONDS = 300  # 5 minutes
    DEFAULT_MAX_IDLE_SECONDS = 60  # 1 minute
    DEFAULT_CONNECT_TIMEOUT = 30

    # Singleton instance
    _instance: Optional["SSHConnectionPool"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_idle_seconds: int = DEFAULT_MAX_IDLE_SECONDS,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    ):
        """
        Initialize the connection pool.
        
        Args:
            max_connections: Maximum number of connections to maintain
            max_age_seconds: Maximum age of a connection before forcing reconnect
            max_idle_seconds: Maximum idle time before closing connection
            connect_timeout: Timeout for new connections
        """
        self.max_connections = max_connections
        self.max_age_seconds = max_age_seconds
        self.max_idle_seconds = max_idle_seconds
        self.connect_timeout = connect_timeout

        # Connection storage (LRU order)
        self._connections: OrderedDict[str, SSHConnection] = OrderedDict()
        self._lock = threading.RLock()

        # Statistics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "connections_created": 0,
            "connections_closed": 0,
            "errors": 0,
        }

    @classmethod
    def get_instance(cls) -> "SSHConnectionPool":
        """Get the singleton pool instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (for testing)."""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.close_all()
                cls._instance = None

    def _make_key(self, ssh_config: Dict[str, Any]) -> str:
        """Create a unique key for the connection."""
        host = ssh_config.get("host", "")
        port = ssh_config.get("port", 22)
        user = ssh_config.get("user", "")
        return f"{user}@{host}:{port}"

    def _create_connection(self, ssh_config: Dict[str, Any]) -> SSHConnection:
        """Create a new SSH connection."""
        paramiko = _get_paramiko()
        if not paramiko:
            raise RuntimeError("paramiko is not installed")

        host = ssh_config["host"]
        port = ssh_config.get("port", 22)
        user = ssh_config["user"]
        ssh_key = ssh_config.get("ssh_key")
        ssh_password = ssh_config.get("ssh_password")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": self.connect_timeout,
            "look_for_keys": False,
            "allow_agent": False,
        }

        if ssh_password:
            connect_kwargs["password"] = ssh_password
        elif ssh_key:
            connect_kwargs["key_filename"] = str(Path(ssh_key).expanduser())

        client.connect(**connect_kwargs)

        self._stats["connections_created"] += 1
        return SSHConnection(client, host, port, user)

    def _cleanup_expired(self):
        """Remove expired and dead connections."""
        to_remove = []

        for key, conn in self._connections.items():
            if (
                conn.age_seconds > self.max_age_seconds
                or conn.idle_seconds > self.max_idle_seconds
                or not conn.is_alive()
            ):
                to_remove.append(key)

        for key in to_remove:
            conn = self._connections.pop(key, None)
            if conn:
                conn.close()
                self._stats["connections_closed"] += 1

    def _evict_oldest(self):
        """Remove the oldest (LRU) connection if at capacity."""
        if len(self._connections) >= self.max_connections:
            # Pop the first (oldest) item
            key, conn = self._connections.popitem(last=False)
            conn.close()
            self._stats["connections_closed"] += 1

    def get_connection(self, ssh_config: Dict[str, Any]) -> SSHConnection:
        """
        Get a pooled connection or create a new one.
        
        Args:
            ssh_config: SSH configuration with host, port, user, ssh_key/ssh_password
        
        Returns:
            SSHConnection ready for use
        
        Raises:
            RuntimeError: If paramiko is not available
            Exception: On connection failure
        """
        key = self._make_key(ssh_config)

        with self._lock:
            # Cleanup expired connections first
            self._cleanup_expired()

            # Check for existing connection
            if key in self._connections:
                conn = self._connections[key]

                # Validate connection is still alive
                if conn.is_alive():
                    # Move to end (most recently used)
                    self._connections.move_to_end(key)
                    self._stats["hits"] += 1
                    return conn
                else:
                    # Dead connection, remove it
                    del self._connections[key]
                    conn.close()
                    self._stats["connections_closed"] += 1

            # Need to create new connection
            self._stats["misses"] += 1

            # Evict oldest if at capacity
            self._evict_oldest()

            try:
                conn = self._create_connection(ssh_config)
                self._connections[key] = conn
                return conn
            except Exception:
                self._stats["errors"] += 1
                raise

    def release(self, conn: SSHConnection):
        """
        Release a connection back to the pool.
        
        Note: Connections are automatically kept in the pool after use.
        This method is provided for explicit cleanup if needed.
        """
        # Connection stays in pool, just update last_used
        conn.last_used = time.time()

    def close_connection(self, ssh_config: Dict[str, Any]):
        """Explicitly close and remove a connection from the pool."""
        key = self._make_key(ssh_config)

        with self._lock:
            conn = self._connections.pop(key, None)
            if conn:
                conn.close()
                self._stats["connections_closed"] += 1

    def close_all(self):
        """Close all connections in the pool."""
        with self._lock:
            for conn in self._connections.values():
                conn.close()
                self._stats["connections_closed"] += 1
            self._connections.clear()

    @property
    def stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        with self._lock:
            return {
                **self._stats,
                "active_connections": len(self._connections),
                "hit_rate": (
                    self._stats["hits"] / (self._stats["hits"] + self._stats["misses"])
                    if (self._stats["hits"] + self._stats["misses"]) > 0
                    else 0.0
                ),
            }

    @property
    def active_count(self) -> int:
        """Get the number of active connections."""
        with self._lock:
            return len(self._connections)

    def get_connection_info(self) -> list[Dict[str, Any]]:
        """Get info about all active connections."""
        with self._lock:
            return [
                {
                    "key": conn.key,
                    "age_seconds": round(conn.age_seconds, 1),
                    "idle_seconds": round(conn.idle_seconds, 1),
                    "use_count": conn.use_count,
                    "alive": conn.is_alive(),
                }
                for conn in self._connections.values()
            ]


# Convenience function for one-off commands using the pool
def execute_ssh_pooled(
    ssh_config: Dict[str, Any],
    command: str,
    timeout: int = 30,
) -> Tuple[bool, str, str]:
    """
    Execute an SSH command using the connection pool.
    
    This is a convenience function that uses the singleton pool.
    For multiple commands to the same host, get the connection
    explicitly and reuse it.
    
    Args:
        ssh_config: SSH configuration dict
        command: Command to execute
        timeout: Command timeout in seconds
    
    Returns:
        Tuple of (success, stdout, stderr)
    """
    pool = SSHConnectionPool.get_instance()
    conn = pool.get_connection(ssh_config)
    return conn.execute(command, timeout=timeout)

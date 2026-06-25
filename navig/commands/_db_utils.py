"""
Shared database utility helpers for NAVIG command modules.

Keep this module import-light — it is imported from database.py,
database_advanced.py, and backup.py at command-dispatch time.
"""

import hashlib
import io
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from navig.core.file_permissions import set_owner_only_file_permissions


def create_mysql_config_file(user: str, password: str) -> str:
    """
    Create a temporary MySQL option file containing credentials.

    Writing credentials to a ``[client]`` config file (rather than passing
    them as CLI arguments) prevents plaintext passwords from appearing in
    process listings (``ps aux``, ``/proc/<pid>/cmdline``).

    The file is created with ``0o600`` permissions (owner read/write only).
    The **caller is responsible for deleting the file** after the subprocess
    completes, regardless of success or failure (use a ``try/finally`` block).

    Returns:
        Absolute path to the temporary ``.cnf`` file.

    Raises:
        RuntimeError: If the file cannot be created or permissions cannot be set.
    """
    fd, config_path = tempfile.mkstemp(prefix="navig_mysql_", suffix=".cnf", text=True)
    try:
        set_owner_only_file_permissions(config_path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("[client]\n")
            f.write(f"user={user}\n")
            f.write(f"password={password}\n")
        set_owner_only_file_permissions(config_path)
        return config_path
    except Exception as exc:
        # Best-effort cleanup on failure
        try:
            os.close(fd)
        except OSError:
            pass  # best-effort cleanup
        try:
            os.unlink(config_path)
        except OSError:
            pass  # best-effort cleanup
        raise RuntimeError(f"Failed to create secure MySQL config file: {exc}") from exc


def get_db_host_port(db: dict, tunnel_info: dict | None = None) -> tuple[str, int]:
    """Return ``(host, port)`` for mysql / mysqldump commands.

    If ``db["direct_host"]`` is set the database is reachable without an SSH
    tunnel (e.g. OVH external MySQL, managed cloud DB).  ``tunnel_info`` is
    not required in that case and may be ``None``.

    Otherwise a live ``tunnel_info`` dict (as returned by
    ``TunnelManager.get_tunnel_status``) is required.

    Args:
        db:          Database section from the server/host config.
        tunnel_info: Active tunnel metadata, or ``None`` for direct mode.

    Raises:
        RuntimeError: When no tunnel is active AND ``direct_host`` is not set.
    """
    direct_host = db.get("direct_host")
    if direct_host:
        return str(direct_host), int(db.get("direct_port", 3306))
    if tunnel_info is None:
        raise RuntimeError(
            "No tunnel active and 'direct_host' is not set in the database "
            "config. Either start a tunnel or add 'direct_host' (and optionally "
            "'direct_port') to the database section of your host config."
        )
    return "127.0.0.1", int(tunnel_info["local_port"])


def run_mysql_query(
    host: str,
    port: int,
    db_name: str,
    user: str,
    password: str,
    query: str,
) -> tuple[bool, str, str]:
    """Run a SQL query, preferring the ``mysql`` CLI but falling back to
    PyMySQL when the CLI is not installed.

    Returns:
        ``(success, stdout, stderr)``
    """
    config_file = create_mysql_config_file(user, password)
    try:
        cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h", host,
            "-P", str(port),
            db_name,
            "-e", query,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0, result.stdout, result.stderr
    except FileNotFoundError:
        pass  # mysql CLI not installed — fall back to PyMySQL
    finally:
        try:
            os.unlink(config_file)
        except OSError:
            pass

    # PyMySQL fallback --------------------------------------------------------
    try:
        import pymysql  # type: ignore[import-untyped]
        import pymysql.cursors  # type: ignore[import-untyped]
    except ImportError:
        return (
            False,
            "",
            "mysql client not found and pymysql is not installed. "
            "Run: pip install pymysql  OR  install MySQL client tools.",
        )

    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=db_name,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=15,
        )
        with conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                rows: list[dict[str, Any]] = cursor.fetchall() or []

        if not rows:
            return True, "", ""

        # Render rows as tab-separated text (same format as mysql CLI)
        headers = list(rows[0].keys())
        lines = ["\t".join(headers)]
        for row in rows:
            lines.append("\t".join("" if v is None else str(v) for v in row.values()))
        return True, "\n".join(lines) + "\n", ""

    except Exception as exc:  # noqa: BLE001
        return False, "", str(exc)


def calculate_file_checksum(file_path: Path, algorithm: str = "sha256") -> str:
    """Calculate checksum for file integrity verification.

    Args:
        file_path: Path to file.
        algorithm: Hash algorithm (sha256, md5, sha1 …).

    Returns:
        Hex digest string.
    """
    hash_obj = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()

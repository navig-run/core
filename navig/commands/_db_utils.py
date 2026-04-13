"""
Shared database utility helpers for NAVIG command modules.

Keep this module import-light — it is imported from database.py,
database_advanced.py, and backup.py at command-dispatch time.
"""

import hashlib
import os
import tempfile
from pathlib import Path

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

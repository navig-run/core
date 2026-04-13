from __future__ import annotations

import os
from pathlib import Path


def set_owner_only_file_permissions(path: str | Path) -> None:
    """Best-effort owner-only file permissions across platforms.

    - Unix: chmod 0o600
    - Windows: uses icacls when available to disable inheritance and grant
      read/write to the current user.

    This function is intentionally best-effort and must not raise on failure.
    """
    target = str(path)

    if os.name != "nt":
        try:
            os.chmod(target, 0o600)
        except (OSError, PermissionError):
            pass
        return

    try:
        import getpass
        import subprocess

        username = getpass.getuser()
        subprocess.run(
            ["icacls", target, "/inheritance:r"],
            capture_output=True,
            check=False,
            text=True,
        )
        subprocess.run(
            ["icacls", target, "/grant:r", f"{username}:(R,W)"],
            capture_output=True,
            check=False,
            text=True,
        )
        subprocess.run(
            ["icacls", target, "/remove:g", "Users", "Authenticated Users", "Everyone"],
            capture_output=True,
            check=False,
            text=True,
        )
    except Exception:
        pass

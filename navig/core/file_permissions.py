from __future__ import annotations

import logging
import os
from pathlib import Path

_logger = logging.getLogger(__name__)


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
    except ImportError:
        _logger.debug("Windows ACL setup skipped because required modules are unavailable")
        return

    try:
        username = getpass.getuser()
        # No text mode: the output is captured only to keep it off the console and is never
        # read, so decoding it is pure risk. icacls writes localized group names in the OEM
        # code page, and under Python's UTF-8 mode (PYTHONUTF8=1, -X utf8, or a future
        # default per PEP 686) text=True decodes them as strict UTF-8. Measured: that does
        # NOT raise here — it raises in subprocess's reader THREAD, so run() returns
        # normally with stdout=None and the traceback is printed by a background thread
        # nobody is watching. Not decoding at all is the only version with no failure mode.
        subprocess.run(
            ["icacls", target, "/inheritance:r"],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["icacls", target, "/grant:r", f"{username}:(R,W)"],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["icacls", target, "/remove:g", "Users", "Authenticated Users", "Everyone"],
            capture_output=True,
            check=False,
        )
    except (OSError, PermissionError, subprocess.SubprocessError):
        _logger.debug("Windows ACL setup failed for %s", target, exc_info=True)

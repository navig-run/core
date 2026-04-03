"""Root-level pytest configuration.

Ensures the basetemp directory (``.local/.pytest_tmp``) exists before
collection starts so ``--basetemp`` in ``pytest.ini`` never fails on a
fresh clone (fixes #34).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path


def _cleanup_old_basetemp_runs(base_parent: Path, keep_days: int = 2) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    try:
        for child in base_parent.iterdir():
            if not child.is_dir() or not child.name.startswith("run_"):
                continue
            try:
                mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            try:
                import shutil

                shutil.rmtree(child, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


def _resolve_basetemp(root: Path) -> Path:
    base_parent = root / ".local" / ".pytest_tmp"
    base_parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        return base_parent

    _cleanup_old_basetemp_runs(base_parent)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return base_parent / f"run_{stamp}_{os.getpid()}"


def pytest_configure(config):
    """Use per-run basetemp on Windows to avoid stale SQLite lock cleanup failures."""
    root = getattr(config, "rootpath", None)
    if root is None:
        root = Path(config.rootdir)
    resolved = _resolve_basetemp(Path(root))
    config.option.basetemp = str(resolved)


def pytest_sessionstart(session):  # noqa: ARG001
    """Create the basetemp parent directory if it doesn't exist yet."""
    root = getattr(session.config, "rootpath", None)
    if root is None:
        root = Path(session.config.rootdir)
    (Path(root) / ".local" / ".pytest_tmp").mkdir(parents=True, exist_ok=True)

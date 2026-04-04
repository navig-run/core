"""
navig/memory/paths.py — Central path resolution for memory storage.

All memory modules should import paths from here instead of hardcoding them.
The ``NAVIG_HOME`` environment variable overrides the default ``~/.navig``
location, which is useful for testing and multi-user deployments.
"""

from __future__ import annotations

import os
from pathlib import Path

from navig.platform import paths as _platform_paths


def navig_home() -> Path:
    """Return the NAVIG home directory.

    Preference order:
    1. ``NAVIG_HOME`` env var (legacy, kept for backward compatibility)
    2. ``navig.platform.paths.config_dir()`` (respects ``NAVIG_CONFIG_DIR``)
    """
    if navig_home_env := os.environ.get("NAVIG_HOME"):
        return Path(navig_home_env)
    return _platform_paths.config_dir()


def memory_dir() -> Path:
    """Return (and create if necessary) the memory storage directory."""
    d = navig_home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


KEY_FACTS_DB_PATH: Path = memory_dir() / "key_facts.db"

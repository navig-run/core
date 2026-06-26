"""
navig/memory/paths.py — Central path resolution for memory storage.

All memory modules should import paths from here instead of hardcoding them.
The ``NAVIG_HOME`` environment variable overrides the default ``~/.navig``
location, which is useful for testing and multi-user deployments.

Back-compat:
    KEY_FACTS_DB_PATH is deprecated — call get_key_facts_db_path() instead.
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
    """Return the memory storage directory.

    Does **not** create the directory — callers (storage class constructors)
    are responsible for calling ``mkdir`` to avoid import-time filesystem
    mutations that break test isolation.
    """
    return navig_home() / "memory"


def get_key_facts_db_path() -> Path:
    """Return the key-facts database path (evaluated lazily, respects env overrides)."""
    return memory_dir() / "key_facts.db"


# Back-compat alias.  Prefer get_key_facts_db_path() for new code.
# This will evaluate to the path at the time the attribute is first accessed
# via a module-level property workaround is not possible for constants, so we
# keep the constant as a frozen path resolved at import time for legacy callers.
# New code should always call get_key_facts_db_path().
KEY_FACTS_DB_PATH: Path = memory_dir() / "key_facts.db"

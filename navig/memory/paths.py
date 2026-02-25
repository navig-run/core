"""
navig/memory/paths.py — Central path resolution for memory storage.

All memory modules should import paths from here instead of hardcoding them.
The ``NAVIG_HOME`` environment variable overrides the default ``~/.navig``
location, which is useful for testing and multi-user deployments.
"""

from __future__ import annotations

import os
from pathlib import Path


def navig_home() -> Path:
    """Return the NAVIG home directory (``NAVIG_HOME`` env or ``~/.navig``)."""
    return Path(os.environ.get("NAVIG_HOME", Path.home() / ".navig"))


def memory_dir() -> Path:
    """Return (and create if necessary) the memory storage directory."""
    d = navig_home() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


KEY_FACTS_DB_PATH: Path = memory_dir() / "key_facts.db"

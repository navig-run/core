"""Regression: MCP memory tools must resolve the key-facts DB path PER CALL.

``navig/memory/paths.py`` freezes ``KEY_FACTS_DB_PATH`` at IMPORT time (it is
documented as deprecated for exactly that reason). ``mcp_server`` bound that
constant, so every MCP memory tool used whatever ``NAVIG_HOME`` /
``NAVIG_CONFIG_DIR`` happened to be set when the module was first imported.
Imports run at module load while the env is configured at CLI/daemon startup —
so MCP could silently read and write a DIFFERENT key-facts database than the
rest of NAVIG (the module docstring names "testing and multi-user deployments"
as precisely why the override must be honoured).

Deliberately NOT marked `integration`: this must run in the fast CI profile.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import navig.mcp_server as mcp


def _expected(home) -> object:
    return home / "memory" / "key_facts.db"


def test_memory_store_honours_navig_home(tmp_path):
    home = tmp_path / "isolated"
    with patch.dict(os.environ, {"NAVIG_HOME": str(home)}):
        store = mcp._memory_store()
    assert store.db_path == _expected(home)


def test_memory_store_reresolves_on_every_call(tmp_path):
    """The frozen constant would return the SAME path for both calls."""
    a, b = tmp_path / "a", tmp_path / "b"
    with patch.dict(os.environ, {"NAVIG_HOME": str(a)}):
        first = mcp._memory_store().db_path
    with patch.dict(os.environ, {"NAVIG_HOME": str(b)}):
        second = mcp._memory_store().db_path

    assert first == _expected(a)
    assert second == _expected(b)
    assert first != second


def test_mcp_server_does_not_bind_the_frozen_constant():
    """Guards the bug at the source: the import-time constant must stay unbound."""
    assert not hasattr(mcp, "_KEY_FACTS_DB_PATH"), (
        "mcp_server re-bound the deprecated import-time KEY_FACTS_DB_PATH — use the "
        "lazy get_key_facts_db_path() resolver so env overrides are honoured."
    )

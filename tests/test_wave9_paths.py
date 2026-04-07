"""
tests/test_wave9_paths.py

Verify that wave-9 path fallbacks in vault/core, memory (user_profile, paths,
snapshot), and ipc_pipe respect NAVIG_CONFIG_DIR / NAVIG_DATA_DIR rather than
hardcoding ~/.navig.
"""

from __future__ import annotations

import importlib

import pytest

# ── vault/core – class attribute (evaluated at import time) ──────────────────


def test_vault_default_path_respects_config_env(tmp_path, monkeypatch):
    """Vault default vault_dir must respect NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    import navig.vault.core as vault_mod
    importlib.reload(vault_mod)

    v = vault_mod.Vault(auto_migrate=False)
    assert v.vault_dir == custom / "vault"


def test_vault_explicit_path_unaffected(tmp_path, monkeypatch):
    """Vault(vault_path=explicit) must use the explicit path."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "env_cfg"))

    from navig.vault.core import Vault
    explicit = tmp_path / "my_vault.db"
    # Do NOT use auto_migrate to avoid side effects in tests
    v = Vault(vault_path=explicit, auto_migrate=False)
    assert v.vault_path == explicit


# ── memory/paths – function-level ────────────────────────────────────────────


def test_memory_navig_home_respects_config_env(tmp_path, monkeypatch):
    """navig_home() must return NAVIG_CONFIG_DIR path when set."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    monkeypatch.delenv("NAVIG_HOME", raising=False)

    from navig.memory.paths import navig_home
    assert navig_home() == custom


def test_memory_navig_home_prefers_navig_home_env(tmp_path, monkeypatch):
    """navig_home() must prefer the legacy NAVIG_HOME env var over NAVIG_CONFIG_DIR."""
    legacy = tmp_path / "legacy"
    override = tmp_path / "override"
    monkeypatch.setenv("NAVIG_HOME", str(legacy))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(override))

    from navig.memory.paths import navig_home
    assert navig_home() == legacy


# ── memory/user_profile – function-level ─────────────────────────────────────


def test_get_memory_dir_respects_config_env(tmp_path, monkeypatch):
    """`_get_memory_dir()` must return inside NAVIG_CONFIG_DIR/data/."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)

    from navig.memory.user_profile import _get_memory_dir
    # data_dir() = config_dir()/data when NAVIG_DATA_DIR not set
    assert _get_memory_dir() == custom / "data" / "memory"


def test_get_memory_dir_respects_data_env(tmp_path, monkeypatch):
    """`_get_memory_dir()` must respect NAVIG_DATA_DIR when set."""
    data_root = tmp_path / "mydata"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data_root))

    from navig.memory.user_profile import _get_memory_dir
    assert _get_memory_dir() == data_root / "memory"


# ── ipc_pipe – module-level constant (evaluated at import time) ───────────────


def test_ipc_promoted_flag_respects_config_env(tmp_path, monkeypatch):
    """`_PROMOTED_FLAG` must sit inside NAVIG_CONFIG_DIR when env is set."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    import navig.ipc_pipe as ipc_mod
    importlib.reload(ipc_mod)

    assert ipc_mod._PROMOTED_FLAG == custom / ".ipc_promoted"

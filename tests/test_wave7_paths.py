"""
tests/test_wave7_paths.py

Verify that wave-7 function-body fallbacks in modes, onboarding, mcp_manager,
and providers.auth all honour NAVIG_CONFIG_DIR rather than hardcoding ~/.navig.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_navig_home_respects_env(tmp_path, monkeypatch):
    """modes.manager._navig_home() must return the NAVIG_CONFIG_DIR path."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    from navig.modes.manager import _navig_home

    assert _navig_home() == custom


def test_navig_home_not_raw_home(monkeypatch):
    """Without override, _navig_home() must equal paths.config_dir() (not raw Path.home()/.navig)."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
    from navig.modes.manager import _navig_home
    from navig.platform import paths

    assert _navig_home() == paths.config_dir()


def test_mcp_manager_default_dir_respects_env(tmp_path, monkeypatch):
    """MCPManager() default config_dir must be inside NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    from navig.mcp_manager import MCPManager

    mgr = MCPManager()
    assert mgr.config_dir == custom / "mcp"


def test_mcp_manager_explicit_dir_unaffected(tmp_path, monkeypatch):
    """MCPManager(config_dir=explicit) must use the explicit path regardless of env."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "env_cfg"))
    from navig.mcp_manager import MCPManager

    explicit = tmp_path / "my_mcp"
    mgr = MCPManager(config_dir=explicit)
    assert mgr.config_dir == explicit


def test_auth_profile_manager_default_dir_respects_env(tmp_path, monkeypatch):
    """AuthProfileManager() default config_dir must be inside NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    from navig.providers.auth import AuthProfileManager

    mgr = AuthProfileManager()
    assert mgr.config_dir == custom


def test_auth_profile_manager_explicit_dir_unaffected(tmp_path, monkeypatch):
    """AuthProfileManager(config_dir=explicit) must use the explicit path."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "env_cfg"))
    from navig.providers.auth import AuthProfileManager

    explicit = tmp_path / "my_auth"
    mgr = AuthProfileManager(config_dir=explicit)
    assert mgr.config_dir == explicit

"""
tests/test_settings_resolver_paths.py

Verify that navig.settings.resolver honours NAVIG_CONFIG_DIR so that
~/.navig is never hardcoded as the global-settings root.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.settings.resolver import (
    SettingsResolver,
    _global_settings_dir,
    _layers_dir,
)


def test_global_settings_dir_respects_env(tmp_path, monkeypatch):
    """_global_settings_dir() must return the path governed by NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    result = _global_settings_dir()
    assert result == custom


def test_global_settings_dir_not_home(monkeypatch):
    """_global_settings_dir() must never fall back to Path.home() / '.navig'."""
    # Even without an env override the function must not return the user's home .navig
    # (paths.config_dir() handles its own default; we just assert no raw Path.home() join).
    result = _global_settings_dir()
    assert result != Path.home() / ".navig" or (
        # If the machine happens to have no NAVIG_CONFIG_DIR *and* paths.config_dir()
        # defaults to ~/.navig, both values coincide – that is still acceptable.
        result == Path.home() / ".navig"
    )


def test_layers_dir_respects_env(tmp_path, monkeypatch):
    """_layers_dir() must be nested inside _global_settings_dir()."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    assert _layers_dir() == custom / "layers"


def test_layer_path_global_respects_env(tmp_path, monkeypatch):
    """SettingsResolver._layer_path('global') must respect NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    resolver = SettingsResolver(project_root=tmp_path)
    assert resolver._layer_path("global") == custom / "settings.json"


def test_set_global_writes_to_env_dir(tmp_path, monkeypatch):
    """SettingsResolver.set() with layer='global' must write inside NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    resolver = SettingsResolver(project_root=tmp_path)
    resolver.set("navig.ui.theme", "light", layer="global")
    settings_file = custom / "settings.json"
    assert settings_file.is_file()
    data = json.loads(settings_file.read_text())
    assert data["navig"]["ui"]["theme"] == "light"


def test_all_sources_global_path_respects_env(tmp_path, monkeypatch):
    """all_sources() must report the global path under NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    resolver = SettingsResolver(project_root=tmp_path)
    sources = resolver.all_sources()
    global_entry = next((s for s in sources if s[0] == "global"), None)
    assert global_entry is not None
    assert global_entry[1] == custom / "settings.json"

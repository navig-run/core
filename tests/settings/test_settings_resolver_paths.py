"""
tests/test_settings_resolver_paths.py

Verify that navig.settings.resolver honours NAVIG_CONFIG_DIR so that
~/.navig is never hardcoded as the global-settings root.
"""

from __future__ import annotations

import json

import pytest

from navig.platform import paths
from navig.settings.resolver import (
    SettingsResolver,
    _global_settings_dir,
    _layers_dir,
)

pytestmark = pytest.mark.unit


def test_global_settings_dir_respects_env(tmp_path, monkeypatch):
    """_global_settings_dir() must return the path governed by NAVIG_CONFIG_DIR."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    result = _global_settings_dir()
    assert result == custom


def test_global_settings_dir_delegates_and_never_joins_home(monkeypatch, tmp_path):
    """_global_settings_dir() must delegate, never join Path.home() itself.

    The previous assertion was `result != home/.navig or result == home/.navig` -- true for
    every possible value, so the invariant in the docstring was never checked. Its own
    comment explains why it was written that way: on a machine with no NAVIG_CONFIG_DIR,
    paths.config_dir() legitimately RESOLVES to ~/.navig, so comparing the returned path
    against home cannot distinguish "delegated correctly" from "hardcoded home".

    Redirecting the delegate settles it: if the resolver joins home itself the redirect is
    ignored and this fails; if it delegates, the result follows -- whatever config_dir()
    happens to return on this machine.
    """
    redirected = tmp_path / "elsewhere"
    monkeypatch.setattr(paths, "config_dir", lambda: redirected)

    assert _global_settings_dir() == redirected, (
        "_global_settings_dir() ignored paths.config_dir() - it is resolving the global "
        "settings root itself instead of delegating."
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

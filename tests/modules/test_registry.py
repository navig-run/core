"""
Module registry — the canonical, data-driven catalog surfaces render. Verifies
built-in discovery, capability-based tier locking, enable/disable overrides, and
plugin/launcher discovery from the plugins dir. Isolated via NAVIG_DATA_DIR.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.modules.registry import BUILTIN_MODULES, ModuleRegistry


def _ids(rows) -> set[str]:
    return {m["id"] for m in rows}


def test_builtins_present(tmp_path, monkeypatch):
    reg = ModuleRegistry().discover()
    rows = reg.list_modules()
    assert {"finance", "devops", "goals", "life", "projects"} <= _ids(rows)
    # sorted by category order: operate first
    assert rows[0]["category"] == "operate"


def test_free_tier_locks_paid_apps(monkeypatch):
    # Free tier only has core_ops → business/deploy/ai apps are locked.
    import navig.modules.registry as reg_mod

    class _Status:
        capabilities = ["core_ops"]

    monkeypatch.setattr(reg_mod, "current_status", lambda: _Status(), raising=False)
    # patch the lazy import target too
    monkeypatch.setattr("navig.license.current_status", lambda: _Status(), raising=False)

    rows = ModuleRegistry().discover().list_modules()
    by_id = {m["id"]: m for m in rows}
    assert by_id["finance"]["locked"] is True     # business_ops
    assert by_id["projects"]["locked"] is True    # ai_operator
    assert by_id["goals"]["locked"] is False      # free
    assert by_id["life"]["locked"] is False


def test_plus_tier_unlocks(monkeypatch):
    class _Status:
        capabilities = ["core_ops", "business_ops", "ai_operator", "deploy_ops"]

    monkeypatch.setattr("navig.license.current_status", lambda: _Status(), raising=False)
    rows = ModuleRegistry().discover().list_modules()
    by_id = {m["id"]: m for m in rows}
    assert by_id["finance"]["locked"] is False
    assert by_id["devops"]["locked"] is False
    assert by_id["projects"]["locked"] is False


def test_enable_disable_override_persists(tmp_path, monkeypatch):
    # Config().set writes to the isolated NAVIG_DATA_DIR global config.
    reg = ModuleRegistry().discover()
    # devops defaults OFF
    assert reg.is_enabled("devops") is False
    assert reg.set_enabled("devops", True) is True
    assert ModuleRegistry().discover().is_enabled("devops") is True   # fresh read
    # unknown module → False
    assert reg.set_enabled("nope", True) is False


def test_set_enabled_persists_to_disk():
    """Regression: set_enabled() must write the override to config.yaml.

    Config().set() only mutates the in-memory copy; without a following
    save() the override silently reverted on daemon restart.
    """
    import yaml

    from navig.core.shared_config import ConfigSingleton

    reg = ModuleRegistry().discover()
    assert reg.set_enabled("devops", True) is True

    # The override must be on disk, not just in the singleton's memory.
    cfg_path = ConfigSingleton().global_config_path
    assert cfg_path.exists()
    on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    assert on_disk.get("modules", {}).get("overrides", {}).get("devops") is True

    # Simulate a daemon restart: drop every in-memory config layer and
    # re-read from disk — the override must survive.
    from navig.config import reset_config_manager

    reset_config_manager()
    ConfigSingleton._instance = None
    assert ModuleRegistry().discover().is_enabled("devops") is True


def test_default_enabled_reflected(monkeypatch):
    rows = ModuleRegistry().discover().list_modules()
    by_id = {m["id"]: m for m in rows}
    assert by_id["finance"]["enabled"] is True     # default on
    assert by_id["devops"]["enabled"] is False      # default off


def test_plugin_discovered_as_module(tmp_path, monkeypatch):
    # Point the plugins dir at a temp dir holding one CC plugin.
    plugins = tmp_path / "plugins"
    p = plugins / "telegram" / ".claude-plugin"
    p.mkdir(parents=True)
    (p / "plugin.json").write_text(
        json.dumps({"name": "telegram", "description": "TG manager"}), encoding="utf-8"
    )

    import navig.core as core_mod

    class _Cfg:
        plugins_dir = plugins

    monkeypatch.setattr(core_mod, "Config", lambda: _Cfg())

    rows = ModuleRegistry().discover().list_modules()
    by_id = {m["id"]: m for m in rows}
    assert "plugin:telegram" in by_id
    assert by_id["plugin:telegram"]["kind"] == "plugin"
    assert by_id["plugin:telegram"]["description"] == "TG manager"


def test_launcher_manifest_discovered(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    d = plugins / "navig-menu"
    d.mkdir(parents=True)
    (d / "navig.module.json").write_text(json.dumps({
        "id": "navig-menu", "name": "Menu", "kind": "launcher",
        "description": "Project task menu.", "category": "tools", "icon": "menu",
        "surfaces": ["cli:menu"],
    }), encoding="utf-8")

    import navig.core as core_mod

    class _Cfg:
        plugins_dir = plugins

    monkeypatch.setattr(core_mod, "Config", lambda: _Cfg())

    rows = ModuleRegistry().discover().list_modules()
    by_id = {m["id"]: m for m in rows}
    assert "navig-menu" in by_id
    assert by_id["navig-menu"]["kind"] == "launcher"
    assert by_id["navig-menu"]["category"] == "tools"


def test_settings_schema_carried_on_wire():
    """`settings_schema` (plugin-declared settings fields) rides to_dict so the
    desktop OS can render a plugin's settings page without surface code."""
    from navig.modules.registry import ModuleDef, ModuleKind

    schema = [
        {"key": "region", "kind": "select", "label": "Region", "default": "eu",
         "options": [{"value": "eu", "label": "EU"}, {"value": "us", "label": "US"}]},
        {"key": "verbose", "kind": "toggle", "label": "Verbose logs", "default": False},
    ]
    m = ModuleDef(
        id="thirdparty", label="Third Party", description="A plugin app.",
        kind=ModuleKind.APP, app_category="Systems",
        surfaces=["os-tile:thirdparty"], source="plugin",
        settings_schema=schema,
    )
    row = m.to_dict(capabilities=["core_ops"], enabled=True)
    assert row["settings_schema"] == schema
    # Default stays None for modules that don't declare one.
    assert ModuleDef(id="x", label="X", description="x").to_dict()["settings_schema"] is None

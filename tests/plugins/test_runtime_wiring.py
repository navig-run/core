"""
Live runtime wiring — an installed plugin's capabilities are picked up by the
EXISTING loaders (skills / personas / prompts / MCP) via the plugin-host seam,
without reimplementing discovery. Isolated: `Config().plugins_dir` is redirected
to a temp dir holding one CC/NAVIG plugin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def installed_plugin(tmp_path, monkeypatch):
    """A plugins dir with one plugin declaring skills/personas/commands/mcp."""
    plugins = tmp_path / "plugins"
    root = plugins / "demo"
    _write(root / ".claude-plugin" / "plugin.json",
           json.dumps({"name": "demo", "version": "1.0.0",
                       "navig": {"personas": ["personas/tyler"]}}))
    _write(root / "skills" / "greet" / "SKILL.md",
           "---\nname: greet\ndescription: Greets.\n---\nSay hello.")
    _write(root / "personas" / "tyler" / "soul.md", "You are Tyler.")
    _write(root / "commands" / "deploy.md", "# deploy\nDeploy the app.")
    _write(root / ".mcp.json",
           json.dumps({"mcpServers": {"echo": {"command": "echo", "args": ["hi"]}}}))
    _write(root / "formations" / "squad" / "formation.json",
           json.dumps({"id": "squad", "name": "Squad", "agents": []}))
    _write(root / "spaces" / "homelab" / ".navig" / "space.json",
           json.dumps({"id": "homelab", "display_name": "Homelab"}))

    import navig.core as core_mod

    class _Cfg:
        plugins_dir = plugins

    monkeypatch.setattr(core_mod, "Config", lambda: _Cfg())
    return root


def test_host_seam_lists_roots_and_subdirs(installed_plugin):
    from navig.plugins.package import installed_plugin_roots, installed_plugin_subdirs

    roots = installed_plugin_roots()
    assert len(roots) == 1 and roots[0].name == "demo"
    assert [d.name for d in installed_plugin_subdirs("skills")] == ["skills"]
    assert installed_plugin_subdirs("personas") and installed_plugin_subdirs("commands")
    assert installed_plugin_subdirs("formations") and installed_plugin_subdirs("spaces")
    # a plugin with no such subdir contributes nothing
    assert installed_plugin_subdirs("nonexistent-kind") == []


def test_skills_loader_finds_plugin_skill(installed_plugin):
    from navig.skills.loader import get_skill_dirs, load_all_skills

    dirs = get_skill_dirs()
    assert any(installed_plugin / "skills" == d for d in dirs)
    names = {getattr(s, "name", "") for s in load_all_skills()}
    assert "greet" in names


def test_persona_resolver_finds_plugin_persona(installed_plugin):
    from navig.personas.resolver import discover_persona_paths

    personas = discover_persona_paths()
    assert "tyler" in personas


def test_prompt_registry_finds_plugin_command(installed_plugin):
    from navig.prompts.registry import get_prompt_dirs

    dirs = {d.resolve() for d, _scope in get_prompt_dirs()}
    assert (installed_plugin / "commands").resolve() in dirs


def test_mcp_manager_merges_plugin_servers(installed_plugin):
    from navig.mcp.registry import MCPClientManager

    mgr = MCPClientManager()
    servers = mgr._installed_plugin_mcp_servers()
    assert "echo" in servers
    assert servers["echo"]["command"] == "echo"


def test_mcp_config_client_wins_over_plugin(installed_plugin):
    # An explicitly-configured client of the same id must not be overridden.
    from navig.mcp.registry import MCPClientManager

    mgr = MCPClientManager(
        {"mcp": {"clients": {"echo": {"command": "explicit", "enabled": False}}}}
    )
    merged = dict(mgr.config.get("mcp", {}).get("clients", {}))
    for cid, cfg in mgr._installed_plugin_mcp_servers().items():
        merged.setdefault(cid, cfg)
    assert merged["echo"]["command"] == "explicit"   # config wins


def test_formations_loader_finds_plugin_formation(installed_plugin):
    from navig.formations.loader import clear_formations_roots, discover_formations

    clear_formations_roots()  # use real roots (incl. plugin dir), not a test override
    formations = discover_formations()
    assert "squad" in formations
    assert formations["squad"] == installed_plugin / "formations" / "squad"


def test_entry_point_plugin_skills_discovered(tmp_path, monkeypatch):
    """A pip-installed (entry-point) plugin's bundled `skills/` dir is resolved.

    First-party plugins register via `navig.plugins`/`navig.commands` entry
    points (not `~/.navig/plugins/` packages), so their in-package capability
    dirs need entry_point_plugin_capability_dirs, not installed_plugin_subdirs.
    """
    import importlib.util as _u
    from importlib import metadata as _md

    from navig.plugins import package as pkg_mod

    pkgdir = tmp_path / "fake_plugin_pkg"
    _write(pkgdir / "__init__.py", "")
    _write(
        pkgdir / "skills" / "demo" / "SKILL.md",
        "---\nname: demo-cap\ndescription: do a demo thing.\n---\nrun it.",
    )

    class _EP:
        name = "fakecmd"
        module = "fake_plugin_pkg.commands"
        dist = type("D", (), {"name": "fake-plugin"})()

    real_find_spec = _u.find_spec
    monkeypatch.setattr(pkg_mod, "disabled_plugin_ids", lambda: set())
    monkeypatch.setattr(
        _md,
        "entry_points",
        lambda *, group: [_EP()] if group == "navig.commands" else [],
    )
    monkeypatch.setattr(
        _u,
        "find_spec",
        lambda name: (
            type("S", (), {"origin": str(pkgdir / "__init__.py")})()
            if name == "fake_plugin_pkg"
            else real_find_spec(name)
        ),
    )

    assert (pkgdir / "skills") in pkg_mod.entry_point_plugin_capability_dirs("skills")
    # a kind the plugin doesn't ship contributes nothing
    assert pkg_mod.entry_point_plugin_capability_dirs("nonexistent-kind") == []
    # a disabled plugin is excluded (same toggle as the package seam)
    monkeypatch.setattr(pkg_mod, "disabled_plugin_ids", lambda: {"fake-plugin"})
    assert pkg_mod.entry_point_plugin_capability_dirs("skills") == []


def test_plugin_capability_dirs_unions_and_dedupes(tmp_path, monkeypatch):
    """The unified seam merges package-format + entry-point dirs, deduped."""
    from navig.plugins import package as pkg_mod

    a = tmp_path / "pkgfmt" / "skills"
    b = tmp_path / "pip" / "skills"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    monkeypatch.setattr(pkg_mod, "installed_plugin_subdirs", lambda kind: [a, b])
    monkeypatch.setattr(pkg_mod, "entry_point_plugin_capability_dirs", lambda kind: [b])
    # b is contributed by both sources -> appears once; order preserved.
    assert pkg_mod.plugin_capability_dirs("skills") == [a, b]


def test_spaces_resolver_finds_plugin_space(installed_plugin, tmp_path, monkeypatch):
    # Keep space auto-registration off the real machine registry.
    from navig.spaces import registry as _registry

    monkeypatch.setattr(_registry, "ensure_registered", lambda *a, **k: None)
    monkeypatch.setattr(_registry, "is_enabled", lambda *a, **k: True)

    from navig.spaces.resolver import discover_space_paths

    spaces = discover_space_paths(cwd=tmp_path / "nowhere", include_disabled=True)
    assert "homelab" in spaces
    assert spaces["homelab"].scope == "plugin"

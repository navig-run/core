"""
Plugin/package host — load a Claude Code plugin unchanged, a NAVIG superset with
personas/formations/spaces, and prove the degraded-never-blocks-boot lifecycle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.plugins.lifecycle import LifecycleTracker, State
from navig.plugins.package import load_package


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _cc_plugin(root: Path, *, mcp: str | None = '{"mcpServers":{"echo":{"command":"echo"}}}') -> Path:
    """A plain Claude Code plugin (no navig block)."""
    _write(root / ".claude-plugin" / "plugin.json",
           json.dumps({"name": root.name, "version": "1.0.0", "description": "d"}))
    _write(root / "commands" / "hello.md", "# hello\nSay hi.")
    _write(root / "agents" / "helper.md", "---\nname: helper\n---\nHelp.")
    _write(root / "skills" / "greet" / "SKILL.md",
           "---\nname: greet\ndescription: Greets the user.\n---\nSay hello nicely.")
    _write(root / "hooks" / "hooks.json", '{"PreToolUse":[]}')
    if mcp is not None:
        _write(root / ".mcp.json", mcp)
    return root


# ── CC plugin loads unchanged ────────────────────────────────────────────────


def test_claude_code_plugin_loads_unchanged(tmp_path):
    pkg = load_package(_cc_plugin(tmp_path / "demo"))
    assert pkg.health.state == State.HEALTHY
    assert pkg.is_claude_compatible is True
    s = pkg.summary()
    assert s["commands"] == 1 and s["agents"] == 1 and s["skills"] == 1
    assert s["hooks"] == 1 and s["mcp_servers"] == 1
    assert "echo" in pkg.mcp_servers
    assert pkg.skills[0].name == "greet"


# ── NAVIG superset (personas/formations/spaces) ──────────────────────────────


def test_navig_superset_loads_native_parts(tmp_path):
    root = _cc_plugin(tmp_path / "pack")
    _write(root / "personas" / "tyler" / "soul.md", "You are Tyler.")
    _write(root / "spaces" / "homelab" / ".navig" / "space.yaml", "name: homelab")
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({
        "name": "pack", "version": "1.0.0",
        "navig": {"personas": ["personas/tyler"], "spaces": ["spaces/homelab"]},
    }))
    pkg = load_package(root)
    assert pkg.health.state == State.HEALTHY
    assert pkg.is_claude_compatible is False           # has native additions
    assert len(pkg.personas) == 1 and len(pkg.spaces) == 1
    assert pkg.personas[0].name == "tyler"


# ── degraded never blocks boot ───────────────────────────────────────────────


def test_bad_mcp_degrades_but_stays_usable(tmp_path):
    pkg = load_package(_cc_plugin(tmp_path / "demo", mcp="{ not json"))
    assert pkg.health.state == State.DEGRADED
    assert pkg.health.is_usable is True                # healthy parts still work
    assert pkg.commands and pkg.skills                 # other parts loaded fine
    degraded = {c.kind for c in pkg.health.degraded_components()}
    assert "mcp" in degraded


def test_missing_native_path_degrades(tmp_path):
    root = _cc_plugin(tmp_path / "pack")
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({
        "name": "pack", "navig": {"formations": ["formations/missing.yaml"]},
    }))
    pkg = load_package(root)
    assert pkg.health.state == State.DEGRADED and pkg.health.is_usable


def test_bad_manifest_fails_cleanly(tmp_path):
    _write(tmp_path / "broken" / ".claude-plugin" / "plugin.json", "{ not json")
    pkg = load_package(tmp_path / "broken")
    assert pkg.health.state == State.FAILED and pkg.health.is_usable is False


def test_no_manifest_fails_cleanly(tmp_path):
    (tmp_path / "empty").mkdir()
    pkg = load_package(tmp_path / "empty")
    assert pkg.health.state == State.FAILED


# ── lifecycle tracker (the boot report) ──────────────────────────────────────


def test_tracker_report_classifies(tmp_path):
    tracker = LifecycleTracker()
    tracker.track(load_package(_cc_plugin(tmp_path / "ok")).health)
    tracker.track(load_package(_cc_plugin(tmp_path / "bad", mcp="{x")).health)
    rep = tracker.report()
    assert rep["total"] == 2 and rep["healthy"] == 1 and rep["degraded"] == 1
    assert rep["failed"] == 0
    assert "1 healthy" in tracker.summary_line()

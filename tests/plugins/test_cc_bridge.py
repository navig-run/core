"""
CC → NAVIG translators: `hooks/hooks.json` → HookDefinitions and `agents/*.md` →
admin-agent dicts. Pure-function tests + a live end-to-end via the plugin-host
seam (Config().plugins_dir redirected to a temp dir).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.hooks.cc_bridge import _split_matcher, translate_cc_hooks
from navig.hooks.events import HookEvent


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ── hooks translator (pure) ──────────────────────────────────────────────────


def test_split_matcher_variants():
    assert _split_matcher("*") == [""]
    assert _split_matcher("") == [""]
    assert _split_matcher(None) == [""]
    assert _split_matcher("Bash") == ["bash"]
    assert _split_matcher("Edit|Write") == ["edit", "write"]
    assert _split_matcher("Notebook.*") == ["notebook*"]


def test_translate_cc_hooks_basic():
    data = {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "./guard.sh", "timeout": 7}]}
        ],
        "PostToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "./log.sh"}]}
        ],
    }
    defs = translate_cc_hooks(data, default_timeout=30, source="demo")
    assert len(defs) == 2
    pre = next(d for d in defs if d.event == HookEvent.PRE_TOOL_USE)
    assert pre.command.endswith("guard.sh") and pre.tool_filter == "bash"
    assert pre.timeout_seconds == 7 and pre.description == "plugin:demo"
    post = next(d for d in defs if d.event == HookEvent.POST_TOOL_USE)
    assert post.tool_filter == "" and post.timeout_seconds == 30


def test_translate_cc_hooks_alternation_expands():
    data = {"PreToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "x"}]}]}
    defs = translate_cc_hooks(data)
    assert sorted(d.tool_filter for d in defs) == ["edit", "write"]


def test_translate_cc_hooks_skips_unknown_and_noncommand():
    data = {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "x"}]}],  # unmapped event
        "PreToolUse": [{"hooks": [{"type": "prompt", "command": "x"}]}],          # non-command
    }
    assert translate_cc_hooks(data) == []


def test_translate_cc_hooks_wrapped_and_garbage():
    wrapped = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "s"}]}]}}
    assert len(translate_cc_hooks(wrapped)) == 1
    assert translate_cc_hooks("not a dict") == []
    assert translate_cc_hooks({"PreToolUse": "nope"}) == []


# ── live end-to-end via the plugin-host seam ─────────────────────────────────


@pytest.fixture
def plugin_with_hooks_and_agents(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    root = plugins / "demo"
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": "demo"}))
    _write(root / "hooks" / "hooks.json", json.dumps({
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "./g.sh"}]}],
    }))
    _write(root / "agents" / "reviewer.md",
           "---\nname: Security Reviewer\ndescription: Audits diffs.\nmodel: sonnet\n---\nYou review.")
    _write(root / "agents" / "noname.md", "no frontmatter, just a body line")

    import navig.core as core_mod

    class _Cfg:
        plugins_dir = plugins

    monkeypatch.setattr(core_mod, "Config", lambda: _Cfg())
    return root


def test_load_plugin_hook_definitions_live(plugin_with_hooks_and_agents):
    from navig.hooks.cc_bridge import load_plugin_hook_definitions

    defs = load_plugin_hook_definitions()
    assert len(defs) == 1
    assert defs[0].event == HookEvent.PRE_TOOL_USE and defs[0].tool_filter == "bash"


def test_hook_registry_picks_up_plugin_hooks(plugin_with_hooks_and_agents, tmp_path):
    from navig.hooks.registry import HookRegistry

    reg = HookRegistry(global_dir=tmp_path / "no-global", project_dir=tmp_path / "no-project")
    reg.load()
    hits = reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE, tool_name="bash")
    assert len(hits) == 1
    assert reg.get_hooks_for_event(HookEvent.PRE_TOOL_USE, tool_name="python") == []


def test_discover_plugin_agents_live(plugin_with_hooks_and_agents):
    from navig.plugins.cc_agents import discover_plugin_agents

    agents = discover_plugin_agents()
    by_key = {a["key"]: a for a in agents}
    assert "security-reviewer" in by_key
    a = by_key["security-reviewer"]
    assert a["label"] == "Security Reviewer" and a["subtitle"] == "Audits diffs."
    assert a["builtin"] is False and a["source"] == "plugin" and a["model"] == "sonnet"
    # a frontmatter-less file still yields an agent from its filename + first body line
    assert "noname" in by_key and by_key["noname"]["subtitle"] == "no frontmatter, just a body line"

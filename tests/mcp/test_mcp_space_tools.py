"""Tests for navig.mcp.tools.spaces — registration, the graph/context readers, the read guard.

The bundle reuses the SAME core library as the CLI and deck (relations /
linked_context), so these tests pin the wiring (tools + safety + handlers) and
the guard's refusal surface: not-linked, deny-list, include/exclude, containment,
missing file. Fixtures run against an isolated registry (monkeypatched
``_registry_file``), never the operator's ``~/.navig``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from navig.mcp.tools.spaces import (
    _tool_space_context,
    _tool_space_current,
    _tool_space_read,
    _tool_space_related,
    register,
)


def _server() -> MagicMock:
    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}
    server._tool_safety = {}
    return server


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    from navig.spaces import registry

    f = tmp_path / "spaces.json"
    monkeypatch.setattr(registry, "_registry_file", lambda: f)
    return f


def _mk(root: Path, sid: str) -> Path:
    p = root / f"{sid}-space"
    (p / ".navig").mkdir(parents=True)
    (p / ".navig" / "space.json").write_text(
        json.dumps({"id": sid, "name": sid.title(), "version": "1.0.0"}) + "\n", encoding="utf-8"
    )
    from navig.spaces import registry

    registry.register(p, id=sid, name=sid.title(), source="external", enabled=True)
    return p


# ── registration ──────────────────────────────────────────────────────────────


def test_register_declares_tools_safety_and_handlers():
    server = _server()
    register(server)
    for name in (
        "navig_space_current",
        "navig_space_related",
        "navig_space_context",
        "navig_space_read",
    ):
        assert name in server.tools, f"{name} not declared"
        assert name in server._tool_handlers, f"{name} handler missing"
        assert name in server._tool_safety, f"{name} safety entry missing"
    assert server._tool_safety["navig_space_read"] == "moderate"
    assert server._tool_safety["navig_space_current"] == "safe"


def test_bundle_is_served_by_default():
    from navig.mcp.tools import all_bundles, bundle_name

    names = {bundle_name(b) for b in all_bundles()}
    assert "spaces" in names


def test_only_selects_the_spaces_bundle():
    from navig.mcp import tools as tools_mod

    seen: list[str] = []
    server = _server()
    original = tools_mod.all_bundles
    tools_mod.all_bundles = lambda: [__import__("navig.mcp.tools.spaces", fromlist=["x"])]
    try:
        tools_mod.register_all_tools(server, only=["spaces"])
        seen = list(server.tools)
    finally:
        tools_mod.all_bundles = original
    assert set(seen) >= {
        "navig_space_current",
        "navig_space_related",
        "navig_space_context",
        "navig_space_read",
    }


# ── readers ───────────────────────────────────────────────────────────────────


def test_current_related_context_round_trip(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    alpha = _mk(tmp_path, "alpha")
    beta = _mk(tmp_path, "beta")
    (beta / ".navig" / "wiki").mkdir(parents=True)
    (beta / ".navig" / "wiki" / "guide.md").write_text("# g", encoding="utf-8")

    monkeypatch.setenv("NAVIG_SPACE", "alpha")
    from navig.spaces.relations import link

    link(alpha, "beta", include=(".navig/wiki/**",))

    cur = _tool_space_current(_server(), {})
    assert cur["id"] == "alpha" and cur["path"] == str(alpha)
    assert cur["relations"]["links"] == ["beta"]

    rel = _tool_space_related(_server(), {"space": "alpha"})
    assert rel["links"][0]["id"] == "beta" and rel["links"][0]["available"] is True

    ctx = _tool_space_context(_server(), {"space": "alpha", "query": "q1", "limit": 10})
    assert ctx["space"] == "alpha" and ctx["focus"] == "q1"
    assert any(i["rel"] == ".navig/wiki/guide.md" and i["space"] == "beta" for i in ctx["items"])
    assert ctx["truncated"] is False


def test_unknown_space_errors_do_not_raise(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("NAVIG_SPACE", "alpha")
    for fn in (_tool_space_current, _tool_space_related, _tool_space_context):
        out = fn(_server(), {"space": "ghost-space"})
        assert out.get("isError") is True and "no such space" in out["error"]


# ── the read guard ────────────────────────────────────────────────────────────


@pytest.fixture()
def linked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    alpha = _mk(tmp_path, "alpha")
    beta = _mk(tmp_path, "beta")
    (beta / ".navig" / "wiki").mkdir(parents=True)
    (beta / ".navig" / "wiki" / "guide.md").write_text("shared knowledge", encoding="utf-8")
    (beta / "vault").mkdir()
    (beta / "vault" / "keys.md").write_text("secret", encoding="utf-8")
    (beta / ".env").write_text("TOKEN=x", encoding="utf-8")
    monkeypatch.setenv("NAVIG_SPACE", "alpha")
    from navig.spaces.relations import link

    link(alpha, "beta", include=("**",))
    return alpha, beta


def test_read_returns_content_with_provenance(linked):
    alpha, _ = linked
    out = _tool_space_read(_server(), {"space": "beta", "path": ".navig/wiki/guide.md"})
    assert out["content"] == "shared knowledge"
    assert out["space"] == "beta"
    assert out["rel"] == ".navig/wiki/guide.md"
    assert out["mtime"] > 0


def test_read_denies_vault_and_env(linked):
    for path in ("vault/keys.md", ".env"):
        out = _tool_space_read(_server(), {"space": "beta", "path": path})
        assert out.get("isError") is True, f"{path} was read despite the deny list"
        assert "deny" in out["error"] or "denied" in out["error"]


def test_read_refuses_a_space_that_is_not_linked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    alpha = _mk(tmp_path, "alpha")
    gamma = _mk(tmp_path, "gamma")
    (gamma / "a.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv("NAVIG_SPACE", "alpha")

    out = _tool_space_read(_server(), {"space": "gamma", "path": "a.md"})
    assert out.get("isError") is True
    assert "not linked" in out["error"]
    assert "navig space link" in out["error"]  # the fix is named, not just the refusal


def test_read_enforces_containment_against_junctions(tmp_path, monkeypatch, linked):
    alpha, beta = linked
    outside = tmp_path / "outside.md"
    outside.write_text("escape", encoding="utf-8")
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("junction fixture is Windows-only")
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(beta / "linked"), str(tmp_path)],
        capture_output=True,
        check=False,
    )
    if made.returncode != 0:
        pytest.skip(f"could not create a junction: {made.stderr!r}")

    out = _tool_space_read(_server(), {"space": "beta", "path": "linked/outside.md"})
    assert out.get("isError") is True
    assert "escape" in out["error"] or "junction" in out["error"]


def test_read_include_globs_are_enforced(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    alpha = _mk(tmp_path, "alpha")
    beta = _mk(tmp_path, "beta")
    (beta / ".navig" / "wiki").mkdir(parents=True)
    (beta / ".navig" / "wiki" / "g.md").write_text("ok", encoding="utf-8")
    (beta / "other.md").write_text("no", encoding="utf-8")
    monkeypatch.setenv("NAVIG_SPACE", "alpha")
    from navig.spaces.relations import link

    link(alpha, "beta", include=(".navig/wiki/**",))

    ok = _tool_space_read(_server(), {"space": "beta", "path": ".navig/wiki/g.md"})
    assert ok.get("content") == "ok"
    refused = _tool_space_read(_server(), {"space": "beta", "path": "other.md"})
    assert refused.get("isError") is True
    assert "include" in refused["error"]

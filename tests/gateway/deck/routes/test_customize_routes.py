"""Tests for the Customizations deck routes (navig/gateway/deck/routes/customize.py).

The routes back navig OS's Customizations app, so what they must prove is that each
section reports what the runtime actually reads — and that every mutation is validated,
scoped, and refuses to lose data:

* instructions: allowlist only (no path traversal), atomic save + ``.bak``, 409 on a
  stale ``expected_mtime_ns`` instead of overwriting a newer file;
* MCP: every declaration source listed with which one is editable, live state from the
  gateway, header/env VALUES never leaked, enable toggles applied live;
* tools: block/unblock writes the ``tools.blocked_tools`` policy dispatch enforces,
  matched canonically;
* overview: each count is the length of the list it opens.

Handlers are called directly with a minimal fake request; config is a real
``ConfigManager`` in ``tmp_path``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _req(mgr=None, body=None, **match):
    app = {"gateway": SimpleNamespace(mcp_client_manager=mgr)} if mgr is not None else {}

    async def _json():
        if isinstance(body, Exception):
            raise body
        return body

    return SimpleNamespace(app=app, json=_json, match_info=match)


def _data(resp):
    payload = json.loads(resp.text)
    return resp.status, payload.get("data"), payload.get("error")


@pytest.fixture
def cm(tmp_path, monkeypatch):
    import navig.config as ncfg
    from navig.config import ConfigManager

    # Per-test config dir. `config_dir=` alone is NOT isolation: saves go to the global
    # config resolved live from NAVIG_CONFIG_DIR — conftest's one per-session dir, so
    # without this every test here would see the previous test's writes.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / ".navig"))
    real = ConfigManager(config_dir=tmp_path / ".navig")
    monkeypatch.setattr(ncfg, "get_config_manager", lambda *a, **k: real)
    return real


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Two workspace dirs in the runtime's read order: user first, config second."""
    import navig.gateway.deck.routes.customize as cz

    user, conf = tmp_path / "user_ws", tmp_path / "conf_ws"
    user.mkdir()
    conf.mkdir()
    monkeypatch.setattr(cz, "_instruction_dirs", lambda: [user, conf])
    return user, conf


# ── instructions ───────────────────────────────────────────────────────────────


async def test_instruction_resolves_like_the_runtime(ws):
    from navig.gateway.deck.routes.customize import handle_customize_instruction_get

    user, conf = ws
    (conf / "USER.md").write_text("from config", encoding="utf-8")
    status, data, _ = _data(await handle_customize_instruction_get(_req(name="USER.md")))
    assert status == 200 and data["content"] == "from config"
    (user / "USER.md").write_text("from user", encoding="utf-8")
    _, data, _ = _data(await handle_customize_instruction_get(_req(name="USER.md")))
    assert data["content"] == "from user"  # user workspace wins, as in _build_agent_context


@pytest.mark.parametrize("name", ["../config.yaml", "config.yaml", "SOUL.md/../x", ""])
async def test_instruction_names_outside_the_allowlist_are_refused(ws, name):
    from navig.gateway.deck.routes.customize import (
        handle_customize_instruction_get,
        handle_customize_instruction_save,
    )

    assert (await handle_customize_instruction_get(_req(name=name))).status == 404
    resp = await handle_customize_instruction_save(_req(body={"content": "x"}, name=name))
    assert resp.status == 404


async def test_instruction_save_is_atomic_backs_up_and_refuses_lost_update(ws):
    from navig.gateway.deck.routes.customize import (
        handle_customize_instruction_get,
        handle_customize_instruction_save,
    )

    user, _ = ws
    target = user / "AGENTS.md"
    target.write_text("v1", encoding="utf-8")
    _, loaded, _ = _data(await handle_customize_instruction_get(_req(name="AGENTS.md")))

    status, saved, _ = _data(await handle_customize_instruction_save(
        _req(body={"content": "v2", "expected_mtime_ns": loaded["mtime_ns"]}, name="AGENTS.md")
    ))
    assert status == 200 and target.read_text(encoding="utf-8") == "v2"
    assert (user / "AGENTS.md.bak").read_text(encoding="utf-8") == "v1"

    # A save based on the v1 snapshot must not clobber v2.
    status, _, err = _data(await handle_customize_instruction_save(
        _req(body={"content": "stale", "expected_mtime_ns": loaded["mtime_ns"] - 1},
             name="AGENTS.md")
    ))
    assert status == 409 and "changed on disk" in err
    assert target.read_text(encoding="utf-8") == "v2"


@pytest.mark.parametrize("body,status", [
    (None, 400), (ValueError("bad json"), 400), ({"content": 5}, 400),
    ({"content": "x", "expected_mtime_ns": "1"}, 400),
    ({"content": "x" * (256 * 1024 + 1)}, 413),
])
async def test_instruction_save_validates(ws, body, status):
    from navig.gateway.deck.routes.customize import handle_customize_instruction_save

    resp = await handle_customize_instruction_save(_req(body=body, name="USER.md"))
    assert resp.status == status


async def test_instruction_save_creates_missing_file_in_user_workspace(ws):
    from navig.gateway.deck.routes.customize import handle_customize_instruction_save

    user, conf = ws
    status, data, _ = _data(await handle_customize_instruction_save(
        _req(body={"content": "hello"}, name="TOOLS.md")
    ))
    assert status == 200 and data["backup"] is None
    assert (user / "TOOLS.md").read_text(encoding="utf-8") == "hello"
    assert not (conf / "TOOLS.md").exists()


# ── MCP ────────────────────────────────────────────────────────────────────────


class _Mgr:
    def __init__(self, clients=None):
        self.clients = clients or {}
        self.calls: list = []

    async def remove_client(self, name):
        self.calls.append(("remove", name))
        self.clients.pop(name, None)

    async def add_client(self, cfg):
        self.calls.append(("add", cfg.id))
        self.clients[cfg.id] = SimpleNamespace(is_connected=False, tools=[])

    async def connect_client(self, name):
        self.clients[name].is_connected = True
        return True

    async def disconnect_client(self, name):
        self.clients[name].is_connected = False
        return True


@pytest.fixture
def no_plugins(monkeypatch):
    import navig.mcp_manager as mm
    import navig.plugins.package as pkg

    monkeypatch.setattr(pkg, "installed_plugin_roots", lambda: [])
    monkeypatch.setattr(mm.MCPManager, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(mm.MCPManager, "list_servers", lambda self, **k: [])


async def test_mcp_lists_every_source_with_live_state_and_no_secrets(cm, no_plugins):
    from navig.gateway.deck.routes.customize import handle_customize_mcp

    cm.set_global("mcp", {
        "servers": [{"name": "fs", "type": "stdio", "command": "npx", "args": ["a"],
                     "env": {"TOKEN": "s3cret"}},
                    {"name": "api", "type": "http", "url": "https://x",
                     "headers": {"Authorization": "Bearer s3cret"}}],
        "clients": {"declared": {"command": "x"}},
    })
    cm.set_global("agent", {"mcp": {"servers": [{"name": "pool", "command": ["p"]}]}})
    mgr = _Mgr({"fs": SimpleNamespace(is_connected=True, tools=[1, 2])})

    status, data, _ = _data(await handle_customize_mcp(_req(mgr)))
    assert status == 200 and data["gateway"] is True
    rows = {(r["source"], r["name"]): r for r in data["items"]}
    assert rows[("servers", "fs")]["live"] == "connected"
    assert rows[("servers", "fs")]["tools"] == 2
    assert rows[("servers", "api")]["live"] == "not_loaded"
    assert rows[("servers", "fs")]["editable"] is True
    assert rows[("clients", "declared")]["editable"] is False
    assert rows[("agent", "pool")]["command"] == "p"
    assert "s3cret" not in json.dumps(data)
    assert rows[("servers", "api")]["header_names"] == ["Authorization"]

    # No gateway → "unknown", never a guessed "disconnected".
    _, data, _ = _data(await handle_customize_mcp(_req()))
    assert {r["live"] for r in data["items"] if r["source"] == "servers"} == {"unknown"}


async def test_mcp_enable_toggle_writes_config_and_applies_live(cm, no_plugins):
    from navig.gateway.deck.routes.customize import handle_customize_mcp_patch

    cm.set_global("mcp.servers", [{"name": "fs", "type": "stdio", "command": "npx"}])
    mgr = _Mgr()
    status, data, _ = _data(await handle_customize_mcp_patch(
        _req(mgr, body={"enabled": False}, name="fs")
    ))
    assert status == 200 and data["live"] == "disabled"
    assert cm.refresh_global_config()["mcp"]["servers"][0]["enabled"] is False
    _, data, _ = _data(await handle_customize_mcp_patch(
        _req(mgr, body={"enabled": True}, name="fs")
    ))
    assert data["live"] == "applied" and ("add", "fs") in mgr.calls


@pytest.mark.parametrize("body,name,status", [
    ({"enabled": "false"}, "fs", 400), (None, "fs", 400), ({"enabled": True}, "nope", 404),
])
async def test_mcp_patch_validates(cm, no_plugins, body, name, status):
    from navig.gateway.deck.routes.customize import handle_customize_mcp_patch

    cm.set_global("mcp.servers", [{"name": "fs", "command": "x"}])
    assert (await handle_customize_mcp_patch(_req(_Mgr(), body=body, name=name))).status == status


async def test_mcp_connect_needs_a_gateway_and_a_loaded_client():
    from navig.gateway.deck.routes.customize import (
        handle_customize_mcp_connect,
        handle_customize_mcp_disconnect,
    )

    assert (await handle_customize_mcp_connect(_req(name="fs"))).status == 503
    assert (await handle_customize_mcp_connect(_req(_Mgr(), name="fs"))).status == 409
    mgr = _Mgr({"fs": SimpleNamespace(is_connected=False, tools=[])})
    _, data, _ = _data(await handle_customize_mcp_connect(_req(mgr, name="fs")))
    assert data["connected"] is True
    _, data, _ = _data(await handle_customize_mcp_disconnect(_req(mgr, name="fs")))
    assert data["connected"] is False


# ── tools ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry(monkeypatch):
    import navig.agent.agent_tool_registry as atr

    reg = atr.AgentToolRegistry()
    for name, toolset in (("web_fetch", "web"), ("bash_exec", "core"),
                          ("mcp__fs__read", "mcp:fs")):
        reg._entries[name] = atr.AgentToolEntry(  # noqa: SLF001 — avoid the global gate
            name=name, schema={"description": f"{name} tool"},
            tool_ref=SimpleNamespace(), toolset=toolset,
            origin="external" if toolset.startswith("mcp:") else "first-party",
        )
    monkeypatch.setattr(atr, "_AGENT_REGISTRY", reg)
    return reg


async def test_tool_block_round_trip_is_canonical(cm, registry):
    from navig.gateway.deck.routes.customize import (
        handle_customize_tool_patch,
        handle_customize_tools,
    )

    cm.set_global("tools.blocked_tools", ["web-fetch"])  # operator's own spelling
    _, data, _ = _data(await handle_customize_tools(_req()))
    blocked = {t["name"]: t["blocked"] for t in data["items"]}

    # Asserted per name rather than as a whole dict. This list is whatever the live
    # registry holds, and loading the module registry can pull in entry-point tools
    # (`net_*`, `games_*`, …) — so whole-dict equality broke the first time an
    # unrelated plugin registered a tool, while the thing under test (the canonical
    # round-trip) was passing the whole time.
    assert blocked["web_fetch"] is True, "the operator's `web-fetch` spelling must match"
    assert blocked["bash_exec"] is False
    assert blocked["mcp__fs__read"] is False

    # Unblocking removes the canonically-equal spelling, not just an exact match.
    _, data, _ = _data(await handle_customize_tool_patch(
        _req(body={"blocked": False}, name="web_fetch")
    ))
    assert data["blocked_tools"] == []
    await handle_customize_tool_patch(_req(body={"blocked": True}, name="bash_exec"))
    await handle_customize_tool_patch(_req(body={"blocked": True}, name="bash_exec"))
    assert cm.refresh_global_config()["tools"]["blocked_tools"] == ["bash_exec"]  # no dupes


async def test_tool_patch_validates(cm, registry):
    from navig.gateway.deck.routes.customize import handle_customize_tool_patch

    assert (await handle_customize_tool_patch(
        _req(body={"blocked": "yes"}, name="bash_exec"))).status == 400


# ── hooks ──────────────────────────────────────────────────────────────────────


def test_hooks_string_false_does_not_allow_network_and_bad_entries_dont_drop_all(tmp_path):
    from navig.hooks.registry import HookRegistry

    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "hooks.yaml").write_text(
        'hooks:\n  allow_network: "false"\n  definitions:\n'
        '    - event: PreToolUse\n      command: echo a\n      timeout_seconds: soon\n'
        '    - not-a-mapping\n'
        '    - event: SessionStart\n      command: echo b\n',
        encoding="utf-8",
    )
    reg = HookRegistry(global_dir=tmp_path / "g", project_dir=tmp_path / "p")
    reg._load_plugin_hooks = lambda: None  # noqa: SLF001 — isolate from installed plugins
    defs = reg.all_definitions()
    assert reg.allow_network is False
    assert [d.command for d in defs] == ["echo a", "echo b"]
    assert all(d.source.endswith("hooks.yaml") for d in defs)


# ── overview ───────────────────────────────────────────────────────────────────


async def test_overview_counts_equal_the_lists(cm, ws, registry, no_plugins, monkeypatch):
    import navig.gateway.deck.routes.customize as cz
    import navig.skills.loader as loader

    monkeypatch.setattr(loader, "load_all_skills", lambda *a, **k: [object(), object()])
    monkeypatch.setattr(cz, "_load_agents", lambda: {"items": [{}], "error": None})
    monkeypatch.setattr(cz, "_load_hooks", lambda: {"items": [{}, {}, {}], "error": None})
    cm.set_global("mcp.servers", [{"name": "fs", "command": "x"}])
    (ws[0] / "USER.md").write_text("u", encoding="utf-8")

    _, data, _ = _data(await cz.handle_customize_overview(_req()))
    counts = {k: v["count"] for k, v in data.items()}

    # Every section is mocked to a known size EXCEPT tools, whose registry is live
    # and plugin-extensible: reading it pulls in entry-point tools (`net_*`,
    # `games_*`), so pinning `"tools": 3` broke the moment any plugin registered
    # one — including in runs where this test passed locally and failed in the gate,
    # because the two loaded plugins differently. The invariant this test exists for
    # is the one the next line asserts: count == length of the list it opens.
    assert set(counts) == {"agents", "instructions", "hooks", "mcp", "tools", "skills"}
    assert counts["agents"] == 1
    assert counts["instructions"] == 1
    assert counts["hooks"] == 3
    assert counts["mcp"] == 1
    assert counts["skills"] == 2

    _, tools, _ = _data(await cz.handle_customize_tools(_req()))
    assert len(tools["items"]) == data["tools"]["count"]


async def test_overview_reports_a_failing_section_instead_of_zero(cm, ws, registry,
                                                                  no_plugins, monkeypatch):
    import navig.gateway.deck.routes.customize as cz

    def _boom():
        raise RuntimeError("hooks.yaml unreadable")

    monkeypatch.setattr(cz, "_load_hooks", _boom)
    monkeypatch.setattr(cz, "_load_agents", lambda: {"items": [], "error": None})
    _, data, _ = _data(await cz.handle_customize_overview(_req()))
    assert data["hooks"] == {"count": 0, "error": "hooks.yaml unreadable"}


# ── module enablement: the surface must agree with the agent ──────────────────


async def test_a_disabled_module_marks_its_toolset_not_enabled(cm, registry, monkeypatch):
    """The agent drops a disabled module's tools; this surface must say so too.

    Before this, turning a module off removed its tools from what the model is
    offered (`navig.agent.conv.agent` → `disabled_surfaces("toolset")`) while the
    Customizations surface still listed them with no indication — so NAVIG OS could
    show a capability as present when it was inert everywhere that mattered. Same
    predicate, both places, or "disabled" means two different things.
    """
    import navig.gateway.deck.routes.customize as cz

    class _Reg:
        def disabled_surfaces(self, prefix):
            return {"web"} if prefix == "toolset" else set()

    monkeypatch.setattr("navig.modules.registry.get_registry", lambda: _Reg())

    _, data, _ = _data(await cz.handle_customize_tools(_req()))
    flags = {t["name"]: t["module_enabled"] for t in data["items"]}

    # Per name, not whole-dict: the live registry also carries whatever plugins
    # registered, which has nothing to do with this assertion.
    assert flags["web_fetch"] is False, "its toolset is off"
    assert flags["bash_exec"] is True, "core toolset, untouched"
    assert flags["mcp__fs__read"] is True, "an MCP server is not a module — never gated here"
    assert all(t["module_enabled"] is True for t in data["items"] if t["toolset"] == "core")


async def test_a_registry_that_cannot_decide_lists_nothing(cm, registry, monkeypatch):
    """Failing closed. If the predicate cannot be evaluated, reporting every tool as
    enabled would be a gate that opens on error — the same defect the HTTP gate had.
    """
    import navig.gateway.deck.routes.customize as cz

    def _boom():
        raise RuntimeError("module registry exploded")

    monkeypatch.setattr("navig.modules.registry.get_registry", _boom)

    _, data, _ = _data(await cz.handle_customize_tools(_req()))

    assert data["error"], "the failure must be reported, not swallowed"
    assert data["items"] == [], "no tool may read as usable when the gate cannot decide"

"""Regressions for the core half of the navig OS "Customizations" surface.

Each test pins one defect found while auditing what the brain exposes for agents,
skills and MCP servers — every one of them a state the deck reported that was not
the state the daemon was in:

* admin loaders called ``ConfigManager.get_config()`` — a method that does not exist —
  so they always read ``{}``: saved settings never read back, custom agents never listed;
* the gateway's MCP boot dropped ``args``/``env``/``cwd``/``headers`` from every entry;
* deck MCP add/delete wrote config but never touched the running client manager;
* a non-string MCP ``name`` was a 500, not a 400;
* the admin MCP row said ``running: false`` for every server, always;
* a duplicate skill id got a ``hash()`` suffix, which changes on every restart;
* a failing skills loader rendered as "0 skills".
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

# ── admin loaders read the real config ─────────────────────────────────────────


@pytest.fixture
def real_cm(tmp_path, monkeypatch):
    import navig.config as ncfg
    from navig.config import ConfigManager

    # Per-test config dir. `config_dir=` alone is NOT isolation: saves go to the global
    # config resolved live from NAVIG_CONFIG_DIR — conftest's one per-session dir, so
    # without this every test here would see the previous test's writes.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / ".navig"))
    cm = ConfigManager(config_dir=tmp_path / ".navig")
    monkeypatch.setattr(ncfg, "get_config_manager", lambda *a, **k: cm)
    return cm


def test_saved_admin_setting_reads_back(real_cm):
    from navig.gateway.deck.routes.admin import _load_admin_settings

    real_cm.set("chat.verbosity", "concise")
    # `navig config set` stores raw strings — "false" must read as False, not truthy.
    real_cm.set("code_interpreter.python_enabled", "false")
    settings = _load_admin_settings()
    assert settings["chat_preferences"]["verbosity"] == "concise"
    assert settings["code_interpreter"]["python_enabled"] is False


def test_custom_agents_from_config_are_listed(real_cm):
    from navig.gateway.deck.routes.admin import _load_agents

    real_cm.set("agents.custom", [{"key": "ops", "name": "Ops", "enabled": "false"}])
    real_cm.set("agents.enabled", ["coder"])
    roster = _load_agents()
    ops = next(a for a in roster["custom"] if a["key"] == "ops")
    assert ops["enabled"] is False
    enabled = {a["key"]: a["enabled"] for a in roster["builtin"]}
    assert enabled["coder"] is True and enabled["assistant"] is False


# ── MCP: one entry → client-config mapping ─────────────────────────────────────


def test_server_entry_keeps_args_env_cwd():
    from navig.mcp import MCPClientConfig

    cfg = MCPClientConfig.from_server_entry({
        "name": "fs", "type": "stdio", "command": "npx",
        "args": ["-y", "pkg", 3], "env": {"K": "v"}, "cwd": "/w",
    })
    assert (cfg.transport, cfg.command, cfg.args, cfg.env, cfg.cwd) == (
        "stdio", "npx", ["-y", "pkg", "3"], {"K": "v"}, "/w",
    )
    assert cfg.enabled is True


def test_server_entry_maps_url_types_and_headers_reach_the_transport():
    from navig.mcp import MCPClientConfig
    from navig.mcp.client import MCPClient

    http = MCPClientConfig.from_server_entry({
        "name": "api", "type": "http", "url": "https://x/sse",
        "headers": {"Authorization": "Bearer t"},
    })
    assert http.transport == "sse"
    assert MCPClient(http)._build_transport().headers == {"Authorization": "Bearer t"}

    ws = MCPClientConfig.from_server_entry({"name": "w", "url": "wss://x"})
    assert ws.transport == "websocket"


def test_server_entry_disabled_and_invalid():
    from navig.mcp import MCPClientConfig

    assert MCPClientConfig.from_server_entry(
        {"name": "n", "command": "x", "enabled": "false"}
    ).enabled is False
    with pytest.raises(ValueError):
        MCPClientConfig.from_server_entry({"name": 7, "command": "x"})


# ── MCP: deck writes are applied to the live gateway ───────────────────────────


class _FakeMgr:
    def __init__(self, fail: bool = False) -> None:
        self.added: list = []
        self.removed: list[str] = []
        self.fail = fail
        self.clients: dict = {}

    async def remove_client(self, name: str) -> None:
        self.removed.append(name)

    async def add_client(self, cfg) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.added.append(cfg)


def _req(mgr=None, body=None):
    app = {"gateway": SimpleNamespace(mcp_client_manager=mgr)} if mgr is not None else {}

    async def _json():
        return body

    return SimpleNamespace(app=app, json=_json, match_info={})


async def test_live_apply_add_replace_remove_disable():
    from navig.gateway.deck.routes.connectors import apply_mcp_entry_live

    mgr = _FakeMgr()
    entry = {"name": "fs", "type": "stdio", "command": "npx", "args": ["a"]}
    assert await apply_mcp_entry_live(_req(mgr), "fs", entry) == "applied"
    assert mgr.added[0].args == ["a"] and mgr.removed == ["fs"]  # replace = remove + add
    assert await apply_mcp_entry_live(_req(mgr), "fs", None) == "removed"
    assert await apply_mcp_entry_live(
        _req(mgr), "fs", {**entry, "enabled": False}
    ) == "disabled"
    assert len(mgr.added) == 1


async def test_live_apply_reports_instead_of_raising():
    from navig.gateway.deck.routes.connectors import apply_mcp_entry_live

    assert await apply_mcp_entry_live(_req(), "fs", {"name": "fs"}) == "no_gateway"
    out = await apply_mcp_entry_live(
        _req(_FakeMgr(fail=True)), "fs", {"name": "fs", "command": "x"}
    )
    assert out.startswith("error:")


@pytest.mark.parametrize("body", [{"name": 5, "type": "stdio"}, ["not", "an", "object"],
                                  {"name": "x", "type": "stdio", "command": "c", "args": "a"}])
async def test_mcp_add_rejects_bad_input_with_400(body):
    from navig.gateway.deck.routes.connectors import handle_deck_mcp_add

    resp = await handle_deck_mcp_add(_req(body=body))
    assert resp.status == 400


def test_admin_mcp_row_reads_config_and_never_guesses_running(monkeypatch):
    import navig.mcp_manager as mm
    from navig.gateway.deck.routes.admin import _load_mcp_servers

    server = mm.MCPServer("fs", {"command": "npx", "description": "files", "enabled": True})
    monkeypatch.setattr(mm.MCPManager, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(mm.MCPManager, "list_servers", lambda self, **k: [server])

    row = _load_mcp_servers()[0]
    assert (row["subtitle"], row["command"], row["running"]) == ("files", "npx", None)
    live = {"fs": SimpleNamespace(is_connected=True)}
    assert _load_mcp_servers(live)[0]["running"] is True
    assert _load_mcp_servers({})[0]["running"] is False


# ── skills ─────────────────────────────────────────────────────────────────────


def _skill(dir_, sid: str):
    d = dir_ / sid
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {sid}\ndescription: does {sid}\n---\n# Heading\nbody\n", encoding="utf-8"
    )
    return d / "SKILL.md"


def test_duplicate_skill_id_suffix_is_stable(tmp_path):
    from navig.skills.loader import load_all_skills

    _skill(tmp_path / "a", "dup")
    second = _skill(tmp_path / "b", "dup")
    ids = [s.id for s in load_all_skills([tmp_path / "a", tmp_path / "b"])]
    expected = hashlib.sha1(str(second.resolve()).encode("utf-8")).hexdigest()[:6]
    assert ids == ["dup", f"dup-{expected}"]


async def test_skills_route_surfaces_loader_failure(monkeypatch):
    import navig.skills.loader as loader
    from navig.gateway.deck.routes.skills import handle_deck_skills

    def _boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(loader, "load_all_skills", _boom)
    data = json.loads((await handle_deck_skills(_req())).text)["data"]
    assert data["count"] == 0 and "disk on fire" in data["error"]


async def test_skills_route_returns_description_as_summary(monkeypatch, tmp_path):
    import navig.skills.loader as loader
    from navig.gateway.deck.routes.skills import handle_deck_skills

    _skill(tmp_path, "alpha")
    real = loader.load_all_skills
    monkeypatch.setattr(loader, "load_all_skills", lambda *a, **k: real([tmp_path]))
    data = json.loads((await handle_deck_skills(_req())).text)["data"]
    (skill,) = data["skills"]
    assert skill["description"] == "does alpha" and skill["summary"] == "does alpha"
    assert data["error"] is None

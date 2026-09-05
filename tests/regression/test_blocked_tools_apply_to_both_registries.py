"""`tools.blocked_tools` is documented as blocking a tool "entirely" — so it must hold in
both tool registries, not just the one that happened to have a gate.

NAVIG has two: `ToolRouter` (17 tools) and `AgentToolRegistry` (57 — the conversational
agent's main path). The operator writes ONE list of names for both. Measured, they share
exactly two names, and they are the two that matter most:

    bash_exec    "Execute a shell command"
    web_fetch    network egress

`ToolRouter` had the only gate. So `blocked_tools: ["bash_exec"]` — even once the router
was wired to read it — still left the shell reachable on the path the agent actually uses.
A control enforced on one path out of two is the class this repo keeps finding: the guard
protects a *path*, not the *surface*.

Enforced at `AgentToolRegistry.dispatch`, the chokepoint every caller reaches, rather than
via the `permissions=` argument — that argument has existed all along, is fully implemented,
and **no production caller passes it**. A control that only applies where someone remembered
to thread an argument through is not a control.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_router():
    from navig.tools.router import reset_globals

    reset_globals()
    yield
    reset_globals()


def _operator_config(monkeypatch, tmp_path, body: str):
    """A real `ConfigManager` over a real `config.yaml` (see the router regression test)."""
    import navig.config as config_mod

    (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    cm = config_mod.ConfigManager()
    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: cm)
    return cm


BLOCK_BASH = "tools:\n  blocked_tools:\n    - bash_exec\n"


class _Tool:
    """Minimal BaseTool-shaped object; the registry only reads these attributes."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "test tool"
        self.parameters = {"type": "object", "properties": {}}

    async def run(self, args, **kwargs):
        from navig.tools.schemas import ToolResult, ToolResultStatus

        return ToolResult(tool=self.name, status=ToolResultStatus.SUCCESS, output="EXECUTED")


def _registry_with(name: str):
    """A registry holding one trivially-callable tool called *name*."""
    from navig.agent.agent_tool_registry import AgentToolRegistry

    reg = AgentToolRegistry()
    reg.register(_Tool(name))
    return reg


def test_dispatch_refuses_a_tool_the_operator_blocked(monkeypatch, tmp_path):
    """The agent's main path must honour the same list the router does."""
    _operator_config(monkeypatch, tmp_path, BLOCK_BASH)

    from navig.agent.tool_permissions import ToolPermissionDenied

    reg = _registry_with("bash_exec")

    with pytest.raises(ToolPermissionDenied):
        reg.dispatch("bash_exec", {})


def test_a_tool_the_operator_did_not_block_still_runs(monkeypatch, tmp_path):
    """The guard must not become a blanket denial — that would be its own outage."""
    _operator_config(monkeypatch, tmp_path, BLOCK_BASH)

    reg = _registry_with("web_fetch")

    assert reg.dispatch("web_fetch", {}) == "EXECUTED"


def test_the_two_registries_agree_on_spelling(monkeypatch, tmp_path):
    """One config list, two registries — so one canonicalisation, not two.

    The operator writes `web-fetch`; #813 taught the router to canonicalise it. If the
    agent registry grew its own matching rule, the same entry would stop the tool in one
    registry and not the other.
    """
    _operator_config(monkeypatch, tmp_path, "tools:\n  blocked_tools:\n    - web-fetch\n")

    from navig.agent.tool_permissions import ToolPermissionDenied
    from navig.tools.router import get_tool_router

    assert "web_fetch" in get_tool_router()._blocked  # router side

    reg = _registry_with("web_fetch")
    with pytest.raises(ToolPermissionDenied):  # agent side, same entry
        reg.dispatch("web_fetch", {})


def test_a_blocked_tool_is_not_offered_to_the_model(monkeypatch, tmp_path):
    """Dispatch refuses it anyway; not advertising it saves a wasted turn."""
    _operator_config(monkeypatch, tmp_path, BLOCK_BASH)

    reg = _registry_with("bash_exec")

    names = [s["function"]["name"] for s in reg.get_openai_schemas()]
    assert "bash_exec" not in names


def test_schemas_are_unaffected_when_no_policy_is_set(monkeypatch, tmp_path):
    """The empty-policy path is the common one and must stay a no-op."""
    _operator_config(monkeypatch, tmp_path, "tools:\n  max_calls_per_turn: 10\n")

    reg = _registry_with("bash_exec")

    names = [s["function"]["name"] for s in reg.get_openai_schemas()]
    assert "bash_exec" in names


def test_the_speculative_cache_cannot_serve_a_blocked_tool(monkeypatch, tmp_path):
    """A cache hit returns without ever calling dispatch — the one route that bypasses it.

    `SpeculativeExecutor._dispatch_fn` *is* `AgentToolRegistry.dispatch`, so every miss is
    covered by the gate above. The hit path is not, and it applies to read-only tools —
    which `web_fetch` is. Without this check, blocking `web_fetch` would still hand the
    model a previously-fetched page body.
    """
    _operator_config(monkeypatch, tmp_path, "tools:\n  blocked_tools:\n    - web_fetch\n")

    from navig.agent.speculative import SpeculativeExecutor
    from navig.agent.tool_permissions import ToolPermissionDenied

    spec = SpeculativeExecutor(lambda tool, args: "FRESH")
    spec.cache.put("web_fetch", {}, "CACHED PAGE BODY")

    with pytest.raises(ToolPermissionDenied):
        spec.execute("web_fetch", {})

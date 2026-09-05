"""A toolset may only advertise tools the registry can actually dispatch.

`TOOLSETS` is what turns `toolset="memory"` into the schemas the model sees. A
name in that table that nothing registers does not reach the model at all — it
is dropped in silence, so the toolset simply has fewer tools than it claims and
nothing anywhere says so.

Measured before this guard existed: **2 of 13 toolsets** named a tool that is
never registered.

* ``memory`` listed ``fts_search``, which is a method on the conversation memory
  store, not an agent tool — so the toolset offered four tools while claiming
  five, and conversation full-text search was never actually reachable.
* ``delegation`` lists ``delegate_task``, whose registrar has no caller anywhere
  — that toolset is entirely empty.

Both are the same shape as the defects this codebase keeps finding: something
written, plausibly documented, and wired to nothing.
"""

from __future__ import annotations

import pytest

from navig.agent import toolsets as ts

#: Toolsets that are allowed to register nothing, each with a written reason.
#: An entry here is a promise that the emptiness is KNOWN, not an oversight —
#: wiring one of these must remove its entry, which is why the test also fails
#: when an exempt toolset starts working.
KNOWN_EMPTY = {
    "delegation": (
        "delegate.py defines DelegateTool but register_delegate_tool() has no "
        "caller and register_all_tools() does not include it. The live "
        "multi-agent path is `coordinator`."
    ),
}


@pytest.fixture(scope="module")
def registered() -> set[str]:
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_all_tools

    register_all_tools()
    names = set(_AGENT_REGISTRY.available_names() or [])
    assert len(names) > 20, (
        f"only {len(names)} tools registered — the bootstrap itself is broken, "
        "so every assertion below would pass by reading nothing"
    )
    return names


def _table() -> dict[str, list[str] | None]:
    table = getattr(ts, "TOOLSETS", None)
    assert isinstance(table, dict) and table, "TOOLSETS table not found — guard is stale"
    return table


def test_no_toolset_is_partially_real(registered):
    """The robust rule: if ANY of a toolset's tools registered, ALL must have.

    Deliberately phrased this way rather than "every name must be registered".
    Optional groups (lsp, browser, devops, remote) register inside a try/except,
    so on a machine missing one of their dependencies the whole group is absent —
    which is an environment fact, not a stale table. A group that loaded and is
    still missing one name is a stale table, every time.
    """
    stale: dict[str, list[str]] = {}
    for name, tools in _table().items():
        if not isinstance(tools, list) or not tools:
            continue
        present = [t for t in tools if t in registered]
        missing = [t for t in tools if t not in registered]
        if present and missing:
            stale[name] = missing
    assert not stale, (
        "toolsets advertising tools the registry cannot dispatch "
        f"(they are dropped in silence): {stale}"
    )


def test_an_empty_toolset_is_known_and_explained(registered):
    """A toolset that registers nothing gives the model no tools at all."""
    empty = [
        name
        for name, tools in _table().items()
        if isinstance(tools, list) and tools and not any(t in registered for t in tools)
    ]
    unexplained = sorted(set(empty) - set(KNOWN_EMPTY))
    assert not unexplained, (
        f"toolset(s) {unexplained} register no tools at all — selecting one "
        "yields an agent with nothing. Wire them, or add a KNOWN_EMPTY entry "
        "saying why not."
    )


@pytest.mark.parametrize("name", sorted(KNOWN_EMPTY))
def test_a_known_empty_toolset_that_starts_working_updates_its_note(name, registered):
    """The exemption expires by itself.

    Without this, wiring `delegation` would leave a KNOWN_EMPTY entry asserting
    the opposite of the truth — the exact stale-claim shape the guard exists for.
    """
    tools = _table().get(name) or []
    assert not any(t in registered for t in tools), (
        f"{name!r} now registers tools — remove its KNOWN_EMPTY entry "
        f"(reason on file: {KNOWN_EMPTY[name]})"
    )


def test_fts_search_is_not_advertised_as_a_tool():
    """It is a store method (`navig.memory.conversation`), not a dispatchable tool.

    Pinned by name because it sat in the `memory` toolset long enough to look
    deliberate, and the fix is one word in a list that is easy to re-add.
    """
    memory = _table().get("memory") or []
    assert "fts_search" not in memory, (
        "fts_search is a ConversationStore method, not an agent tool — listing it "
        "makes the toolset claim a capability the model never receives"
    )

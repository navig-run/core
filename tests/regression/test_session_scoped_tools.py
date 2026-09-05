"""A tool that needs to know *which conversation* it is serving must be told.

`browser_tool` needs it (one persistent browser per chat) and got it through a hardcoded
name check in the agent's tool loop:

    if tool_call_item.name == "browser_tool":
        args = {**args, "_session_id": _browser_session_key}

That is a hardcoded list of one, against this repo's stated architectural law —
*"Tools, skills, packs, adapters self-register. No hardcoded lists."* — and it is why the
todo tools could not be wired: `register_todo_tools` takes a `todo_list` argument, nothing
constructed one, and a single process-wide list would let one chat's `todo_create`
(which REPLACES the list) wipe another chat's plan. Exactly the shape that makes wiring
dead code worse than leaving it dead.

A tool now declares `needs_session = True` and the agent injects the stable per-chat key
for any tool that asks. `_session_id` stays undeclared in the tool schema, so it remains
invisible to the model — it is context, not an argument the LLM may choose.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def registry():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    yield _AGENT_REGISTRY
    _AGENT_REGISTRY.deregister_toolset("todo")


# ── the mechanism ──────────────────────────────────────────────────────────────────


def test_base_tool_defaults_to_not_needing_a_session():
    """Opt-in. A tool that never asked must not start receiving an extra arg."""
    from navig.tools.registry import BaseTool

    assert BaseTool.needs_session is False


def test_the_browser_tool_declares_it_needs_a_session():
    """It is the reason the mechanism exists; it must not lose the key in the swap."""
    from navig.agent.tools.browser_tools import BrowserTool

    assert BrowserTool.needs_session is True


def test_the_agent_injects_by_declaration_not_by_tool_name():
    """The hardcoded `== "browser_tool"` must be gone, on the AST.

    A textual check would be satisfied by the string still appearing in a comment.
    """
    import ast
    import inspect

    from navig.agent.conv import agent as agent_mod

    tree = ast.parse(inspect.getsource(agent_mod))
    hardcoded = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(c, ast.Constant) and c.value == "browser_tool"
            for c in node.comparators
        )
    ]
    assert not hardcoded, (
        "the agent still branches on the literal tool name 'browser_tool' to decide "
        "whether to inject the session key — every future session-scoped tool would "
        "need another line here. Read the tool's `needs_session` declaration instead."
    )


# ── the todo tools, keyed by conversation ──────────────────────────────────────────


def test_todo_tools_are_registered(registry):
    from navig.agent.tools import register_all_tools

    register_all_tools()
    for name in ("todo_create", "todo_update", "todo_show"):
        assert name in registry, f"{name} reaches no agent"


def test_todo_tools_declare_they_need_a_session():
    from navig.agent.tools.todo_tools import (
        TodoCreateTool,
        TodoShowTool,
        TodoUpdateTool,
    )

    for cls in (TodoCreateTool, TodoShowTool, TodoUpdateTool):
        assert cls.needs_session is True, (
            f"{cls.__name__} would fall back to a shared list, so one chat's plan "
            "would overwrite another's"
        )


def test_two_conversations_get_separate_lists():
    """The whole reason this could not simply be wired.

    `todo_create` REPLACES the list. Shared state means chat B silently destroys chat A's
    plan — a regression on 'the tool does not exist'.
    """
    from navig.agent.tools import todo_tools

    todo_tools.reset_todo_lists()

    a = todo_tools.get_todo_list("chat:A")
    b = todo_tools.get_todo_list("chat:B")

    assert a is not b
    a.add("only in A")
    assert [i.title for i in b.items] == [], "chat B sees chat A's items"
    assert todo_tools.get_todo_list("chat:A") is a, "the same chat got a fresh list"


async def test_a_todo_survives_across_tool_calls_in_one_chat():
    """End-to-end through the real tools, with the key the agent would inject."""
    from navig.agent.tools import todo_tools

    todo_tools.reset_todo_lists()

    create = todo_tools.TodoCreateTool()
    show = todo_tools.TodoShowTool()

    # A real Python list, not a JSON string: the schema says "string" but the description
    # asks for a JSON array, so a model sends this shape. `run()` must never raise.
    res = await create.run({"items": ["write the guard", "run it"], "_session_id": "c1"})
    assert res.success, res.error

    out = await show.run({"_session_id": "c1"})
    assert "write the guard" in str(out.output)

    other = await show.run({"_session_id": "c2"})
    assert "write the guard" not in str(other.output), (
        "a second conversation can read the first one's todo list"
    )


def test_the_store_does_not_grow_without_bound():
    """A long-lived daemon accumulates chats; the map must not be a leak."""
    from navig.agent.tools import todo_tools

    todo_tools.reset_todo_lists()
    for i in range(todo_tools.MAX_TRACKED_SESSIONS + 25):
        todo_tools.get_todo_list(f"chat:{i}")

    assert len(todo_tools._LISTS) <= todo_tools.MAX_TRACKED_SESSIONS

"""Registered is not offered — the model only sees tools in the turn's toolset.

`manage_skills` (#1069) and `todo_create`/`todo_update`/`todo_show` (#1078) were both
fixed to *register* correctly. They still reached no model on the live path:

    chat()  →  run_agentic(message, on_partial=…, tier_override=…, effort=…)

passes no `toolset`, so it takes the default ``"core"``, and
``get_openai_schemas(toolsets=…)`` filters on the registered entry's toolset. Measured
before this fix — `toolsets=["core"]` returned exactly::

    bash_exec, get_plan_context, list_files, read_file, write_file

so all four were absent from every Telegram and deck turn. The registration guard
(`test_agent_tool_registrars_are_reachable`) cannot see this: the tools ARE registered.
This is the same "wired but not reachable" class one layer further out.

**Why these specifically must be on every turn, rather than router-suggested.** They are
stateful across turns. `suggest_toolsets` returns nothing for a short message (the URL and
research merges beside this one exist for exactly that reason), so a router-gated todo tool
could be offered in turn 1 and absent in turn 3 — the agent creates a plan and then cannot
progress or close it. A cross-turn tool that appears intermittently is worse than one that
never appears: the first leaves dangling state the model believes it recorded.

`get_plan_context` is the precedent — a meta/context tool already available on every turn.
"""

from __future__ import annotations

import pytest

#: Tools the agent needs regardless of what the message looks like.
ALWAYS_ON = ("manage_skills", "todo_create", "todo_update", "todo_show")


@pytest.fixture(scope="module")
def offered_under_default_toolset() -> set[str]:
    """The tool names a plain turn actually puts in front of the model."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.conv.agent import default_turn_toolsets
    from navig.agent.tools import register_all_tools

    register_all_tools()
    schemas = _AGENT_REGISTRY.get_openai_schemas(toolsets=default_turn_toolsets("hello"))
    return {s.get("function", s).get("name") for s in schemas}


def test_the_probe_sees_a_real_toolset(offered_under_default_toolset):
    """A floor. If the resolver returned everything, the assertions below are vacuous."""
    offered = offered_under_default_toolset
    assert "bash_exec" in offered, "the baseline core tools are missing — probe is wrong"
    assert "navig_db_query" not in offered, (
        "the default turn is offering the devops toolset, so this file would pass no "
        "matter what — the resolver is not filtering"
    )


@pytest.mark.parametrize("tool", ALWAYS_ON)
def test_the_always_on_tools_are_offered(offered_under_default_toolset, tool):
    assert tool in offered_under_default_toolset, (
        f"{tool} is registered but NOT offered to the model on a normal turn, so it can "
        "never be called. Registration alone is not reachability — the turn's toolset "
        "decides what the model sees."
    )


def test_a_url_still_widens_the_toolset():
    """The always-on merge must not displace the existing conditional merges."""
    from navig.agent.conv.agent import default_turn_toolsets

    assert "search" in default_turn_toolsets("look at https://example.com")
    assert "search" not in default_turn_toolsets("hello")


def test_the_research_tier_still_gets_browser():
    from navig.agent.conv.agent import default_turn_toolsets

    ts = default_turn_toolsets("who is x", tier_override="research")
    assert {"research", "browser"} <= set(ts)


def test_an_explicit_toolset_still_gets_the_meta_tools():
    """`navig agent --toolset devops` must not lose its plan/skill tools."""
    from navig.agent.conv.agent import default_turn_toolsets

    ts = default_turn_toolsets("hello", toolset="devops")
    assert "devops" in ts
    assert {"skills", "todo"} <= set(ts)

"""A tool the operator switched off must not be advertised to the model either.

`tools.blocked_tools` is documented as blocking a tool **entirely**, and
`operator_blocked_tools()` says why it is read by the code rather than passed in:

    a control that only applies where somebody remembered to thread an argument
    through is the shape of bug this exists to close

It was consulted in exactly two places — `get_openai_schemas` (so the model gets no
schema) and `dispatch` (so a call is refused). It was **not** consulted by
`available_names`, and that list is formatted straight into the system prompt
(`conv/agent.py`) and into the planner's prompt (`plan_execute.py`).

So with `bash_exec` blocked, measured before this fix::

    available_names(['core']) -> ['bash_exec', 'get_plan_context', 'list_files', …]
    get_openai_schemas(['core']) -> [ get_plan_context, list_files, … ]   # no bash_exec

The prompt told the model *"you have bash_exec"* while withholding the schema. Two
costs, and the second is the worse one: the model spends turns calling a tool that
dispatch refuses, and — because the same list is how it answers "what can you do?" — it
tells the **operator** it can run shell commands they explicitly disabled.

`get_openai_schemas` already carries the reasoning in a comment ("don't advertise a tool
the operator switched off … offering it means the model keeps calling it and spending a
turn to be told no"). This applies it to the other readers instead of leaving one door
hardened and the rest open.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def registry():
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_all_tools

    register_all_tools()
    return _AGENT_REGISTRY


@pytest.fixture
def block(monkeypatch):
    """Set the operator's standing policy, the way config would."""

    def _set(*names: str):
        import navig.agent.tool_permissions as tp

        blocked = frozenset(names)
        # Patch the DEFINING module only. The registry imports the symbol inside each
        # function body, so that is what a call resolves through — and a second
        # `setattr(registry_module, …, raising=False)` would invent an attribute nothing
        # reads, i.e. a phantom target that makes the test look more thorough than it is.
        monkeypatch.setattr(tp, "operator_blocked_tools", lambda: blocked)
        return blocked

    return _set


def test_the_schema_layer_still_withholds_it(registry, block):
    """Control. If this ever fails the rest of the file proves nothing."""
    block("bash_exec")
    offered = {
        s["function"]["name"] for s in registry.get_openai_schemas(toolsets=["core"])
    }
    assert "bash_exec" not in offered
    assert "read_file" in offered, "the block removed more than it should"


def test_the_prompt_list_does_not_advertise_a_blocked_tool(registry, block):
    """`available_names` is formatted into the system prompt verbatim."""
    block("bash_exec")
    names = registry.available_names(toolsets=["core"])

    assert "bash_exec" not in names, (
        "the system prompt advertises a tool the operator switched off — the model will "
        "call it and spend a turn being refused, and will tell the operator it can do "
        "something they disabled"
    )
    assert "read_file" in names, "unblocked tools must still be listed"


def test_an_unblocked_registry_is_unchanged(registry, block):
    """The overwhelmingly common case: no policy, nothing filtered."""
    block()  # empty policy
    names = registry.available_names(toolsets=["core"])
    assert "bash_exec" in names


@pytest.mark.parametrize("blocked", [(), ("bash_exec",), ("bash_exec", "read_file")])
def test_what_the_prompt_names_is_exactly_what_the_model_can_call(
    registry, block, blocked
):
    """The invariant behind the bug, not just the one case that exposed it.

    `available_names` feeds the prompt and `get_openai_schemas` feeds the API call. They
    filter separately, so they can drift — and when they do, the gap is silent in both
    directions: a name in the prompt but not the schemas is a tool the model wastes turns
    on; a schema with no name in the prompt is a capability it may never think to use.
    """
    block(*blocked)
    named = set(registry.available_names(toolsets=["core"]))
    callable_ = {
        s["function"]["name"] for s in registry.get_openai_schemas(toolsets=["core"])
    }

    assert named == callable_, (
        f"the prompt and the tool schemas disagree with blocked={blocked!r}: "
        f"only in prompt={sorted(named - callable_)}, "
        f"only in schemas={sorted(callable_ - named)}"
    )


def test_the_capability_summary_drops_a_fully_blocked_toolset(registry, block):
    """"What the agent can ACTUALLY do right now" must not name a dead capability.

    The summary is per-TOOLSET, so this only shows when every tool in one is blocked —
    which is exactly what an operator disabling a capability does.
    """
    core = set(registry.available_names(toolsets=["core"]))
    assert core, "no core tools registered — the assertion below would be vacuous"

    block(*core)
    summary = registry.capability_summary(toolsets=["core"])

    assert summary == "", (
        f"every core tool is blocked and the prompt still claims the capability: {summary!r}"
    )


def test_the_summary_is_not_served_stale_after_the_policy_changes(registry, block):
    """The memo key must include the policy, or the fix above lasts one call.

    `capability_summary` is memoised on (revision, dynamic availability, toolsets,
    compact) for prompt-cache stability. Blocking is none of those, so without adding it
    the first call's answer would be replayed after the operator changes their mind.
    """
    block()
    before = registry.capability_summary(toolsets=["core"])
    assert before != "", "expected a capability line with no policy set"

    block(*registry.available_names(toolsets=["core"]))
    after = registry.capability_summary(toolsets=["core"])

    assert after != before, (
        "the summary was served from cache after the operator blocked every tool — the "
        "memo key does not include the blocking policy"
    )

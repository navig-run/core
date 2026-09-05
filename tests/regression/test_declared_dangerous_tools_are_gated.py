"""A plugin's tool must be able to classify itself as dangerous.

`DESTRUCTIVE_TOOLS` can only ever list names core knows at import time. A plugin
registers agent tools of its own — `navig-games` ships `games_claim`, which completes a
checkout on the operator's real store account with their vaulted session — and core
cannot enumerate those without hardcoding a plugin's vocabulary into the gate. So
`games_claim` ran ungated: it is not in the set, and its `owner_only = True` is an
*authorization* flag the approval gate has never read (~20 read-only devops tools set
it, so honouring it as "needs approval" would gate a pile of reads and train the
operator to click through).

`navig.tools.bridge` had already established `safety = "dangerous"` as the declaration;
nothing on the agent side read it. Registration now pushes it into the gate.

Polarity matters: this can only ever ADD names. A tool declaring itself *safe* is not
believed over `DESTRUCTIVE_TOOLS`.
"""

from __future__ import annotations

import pytest

from navig.agent.agent_tool_registry import AgentToolRegistry
from navig.tools.approval import (
    ApprovalPolicy,
    is_destructive_tool,
    needs_approval,
    set_approval_policy,
)


@pytest.fixture(autouse=True)
def _default_policy():
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)
    yield
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)


class _Tool:
    def __init__(self, name: str, safety: str | None = None) -> None:
        self.name = name
        self.description = "x"
        self.parameters = {"type": "object", "properties": {}}
        if safety is not None:
            self.safety = safety

    async def run(self, args, **kwargs):  # pragma: no cover - never invoked
        return None


def test_a_tool_declaring_dangerous_is_gated() -> None:
    reg = AgentToolRegistry()
    reg.register(_Tool("plugin_wipe_account", safety="dangerous"), toolset="p")

    assert needs_approval("plugin_wipe_account") is True
    assert is_destructive_tool("plugin_wipe_account") is True


def test_an_undeclared_plugin_tool_is_unchanged() -> None:
    """Defaulting every plugin tool to 'ask' would gate a pile of reads. A plugin is
    installed deliberately by the operator; a third-party MCP server is not."""
    reg = AgentToolRegistry()
    reg.register(_Tool("plugin_list_things"), toolset="p")

    assert needs_approval("plugin_list_things") is False


def test_declaring_safe_cannot_UNgate_a_known_destructive_tool() -> None:
    """The index only ever adds. A tool cannot talk its way out of the set."""
    reg = AgentToolRegistry()
    reg.register(_Tool("bash_exec", safety="safe"), toolset="p")

    assert needs_approval("bash_exec") is True


@pytest.mark.parametrize("declared", ["DANGEROUS", "Dangerous"])
def test_the_declaration_is_case_insensitive(declared) -> None:
    reg = AgentToolRegistry()
    reg.register(_Tool("plugin_x", safety=declared), toolset="p")

    assert needs_approval("plugin_x") is True


@pytest.mark.parametrize("declared", ["moderate", "safe", "", "yes", "true", 1, object()])
def test_only_dangerous_counts(declared) -> None:
    """Every other value — including truthy junk — leaves the tool unclassified rather
    than silently gating it."""
    reg = AgentToolRegistry()
    reg.register(_Tool("plugin_y", safety=declared), toolset="p")

    assert needs_approval("plugin_y") is False


def test_a_safety_level_enum_is_accepted() -> None:
    """`bridge.py` accepts a SafetyLevel as well as a string; so must this."""
    from navig.tools.router import SafetyLevel

    reg = AgentToolRegistry()
    reg.register(_Tool("plugin_z", safety=SafetyLevel.DANGEROUS), toolset="p")

    assert needs_approval("plugin_z") is True


def test_deregistering_drops_the_declaration() -> None:
    """A stale entry would let a later tool inherit a predecessor's classification."""
    reg = AgentToolRegistry()
    reg.register(_Tool("plugin_transient", safety="dangerous"), toolset="p")
    assert needs_approval("plugin_transient") is True

    reg.deregister("plugin_transient")
    assert needs_approval("plugin_transient") is False


def test_deregistering_a_toolset_drops_declarations() -> None:
    reg = AgentToolRegistry()
    reg.register(_Tool("plugin_a", safety="dangerous"), toolset="p")
    reg.register(_Tool("plugin_b", safety="dangerous"), toolset="p")

    assert reg.deregister_toolset("p") == 2
    assert needs_approval("plugin_a") is False
    assert needs_approval("plugin_b") is False


def test_a_tool_that_raises_on_safety_does_not_break_registration() -> None:
    """Classification is not worth a boot failure."""

    class _Hostile(_Tool):
        @property
        def safety(self):
            raise RuntimeError("boom")

    reg = AgentToolRegistry()
    reg.register(_Hostile("plugin_hostile"), toolset="p")

    assert reg.get_entry("plugin_hostile") is not None


# NOTE: the live instance this mechanism exists for — `navig-games`' `games_claim` —
# is asserted in that plugin's OWN suite (`plugins/navig-games/tests/`), not here.
#
# ⚠ A core test cannot check it: `navig_games` is a **copied** install
# (`site-packages/navig_games/…`), not an editable one, so importing it from `core/`
# resolves to whatever was last installed and a repo edit is invisible. Run from the
# plugin directory it resolves to the repo copy — which is exactly how the CI gate runs
# `plugin tests — navig-<name>`. Asserting a plugin's source from core would have been
# testing the installed artifact while reporting on the repo.

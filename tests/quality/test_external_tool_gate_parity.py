"""The classifier and the gate must not be able to disagree about a third-party tool.

Its sibling `test_agent_gate_parity.py` pins two registries of NAVIG's **own** tool
names against each other. Both are closed sets NAVIG writes, so every check there lives
inside the set — and that is exactly why neither caught this: nothing asked what happens
to a name in *neither* list. The answer was "it runs", because `is_destructive_tool` is
an allowlist inverted into a denylist and its default answer for anything new is "safe".

Two halves have to stay in step, and they are deliberately in different modules:

* :mod:`navig.mcp.trust` decides ``read`` vs ``action`` from the server's annotations —
  the only place that reads them;
* :mod:`navig.tools.approval` decides whether the operator sees a prompt, holding
  nothing but the tool's name.

The classification travels between them by a *push* at discovery, so `approval` stays
free of `navig.mcp` (it sits on the hot dispatch path). A push can be missed, so the
name itself also carries provenance: the ``mcp__`` prefix makes the gate deny by shape
whether or not the classifier ever ran. This test pins both halves and the prefix that
joins them.
"""

from __future__ import annotations

import pytest

from navig.mcp.trust import (
    TOOL_NAME_PREFIX,
    ClassificationSource,
    ServerTrust,
    ToolMode,
    catalog_revision,
    classify_tool,
)
from navig.tools.approval import (
    _EXTERNAL_TOOL_PREFIX,
    DESTRUCTIVE_TOOLS,
    ApprovalPolicy,
    needs_approval,
    record_external_tool,
    set_approval_policy,
)


@pytest.fixture(autouse=True)
def _default_policy():
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)
    yield
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)


def _catalog() -> list[dict]:
    """A catalog covering every annotation shape a server can send."""
    return [
        {"name": "list_issues", "annotations": {"readOnlyHint": True}},
        {"name": "create_issue", "annotations": {"readOnlyHint": False}},
        {"name": "unannotated"},
        {"name": "empty_annotations", "annotations": {}},
        {"name": "null_annotations", "annotations": None},
        {
            "name": "safe_write",
            "annotations": {"destructiveHint": False, "idempotentHint": True},
        },
        # The Python-specific trap: truthy but not `True`.
        {"name": "stringly_true", "annotations": {"readOnlyHint": "true"}},
        {"name": "numeric_true", "annotations": {"readOnlyHint": 1}},
    ]


def _classify_all(trust: ServerTrust) -> list:
    return [
        classify_tool(
            t["name"],
            server="acme",
            annotations=t.get("annotations"),
            trust=trust,
        )
        for t in _catalog()
    ]


# ---------------------------------------------------------------------------
# The two halves agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trust", [ServerTrust.BYO, ServerTrust.VETTED])
def test_classifier_and_gate_agree(trust) -> None:
    """Whatever the classifier decided is what the operator experiences."""
    disagreements = []
    for classified in _classify_all(trust):
        record_external_tool(
            classified.registry_name,
            mode=classified.mode.value,
            auto_approvable=classified.auto_approvable,
            server=classified.server,
        )
        expected = classified.mode is not ToolMode.READ
        actual = needs_approval(classified.registry_name)
        if actual != expected:
            disagreements.append(
                f"  {classified.wire_name}: classified {classified.mode.value}, "
                f"but needs_approval() == {actual}"
            )

    assert not disagreements, (
        "navig.mcp.trust and navig.tools.approval disagree about these tools. The "
        "classification is pushed into approval's index by record_external_tool(); if "
        "you changed either side, change both:\n" + "\n".join(disagreements)
    )


def test_an_unclassified_external_name_is_gated_by_shape() -> None:
    """The push can be missed; the prefix cannot. Fails closed twice."""
    assert needs_approval(f"{TOOL_NAME_PREFIX}never__recorded") is True


# ---------------------------------------------------------------------------
# The `is True` discipline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "True", 1, "yes", "on", [1], {"a": 1}])
def test_only_a_real_true_declares_a_read(value) -> None:
    """A server that did not follow the spec did not annotate, and an unannotated tool
    is an action.

    This is the test most likely to catch a future well-meaning refactor to
    ``coerce_bool``: that helper exists for the ``navig config set`` raw-string trap and
    accepts ``"true"``/``"on"``/``"yes"``. Correct for the *operator's* config; wrong
    for a remote party's JSON, where "close enough to true" is not a claim.
    """
    classified = classify_tool(
        "x", server="acme", annotations={"readOnlyHint": value}, trust=ServerTrust.VETTED
    )
    assert classified.mode is ToolMode.ACTION
    assert classified.classified_by is ClassificationSource.DEFAULT


def test_a_real_true_does_declare_a_read() -> None:
    classified = classify_tool(
        "x", server="acme", annotations={"readOnlyHint": True}, trust=ServerTrust.BYO
    )
    assert classified.mode is ToolMode.READ
    assert classified.classified_by is ClassificationSource.SERVER_ANNOTATION


def test_auto_approval_needs_a_vetted_endpoint() -> None:
    """A byo server cannot vouch for its own writes, however it annotates them."""
    annotations = {"destructiveHint": False, "idempotentHint": True}
    byo = classify_tool("w", server="acme", annotations=annotations, trust=ServerTrust.BYO)
    vetted = classify_tool(
        "w", server="acme", annotations=annotations, trust=ServerTrust.VETTED
    )

    assert byo.auto_approvable is False
    assert vetted.auto_approvable is True


@pytest.mark.parametrize(
    "annotations",
    [
        {"destructiveHint": False},  # idempotent not claimed
        {"idempotentHint": True},  # destructive not disclaimed
        {"destructiveHint": "false", "idempotentHint": "true"},  # stringly
        {},
    ],
)
def test_auto_approval_needs_every_claim(annotations) -> None:
    classified = classify_tool(
        "w", server="acme", annotations=annotations, trust=ServerTrust.VETTED
    )
    assert classified.auto_approvable is False


def test_honor_read_only_false_makes_everything_an_action() -> None:
    """The paranoid setting: no remote party decides what counts as a read."""
    classified = classify_tool(
        "r",
        server="acme",
        annotations={"readOnlyHint": True},
        trust=ServerTrust.VETTED,
        honor_read_only=False,
    )
    assert classified.mode is ToolMode.ACTION


# ---------------------------------------------------------------------------
# Reverse drift: first-party names must be untouched
# ---------------------------------------------------------------------------


def test_no_first_party_tool_is_treated_as_external() -> None:
    """Over-gating reads trains the operator to click through prompts, which is how a
    real prompt gets ignored."""
    from navig.tools.approval import is_external_tool

    misread = sorted(t for t in DESTRUCTIVE_TOOLS if is_external_tool(t))
    assert not misread, (
        f"These first-party tools look externally defined: {misread}. A first-party "
        f"tool must never be named with the {_EXTERNAL_TOOL_PREFIX!r} prefix."
    )


def test_first_party_read_tools_still_need_no_approval() -> None:
    for tool in ("read_file", "list_files", "search_code"):
        assert needs_approval(tool) is False, tool


def test_the_connector_shape_rule_still_matches() -> None:
    """Namespacing must not break `_DESTRUCTIVE_NAME_SUFFIXES`: if a prefixed name ever
    reached `startswith("connector_")`, every connector write tool would silently
    become ungated."""
    from navig.tools.approval import is_destructive_tool

    assert is_destructive_tool("connector_github_act") is True
    assert is_destructive_tool("connector_github_search") is False


# ---------------------------------------------------------------------------
# The duplicated constant
# ---------------------------------------------------------------------------


def test_the_prefix_is_the_same_on_both_sides() -> None:
    """`approval.py` duplicates the prefix rather than importing it, to stay free of
    `navig.mcp` on the hot dispatch path. Duplication is what makes drift possible, so
    this is the test that makes the duplication safe."""
    assert _EXTERNAL_TOOL_PREFIX == TOOL_NAME_PREFIX


# ---------------------------------------------------------------------------
# Catalog fingerprint
# ---------------------------------------------------------------------------


def test_catalog_revision_is_stable_and_order_independent() -> None:
    catalog = _catalog()
    assert catalog_revision(catalog) == catalog_revision(list(reversed(catalog)))


def test_catalog_revision_ignores_description_edits() -> None:
    """A copy edit must not fire the 'this server changed under us' signal."""
    before = [{"name": "a", "description": "Does a thing."}]
    after = [{"name": "a", "description": "Does a thing, nicely."}]
    assert catalog_revision(before) == catalog_revision(after)


@pytest.mark.parametrize(
    "annotations",
    [
        {"readOnlyHint": True},
        {"destructiveHint": False},
        {"idempotentHint": True},
    ],
)
def test_catalog_revision_reacts_to_every_policy_claim(annotations) -> None:
    """Each annotation a grant was decided against must move the fingerprint."""
    plain = [{"name": "a"}]
    changed = [{"name": "a", "annotations": annotations}]
    assert catalog_revision(plain) != catalog_revision(changed)


# ---------------------------------------------------------------------------
# The gate and the adversarial verifier must agree on "destructive"
# ---------------------------------------------------------------------------


def test_the_verifier_uses_the_predicate_not_the_raw_set() -> None:
    """`agent/conv/agent.py` decides whether to run the adversarial verifier before a
    tool call. It read `tool_call_item.name in DESTRUCTIVE_TOOLS` — the raw frozenset —
    which can only ever hold names core knows at import time.

    Three kinds of destructive tool are absent from it by construction: generated
    connector writes (`connector_*_act`), a plugin's self-declared
    `safety = "dangerous"`, and anything an external MCP server offers. So the gate held
    them and the verifier skipped them, and the two disagreed about what the word means.
    """
    import re
    from pathlib import Path

    import navig

    src = (Path(navig.__file__).parent / "agent/conv/agent.py").read_text(
        encoding="utf-8-sig"
    )
    offenders = [
        line.strip()
        for line in src.splitlines()
        if re.search(r"\bin DESTRUCTIVE_TOOLS\b", line) and not line.strip().startswith("#")
    ]
    assert not offenders, (
        "The verifier is reading DESTRUCTIVE_TOOLS directly again. Use "
        "`is_destructive_tool(name)`, which also covers the connector shape rule, "
        "self-declared plugin tools and external MCP names:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


@pytest.mark.parametrize(
    "tool,why",
    [
        ("connector_github_act", "generated connector write (shape rule)"),
        ("mcp__acme__delete_repo", "external MCP tool (prefix rule)"),
    ],
)
def test_the_predicate_covers_what_the_raw_set_cannot(tool, why) -> None:
    """Anti-vacuity for the guard above: these are exactly the names the raw set misses,
    so swapping the predicate in is load-bearing rather than cosmetic."""
    from navig.tools.approval import DESTRUCTIVE_TOOLS, is_destructive_tool

    record_external_tool(tool, mode="action", auto_approvable=False)
    assert tool not in DESTRUCTIVE_TOOLS, why
    assert is_destructive_tool(tool) is True, why


def test_a_self_declared_plugin_tool_is_destructive_for_both() -> None:
    from navig.agent.agent_tool_registry import AgentToolRegistry
    from navig.tools.approval import DESTRUCTIVE_TOOLS, is_destructive_tool

    class _Tool:
        name = "plugin_charges_a_card"
        description = "x"
        parameters = {"type": "object", "properties": {}}
        safety = "dangerous"

        async def run(self, args, **kwargs):  # pragma: no cover
            return None

    AgentToolRegistry().register(_Tool(), toolset="p")

    assert "plugin_charges_a_card" not in DESTRUCTIVE_TOOLS
    assert is_destructive_tool("plugin_charges_a_card") is True

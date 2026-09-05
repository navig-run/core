"""`manage_skills` was written, schema'd, documented and tested — and never registered.

Three independent reasons the agent could never call it:

1. ``register_skill_tools`` called ``_AGENT_REGISTRY.register_function(...)`` — a method
   that has **never existed** on the registry (it has ``register`` / ``register_entry``).
   The ``AttributeError`` was swallowed by ``except Exception`` at **debug** level, so
   nothing anywhere said so.
2. ``register_skill_tools`` had **zero production callers** — the only references were its
   own module docstring and its tests.
3. ``register_all_tools()`` lists 13 tool groups and ``skills`` was not among them.

The existing test could not catch any of it: it patched ``_AGENT_REGISTRY`` with a
``MagicMock``, and a MagicMock **has every attribute**, so ``register_function`` "worked".
It then asserted only that a module global had been set — never that a tool registered.

The neighbouring guard could not see it either. ``test_toolset_registry_parity`` asks
"does every tool NAMED IN A TOOLSET actually register?" — and ``manage_skills`` is named
in no toolset, so it is invisible from that direction. A guard scoped to one path does not
cover the surface.

The second half of this file is the part that matters more. ``force_activate`` mutates
in-memory sets on a **particular** ``SkillsContext`` instance, and the prompt is built from
whatever instance ``_build_skills_section`` resolves. If those are two different objects the
tool reports "force-activated. It will be included in the next turn" and the next turn does
not include it — a phantom success, and a worse failure than the tool simply being absent.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def registry():
    """The agent registry, with any skills registration undone afterwards.

    The registry is a process-wide singleton; leaving `manage_skills` in it would leak
    into every later test in the session.
    """
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    yield _AGENT_REGISTRY
    _AGENT_REGISTRY.deregister_toolset("skills")


def test_manage_skills_reaches_the_registry(registry):
    """The bug in one line: registering it left the registry unchanged."""
    from navig.agent.tools.skill_tools import register_skill_tools

    register_skill_tools()

    assert "manage_skills" in registry, (
        "register_skill_tools() ran without error and registered nothing — the classic "
        "shape here: a call into a method that does not exist, swallowed by a bare except. "
        "See navig/agent/tools/skill_tools.py::register_skill_tools."
    )


def test_the_hand_written_schema_still_matches_the_registered_one(registry):
    """Two descriptions of one tool drift; this makes the drift fail the build.

    `MANAGE_SKILLS_SCHEMA` predates the tool being a real `BaseTool` and is still exported
    (via `get_skill_schemas`). The registry now generates its own schema from
    `ManageSkillsTool.parameters`, so the model could be shown one contract while callers
    of the module constant read another.
    """
    from navig.agent.tools.skill_tools import MANAGE_SKILLS_SCHEMA, register_skill_tools

    register_skill_tools()
    generated = registry.get_entry("manage_skills").schema

    assert generated["name"] == MANAGE_SKILLS_SCHEMA["name"]
    hand = MANAGE_SKILLS_SCHEMA["parameters"]
    assert set(generated["parameters"]["properties"]) == set(hand["properties"]), (
        "the registered tool and the exported constant describe different parameters"
    )
    assert generated["parameters"].get("required") == hand.get("required"), (
        "the two schemas disagree about which parameters are required"
    )
    assert (
        generated["parameters"]["properties"]["action"].get("enum")
        == hand["properties"]["action"]["enum"]
    ), "the allowed actions drifted between the tool and the exported schema"


def test_register_all_tools_includes_the_skills_group(registry):
    """Reaching the registry is not enough if nothing calls the registrar."""
    from navig.agent.tools import register_all_tools

    register_all_tools()

    assert "manage_skills" in registry, (
        "register_all_tools() does not register the skills group, so the tool is absent "
        "from every live agent even though its registrar now works. Add it to the group "
        "list in navig/agent/tools/__init__.py."
    )


# ── the phantom-success half ───────────────────────────────────────────────────────


def test_activating_a_skill_changes_what_the_next_prompt_contains(tmp_path, monkeypatch):
    """A force-activation must land on the instance the PROMPT BUILDER reads.

    `force_activate` mutates in-memory sets. The tool held its own module-global context
    while `_build_skills_section` built a separate one per working dir — so the activation
    was recorded on an object nobody rendered from. The tool would answer "it will be
    included in the next turn" and the next turn would look exactly the same.
    """
    from navig.agent import skills_context as sc
    from navig.agent.tools.skill_tools import handle_manage_skills

    skill_dir = tmp_path / ".navig" / "skills"
    skill_dir.mkdir(parents=True)
    # No `activation_keywords`, so nothing auto-activates it — the exact situation the
    # tool exists for.
    (skill_dir / "kubernetes.md").write_text(
        "---\nname: Kubernetes Deploys\n---\n"
        "Always drain the node before a rolling restart.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sc, "_CONTEXTS", {}, raising=False)
    monkeypatch.setattr(
        "navig.spaces.active.get_active_working_dir", lambda: tmp_path, raising=False
    )

    def rendered() -> str:
        ctx = sc.get_skills_context(str(tmp_path))
        return ctx.format_for_system_prompt(ctx.activate(user_message="hello there"))

    assert "drain the node" not in rendered(), (
        "the fixture skill auto-activated, so this test cannot prove anything about "
        "force-activation — give it no activation_keywords"
    )

    out = handle_manage_skills(action="activate", skill_name="Kubernetes Deploys")
    assert "force-activated" in out, f"the tool refused the activation: {out}"

    assert "drain the node" in rendered(), (
        "the tool said 'force-activated. It will be included in the next turn' and the "
        "next turn does not include it — the activation landed on a different "
        "SkillsContext than the prompt is built from. That is a phantom success, and a "
        "worse failure than the tool being absent."
    )


def test_two_workspaces_do_not_share_forced_skills(tmp_path, monkeypatch):
    """The daemon serves many spaces from one process.

    A module-global context would hand space B whatever space A last activated — and the
    live prompt builder already carries a comment warning about precisely this ("relying
    on cwd would inject the wrong space's project skills").
    """
    from navig.agent import skills_context as sc

    monkeypatch.setattr(sc, "_CONTEXTS", {}, raising=False)

    a = tmp_path / "space-a"
    b = tmp_path / "space-b"
    for d in (a, b):
        (d / ".navig" / "skills").mkdir(parents=True)

    assert sc.get_skills_context(str(a)) is not sc.get_skills_context(str(b)), (
        "two different spaces share one SkillsContext — a skill forced on in one space "
        "would be forced on in every space the daemon serves"
    )

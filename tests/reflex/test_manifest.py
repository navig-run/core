"""Tool manifest: mandatory description, the shipped set, bindings validated against
the ONE command manifest (a tool may only point at a command that really exists)."""

from __future__ import annotations

import pytest

from navig.reflex.errors import ManifestError
from navig.reflex.manifest import (
    RISK_LEVELS,
    SlotSpec,
    ToolManifest,
    build_registry,
    default_registry,
    known_command_paths,
    validate_against_command_manifest,
)

SHIPPED = {
    "home.on",
    "home.off",
    "home.toggle",
    "home.set",
    "home.status",
    "tv.power",
    "tv.volume",
    "tv.play",
    "assistant.status",
    "campaigns.status",
    "coordination.inbox",
}


def test_the_shipped_registry_is_exactly_the_planned_tools() -> None:
    registry = default_registry()
    assert {tool.name for tool in registry.all()} == SHIPPED
    assert len(registry.all()) == 11


def test_every_tool_declares_a_description_patterns_and_a_command() -> None:
    for tool in default_registry().all():
        assert tool.description.strip(), tool.name
        assert tool.command, tool.name
        assert tool.patterns, tool.name
        assert tool.risk in RISK_LEVELS, tool.name
        payload = tool.as_dict()
        assert payload["description"] and payload["patterns"] and payload["command"]


def test_read_only_tools_are_exactly_the_status_queries() -> None:
    read_only = {tool.name for tool in default_registry().all() if tool.read_only}
    assert read_only == {"home.status", "assistant.status", "campaigns.status", "coordination.inbox"}


def test_registration_rejects_an_empty_description() -> None:
    registry = build_registry([])
    broken = ToolManifest(
        name="bad.tool",
        description="   ",
        command=("home", "on"),
        patterns=(r"^magic$",),
    )
    with pytest.raises(ManifestError, match="empty description"):
        registry.register(broken)


def test_registration_rejects_an_undeclared_risk() -> None:
    registry = build_registry([])
    broken = ToolManifest(
        name="bad.risk",
        description="declared with a risk word that is not in the vocabulary",
        command=("home", "on"),
        risk="spicy",
        patterns=(r"^magic$",),
    )
    with pytest.raises(ManifestError, match="risk"):
        registry.register(broken)


def test_registration_rejects_a_duplicate_name_and_a_duplicate_intent() -> None:
    registry = build_registry([])
    first = ToolManifest(
        name="a.tool", description="first", command=("home", "on"), intents=("a.intent",)
    )
    registry.register(first)
    with pytest.raises(ManifestError, match="already registered"):
        registry.register(
            ToolManifest(name="a.tool", description="dup", command=("home", "on"))
        )
    with pytest.raises(ManifestError, match="intent"):
        registry.register(
            ToolManifest(
                name="b.tool", description="claims the same intent", command=("home", "off"),
                intents=("a.intent",),
            )
        )


def test_registration_rejects_a_tool_with_no_command_binding() -> None:
    registry = build_registry([])
    with pytest.raises(ManifestError, match="no command binding"):
        registry.register(ToolManifest(name="free.form", description="no command"))


def test_bindings_are_validated_against_the_real_command_manifest() -> None:
    """Every shipped tool must point at a command `navig --help` publishes."""
    validate_against_command_manifest(default_registry())


def test_an_unknown_command_binding_is_rejected() -> None:
    registry = build_registry(
        [
            ToolManifest(
                name="fake.tool",
                description="binds to a command that does not exist",
                command=("definitely", "not", "a", "command"),
                patterns=(r"^magic$",),
            )
        ]
    )
    with pytest.raises(ManifestError, match="not present in the command manifest"):
        registry.validate_bindings(known_command_paths())


def test_registry_answers_intents_and_manifests() -> None:
    registry = default_registry()
    tool = registry.for_intent("home.off")
    assert tool is not None and tool.name == "home.off"
    assert registry.get("nope") is None
    manifests = registry.manifests()
    assert len(manifests) == 11
    slots = manifests[0]["slots"]
    assert slots and {"name", "type", "required"} <= set(slots[0])


def test_slot_spec_serializes() -> None:
    assert SlotSpec("value", "value", required=True).as_dict() == {
        "name": "value",
        "type": "value",
        "required": True,
    }

"""
Unit tests for the A2A Agent Card builder (navig.mesh.agent_card).

Pure-function tests — no sockets, no gateway. Assert the card conforms to the
A2A AgentCard shape and that NAVIG mesh capabilities map to A2A skills.
"""

import time

import pytest

from navig.mesh.agent_card import (
    A2A_PROTOCOL_VERSION,
    build_agent_card,
)
from navig.mesh.registry import NodeRecord

pytestmark = pytest.mark.unit


def _record(**overrides) -> NodeRecord:
    base = {
        "node_id": "navig-linux-web01-a3f2",
        "hostname": "web01",
        "os": "linux",
        "gateway_url": "http://10.0.0.5:8789",
        "capabilities": ["llm", "shell", "docker"],
        "formation": "ops",
        "load": 0.2,
        "version": "1.4.0",
        "role": "leader",
        "epoch": 3,
        "last_seen": time.time(),
        "is_self": True,
    }
    base.update(overrides)
    return NodeRecord(**base)


def test_card_has_required_a2a_fields():
    card = build_agent_card(_record())
    for field in (
        "protocolVersion",
        "name",
        "description",
        "url",
        "version",
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "skills",
    ):
        assert field in card, f"missing required A2A field: {field}"
    assert card["protocolVersion"] == A2A_PROTOCOL_VERSION
    assert card["url"] == "http://10.0.0.5:8789/a2a"
    assert card["version"] == "1.4.0"


def test_capabilities_map_to_named_skills():
    card = build_agent_card(_record(capabilities=["llm", "shell", "docker"]))
    ids = {s["id"] for s in card["skills"]}
    assert ids == {"chat", "shell", "docker"}
    # Every skill carries the AgentSkill shape.
    for skill in card["skills"]:
        assert {"id", "name", "description", "tags"} <= set(skill)


def test_unknown_capability_degrades_gracefully():
    card = build_agent_card(_record(capabilities=["quantum_link"]))
    assert len(card["skills"]) == 1
    skill = card["skills"][0]
    assert skill["id"] == "quantum_link"
    assert skill["tags"] == ["quantum_link"]


def test_base_url_override_wins_over_gateway_url():
    card = build_agent_card(_record(), base_url="https://edge.navig.run/agent/")
    # Trailing slash trimmed; override used instead of the LAN gateway_url;
    # the A2A JSON-RPC path is always appended.
    assert card["url"] == "https://edge.navig.run/agent/a2a"


def test_navig_extension_carries_node_identity():
    card = build_agent_card(_record())
    ext = card["x-navig"]
    assert ext["nodeId"] == "navig-linux-web01-a3f2"
    assert ext["os"] == "linux"
    assert ext["role"] == "leader"
    assert ext["formation"] == "ops"


def test_empty_formation_is_null_not_blank():
    card = build_agent_card(_record(formation=""))
    assert card["x-navig"]["formation"] is None

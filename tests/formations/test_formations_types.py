"""Unit tests for navig.formations.types — pure dataclass + from_dict logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.formations.types import AgentSpec, ApiConnector, Formation, ProfileConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT_DICT: dict = {
    "id": "agent-alpha",
    "name": "Alpha",
    "role": "coordinator",
    "traits": ["analytical", "concise"],
    "personality": "Precise and data-driven.",
    "scope": ["monitoring", "alerts"],
    "system_prompt": "You are Alpha.",
}

_FORMATION_DICT: dict = {
    "id": "formation-1",
    "name": "Core Formation",
    "version": "1.0.0",
    "description": "Primary agent formation.",
    "agents": ["agent-alpha", "agent-beta"],
    "default_agent": "agent-alpha",
}


# ---------------------------------------------------------------------------
# AgentSpec
# ---------------------------------------------------------------------------


class TestAgentSpec:
    def test_direct_construction(self):
        spec = AgentSpec(
            id="a1",
            name="Test",
            role="worker",
            traits=["fast"],
            personality="Efficient.",
            scope=["db"],
            system_prompt="Be helpful.",
        )
        assert spec.id == "a1"
        assert spec.council_weight == 1.0
        assert spec.kpis == []
        assert spec.tools == []
        assert spec.api_dependencies == []
        assert spec.source_path is None

    def test_from_dict_minimal(self):
        spec = AgentSpec.from_dict(_AGENT_DICT)
        assert spec.id == "agent-alpha"
        assert spec.name == "Alpha"
        assert spec.role == "coordinator"
        assert spec.personality == "Precise and data-driven."
        assert spec.council_weight == 1.0

    def test_from_dict_traits(self):
        spec = AgentSpec.from_dict(_AGENT_DICT)
        assert "analytical" in spec.traits
        assert "concise" in spec.traits

    def test_from_dict_optional_fields(self):
        data = {**_AGENT_DICT, "kpis": ["uptime"], "council_weight": 2.5}
        spec = AgentSpec.from_dict(data)
        assert spec.kpis == ["uptime"]
        assert spec.council_weight == 2.5

    def test_from_dict_source_path(self):
        p = Path("/agents/alpha.json")
        spec = AgentSpec.from_dict(_AGENT_DICT, source_path=p)
        assert spec.source_path == p

    def test_to_dict_round_trip(self):
        spec = AgentSpec.from_dict(_AGENT_DICT)
        d = spec.to_dict()
        assert d["id"] == spec.id
        assert d["name"] == spec.name
        assert d["traits"] == spec.traits
        assert d["council_weight"] == spec.council_weight

    def test_to_dict_no_source_path(self):
        spec = AgentSpec.from_dict(_AGENT_DICT)
        d = spec.to_dict()
        assert "source_path" not in d

    def test_separate_mutable_defaults(self):
        a = AgentSpec.from_dict(_AGENT_DICT)
        b = AgentSpec.from_dict(_AGENT_DICT)
        a.kpis.append("metric")
        assert "metric" not in b.kpis


# ---------------------------------------------------------------------------
# ApiConnector
# ---------------------------------------------------------------------------


class TestApiConnector:
    def test_direct_construction(self):
        c = ApiConnector(
            name="stripe", type="rest_api", url_pattern="https://api.stripe.com/{endpoint}"
        )
        assert c.auth_type == "none"
        assert c.description == ""

    def test_from_dict_full(self):
        data = {
            "name": "github",
            "type": "graphql",
            "url_pattern": "https://api.github.com/graphql",
            "auth_type": "bearer",
            "description": "GitHub API",
        }
        c = ApiConnector.from_dict(data)
        assert c.name == "github"
        assert c.type == "graphql"
        assert c.auth_type == "bearer"
        assert c.description == "GitHub API"

    def test_from_dict_defaults(self):
        c = ApiConnector.from_dict({"name": "x"})
        assert c.type == "rest_api"
        assert c.url_pattern == ""
        assert c.auth_type == "none"


# ---------------------------------------------------------------------------
# Formation
# ---------------------------------------------------------------------------


class TestFormation:
    def test_from_dict_minimal(self):
        f = Formation.from_dict(_FORMATION_DICT)
        assert f.id == "formation-1"
        assert f.name == "Core Formation"
        assert f.default_agent == "agent-alpha"
        assert f.agents == ["agent-alpha", "agent-beta"]

    def test_from_dict_defaults(self):
        f = Formation.from_dict(_FORMATION_DICT)
        assert f.aliases == []
        assert f.api_connectors == []
        assert f.brief_templates == []
        assert f.source_path is None
        assert f.loaded_agents == {}

    def test_from_dict_with_api_connectors(self):
        data = {
            **_FORMATION_DICT,
            "api_connectors": [{"name": "gh", "url_pattern": "https://x"}],
        }
        f = Formation.from_dict(data)
        assert len(f.api_connectors) == 1
        assert f.api_connectors[0].name == "gh"

    def test_to_dict_keys(self):
        f = Formation.from_dict(_FORMATION_DICT)
        d = f.to_dict()
        for key in ("id", "name", "version", "description", "agents", "default_agent", "aliases"):
            assert key in d

    def test_to_dict_no_source_path(self):
        f = Formation.from_dict(_FORMATION_DICT)
        d = f.to_dict()
        assert "source_path" not in d
        assert "loaded_agents" not in d

    def test_to_dict_round_trip_agents(self):
        f = Formation.from_dict(_FORMATION_DICT)
        assert f.to_dict()["agents"] == ["agent-alpha", "agent-beta"]

    def test_from_dict_with_source_path(self):
        p = Path("/formations/core.json")
        f = Formation.from_dict(_FORMATION_DICT, source_path=p)
        assert f.source_path == p


# ---------------------------------------------------------------------------
# ProfileConfig
# ---------------------------------------------------------------------------


class TestProfileConfig:
    def test_from_dict_basic(self):
        pc = ProfileConfig.from_dict({"version": 1, "profile": "production"})
        assert pc.version == 1
        assert pc.profile == "production"
        assert pc.overrides == {}

    def test_from_dict_default_version(self):
        pc = ProfileConfig.from_dict({"profile": "staging"})
        assert pc.version == 1

    def test_from_dict_string_version(self):
        pc = ProfileConfig.from_dict({"version": "2", "profile": "dev"})
        assert pc.version == 2

    def test_from_dict_float_version(self):
        pc = ProfileConfig.from_dict({"version": 3.0, "profile": "test"})
        assert pc.version == 3

    def test_from_dict_overrides(self):
        pc = ProfileConfig.from_dict(
            {
                "version": 1,
                "profile": "staging",
                "overrides": {"timeout": 30},
            }
        )
        assert pc.overrides["timeout"] == 30

    def test_to_dict_minimal(self):
        pc = ProfileConfig(version=1, profile="prod")
        d = pc.to_dict()
        assert d == {"version": 1, "profile": "prod"}

    def test_to_dict_with_overrides(self):
        pc = ProfileConfig(version=2, profile="dev", overrides={"key": "val"})
        d = pc.to_dict()
        assert "overrides" in d
        assert d["overrides"]["key"] == "val"

    def test_to_dict_no_overrides_key_when_empty(self):
        pc = ProfileConfig(version=1, profile="prod", overrides={})
        d = pc.to_dict()
        assert "overrides" not in d

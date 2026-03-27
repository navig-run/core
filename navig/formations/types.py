"""
Formation System Types

Dataclasses for formations, agent specs, profiles, and connectors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentSpec:
    """A single agent definition loaded from .agent.json."""

    id: str
    name: str
    role: str
    traits: list[str]
    personality: str
    scope: list[str]
    system_prompt: str
    kpis: list[str] = field(default_factory=list)
    council_weight: float = 1.0
    api_dependencies: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], source_path: Path | None = None
    ) -> AgentSpec:
        return cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            traits=data["traits"],
            personality=data["personality"],
            scope=data["scope"],
            system_prompt=data["system_prompt"],
            kpis=data.get("kpis", []),
            council_weight=data.get("council_weight", 1.0),
            api_dependencies=data.get("api_dependencies", []),
            tools=data.get("tools", []),
            source_path=source_path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "traits": self.traits,
            "personality": self.personality,
            "scope": self.scope,
            "system_prompt": self.system_prompt,
            "kpis": self.kpis,
            "council_weight": self.council_weight,
            "api_dependencies": self.api_dependencies,
            "tools": self.tools,
        }


@dataclass
class ApiConnector:
    """Placeholder for external API connectors (Phase 2)."""

    name: str
    type: str  # "rest_api", "graphql", etc.
    url_pattern: str
    auth_type: str = "none"
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiConnector:
        return cls(
            name=data["name"],
            type=data.get("type", "rest_api"),
            url_pattern=data.get("url_pattern", ""),
            auth_type=data.get("auth_type", "none"),
            description=data.get("description", ""),
        )


@dataclass
class Formation:
    """A formation manifest loaded from formation.json."""

    id: str
    name: str
    version: str
    description: str
    agents: list[str]  # agent IDs
    default_agent: str
    aliases: list[str] = field(default_factory=list)
    api_connectors: list[ApiConnector] = field(default_factory=list)
    brief_templates: list = field(
        default_factory=list
    )  # List[str] or List[dict] — both formats supported
    source_path: Path | None = None
    loaded_agents: dict[str, AgentSpec] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], source_path: Path | None = None
    ) -> Formation:
        connectors = [ApiConnector.from_dict(c) for c in data.get("api_connectors", [])]
        return cls(
            id=data["id"],
            name=data["name"],
            version=data["version"],
            description=data["description"],
            agents=data["agents"],
            default_agent=data["default_agent"],
            aliases=data.get("aliases", []),
            api_connectors=connectors,
            brief_templates=data.get("brief_templates", []),
            source_path=source_path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "agents": self.agents,
            "default_agent": self.default_agent,
            "aliases": self.aliases,
            "api_connectors": [
                {
                    "name": c.name,
                    "type": c.type,
                    "url_pattern": c.url_pattern,
                    "auth_type": c.auth_type,
                    "description": c.description,
                }
                for c in self.api_connectors
            ],
            "brief_templates": self.brief_templates,
        }


@dataclass
class ProfileConfig:
    """Workspace profile configuration from .navig/profile.json."""

    version: int
    profile: str
    overrides: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileConfig:
        raw_ver = data.get("version", 1)
        # Accept both int and string versions for backward compat
        version = int(float(raw_ver)) if isinstance(raw_ver, str) else int(raw_ver)
        return cls(
            version=version,
            profile=data["profile"],
            overrides=data.get("overrides", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"version": self.version, "profile": self.profile}
        if self.overrides:
            d["overrides"] = self.overrides
        return d

"""
Agent JSON Config Loader — Optional agent.json definitions.

Loads optional agent.json files from agents/<agent_id>/agent.json.
If agent.json exists → use its llm_mode as the default for that agent.
If agent.json is absent → default llm_mode = "big_tasks" (no breakage).

Does NOT modify how SOUL.md, PERSONALITY.md, or PLAYBOOK.md are loaded.
This is additive — existing agents work unchanged.

Usage:
    from navig.agent_config_loader import load_agent_json, AgentJsonConfig

    cfg = load_agent_json("system_architect")
    if cfg:
        print(cfg.llm_mode)  # "coding"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("navig.agent_config_loader")

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator

    PYDANTIC_OK = True
except ImportError:
    PYDANTIC_OK = False

from navig.llm_router import CANONICAL_MODES

# ─────────────────────────────────────────────────────────────
# Pydantic Schema
# ─────────────────────────────────────────────────────────────

if PYDANTIC_OK:

    class AgentIdentity(BaseModel):
        """Agent identity/domain configuration."""

        domains: List[str] = Field(default_factory=list)
        philosophy: str = ""
        model_config = ConfigDict(extra="allow")

    class AgentVoice(BaseModel):
        """Agent voice/personality traits."""

        traits: List[str] = Field(default_factory=list)
        signature_phrases: List[str] = Field(default_factory=list)
        model_config = ConfigDict(extra="allow")

    class AgentJsonConfig(BaseModel):
        """
        Optional agent.json configuration.

        Fields:
            id: Unique agent identifier
            name: Display name
            role: Agent role
            archetype: Agent archetype
            llm_mode: Default LLM mode for this agent's calls
            identity: Domain and philosophy config
            voice: Personality traits and phrases
        """

        id: str = ""
        name: str = ""
        role: str = ""
        archetype: str = ""
        llm_mode: str = Field(default="big_tasks")
        identity: AgentIdentity = Field(default_factory=AgentIdentity)
        voice: AgentVoice = Field(default_factory=AgentVoice)

        model_config = ConfigDict(extra="allow")

        @field_validator("llm_mode")
        @classmethod
        def validate_llm_mode(cls, v: str) -> str:
            if v and v not in CANONICAL_MODES:
                logger.warning(
                    "Agent llm_mode '%s' not in canonical modes %s, defaulting to 'big_tasks'",
                    v,
                    CANONICAL_MODES,
                )
                return "big_tasks"
            return v

else:
    AgentJsonConfig = None
    AgentIdentity = None
    AgentVoice = None


# ─────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────

# Cache loaded configs
_agent_config_cache: Dict[str, Optional[AgentJsonConfig]] = {}


def load_agent_json(
    agent_id: str,
    search_paths: Optional[List[Path]] = None,
) -> Optional[AgentJsonConfig]:
    """
    Load an agent's optional agent.json configuration.

    Search order:
      1. agents/<agent_id>/agent.json (relative to project root)
      2. formations/*/agents/<agent_id>/agent.json
      3. ~/.navig/agents/<agent_id>/agent.json

    Args:
        agent_id: The agent identifier.
        search_paths: Additional paths to search.

    Returns:
        AgentJsonConfig if found and valid, None otherwise.
    """
    if not PYDANTIC_OK:
        return None

    if agent_id in _agent_config_cache:
        return _agent_config_cache[agent_id]

    paths_to_check = []

    # Project-root relative paths
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        base = Path(cm.base_dir) if hasattr(cm, "base_dir") else None
        if base:
            paths_to_check.append(base / "agents" / agent_id / "agent.json")
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Workspace paths
    cwd = Path.cwd()
    paths_to_check.extend(
        [
            cwd / "agents" / agent_id / "agent.json",
            cwd / "formations" / "**" / "agents" / agent_id / "agent.json",
        ]
    )

    # Global
    home = Path.home()
    paths_to_check.append(home / ".navig" / "agents" / agent_id / "agent.json")

    # Custom search paths
    if search_paths:
        for p in search_paths:
            paths_to_check.append(p / agent_id / "agent.json")

    # Try each path
    for path in paths_to_check:
        if "*" in str(path):
            # Glob pattern
            parent = str(path).split("*")[0]
            for found in Path(parent).rglob("*/agents/" + agent_id + "/agent.json"):
                cfg = _parse_agent_json(found, agent_id)
                if cfg:
                    return cfg
        elif path.exists():
            cfg = _parse_agent_json(path, agent_id)
            if cfg:
                return cfg

    # Not found — cache None
    _agent_config_cache[agent_id] = None
    logger.debug("No agent.json found for '%s', using defaults", agent_id)
    return None


def _parse_agent_json(path: Path, agent_id: str) -> Optional[AgentJsonConfig]:
    """Parse and validate a single agent.json file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = AgentJsonConfig.model_validate(data)
        _agent_config_cache[agent_id] = cfg
        logger.debug(
            "Loaded agent.json for '%s' from %s (llm_mode=%s)",
            agent_id,
            path,
            cfg.llm_mode,
        )
        return cfg
    except Exception as e:
        logger.warning("Failed to parse agent.json at %s: %s", path, e)
        return None


def get_agent_llm_mode(agent_id: str) -> str:
    """
    Get the LLM mode for an agent.

    Returns the agent's configured llm_mode from agent.json,
    or "big_tasks" as default.
    """
    cfg = load_agent_json(agent_id)
    return cfg.llm_mode if cfg else "big_tasks"


def clear_agent_cache() -> None:
    """Clear the agent config cache."""
    _agent_config_cache.clear()

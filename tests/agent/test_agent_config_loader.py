"""Tests for navig.agent_config_loader — optional agent.json loader."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.agent_config_loader import (
    PYDANTIC_OK,
    AgentJsonConfig,
    clear_agent_cache,
    get_agent_llm_mode,
    load_agent_json,
)


pytestmark = pytest.mark.skipif(not PYDANTIC_OK, reason="Pydantic not installed")


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _write_agent_json(directory: Path, agent_id: str, data: dict) -> Path:
    """Write an agent.json fixture file and return its path."""
    agent_dir = directory / "agents" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    path = agent_dir / "agent.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────
# AgentJsonConfig model
# ──────────────────────────────────────────────────────────────


class TestAgentJsonConfig:
    def test_defaults(self):
        cfg = AgentJsonConfig()
        assert cfg.id == ""
        assert cfg.name == ""
        assert cfg.role == ""
        assert cfg.archetype == ""
        assert cfg.llm_mode == "big_tasks"

    def test_valid_llm_mode_accepted(self):
        from navig.llm_router import CANONICAL_MODES

        first_mode = list(CANONICAL_MODES)[0]
        cfg = AgentJsonConfig(llm_mode=first_mode)
        assert cfg.llm_mode == first_mode

    def test_invalid_llm_mode_falls_back_to_big_tasks(self):
        cfg = AgentJsonConfig(llm_mode="nonexistent_mode_xyz")
        assert cfg.llm_mode == "big_tasks"

    def test_full_config(self):
        cfg = AgentJsonConfig(
            id="devops",
            name="DevOps Agent",
            role="infrastructure",
            archetype="specialist",
            llm_mode="big_tasks",
        )
        assert cfg.id == "devops"
        assert cfg.name == "DevOps Agent"
        assert cfg.role == "infrastructure"

    def test_extra_fields_allowed(self):
        cfg = AgentJsonConfig.model_validate({"id": "test", "custom_field": "hello"})
        assert cfg.id == "test"

    def test_identity_defaults(self):
        cfg = AgentJsonConfig()
        assert cfg.identity.domains == []
        assert cfg.identity.philosophy == ""

    def test_voice_defaults(self):
        cfg = AgentJsonConfig()
        assert cfg.voice.traits == []
        assert cfg.voice.signature_phrases == []

    def test_identity_populated(self):
        cfg = AgentJsonConfig.model_validate(
            {"identity": {"domains": ["infra", "devops"], "philosophy": "automate everything"}}
        )
        assert "infra" in cfg.identity.domains
        assert cfg.identity.philosophy == "automate everything"

    def test_voice_populated(self):
        cfg = AgentJsonConfig.model_validate(
            {"voice": {"traits": ["concise", "direct"], "signature_phrases": ["Let's deploy."]}}
        )
        assert "concise" in cfg.voice.traits
        assert cfg.voice.signature_phrases == ["Let's deploy."]


# ──────────────────────────────────────────────────────────────
# load_agent_json
# ──────────────────────────────────────────────────────────────


class TestLoadAgentJson:
    def setup_method(self):
        clear_agent_cache()

    def test_returns_none_for_missing_agent(self, tmp_path):
        result = load_agent_json("does_not_exist_xyz", search_paths=[tmp_path])
        assert result is None

    def test_loads_from_search_path(self, tmp_path):
        _write_agent_json(
            tmp_path,
            "coder",
            {"id": "coder", "name": "Code Agent", "llm_mode": "big_tasks"},
        )
        result = load_agent_json("coder", search_paths=[tmp_path / "agents"])
        assert result is not None
        assert result.id == "coder"
        assert result.name == "Code Agent"

    def test_caches_result_on_second_call(self, tmp_path):
        _write_agent_json(tmp_path, "cached_agent", {"id": "cached_agent"})
        load_agent_json("cached_agent", search_paths=[tmp_path / "agents"])
        # Second call should use cache, not re-read disk
        with patch("navig.agent_config_loader._parse_agent_json") as mock_parse:
            load_agent_json("cached_agent", search_paths=[tmp_path / "agents"])
            mock_parse.assert_not_called()

    def test_caches_none_for_missing_agent(self, tmp_path):
        load_agent_json("phantom", search_paths=[tmp_path])
        with patch("navig.agent_config_loader._parse_agent_json") as mock_parse:
            load_agent_json("phantom", search_paths=[tmp_path])
            mock_parse.assert_not_called()

    def test_invalid_json_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "bad_agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.json").write_text("not-json-at-all", encoding="utf-8")
        result = load_agent_json("bad_agent", search_paths=[tmp_path / "agents"])
        assert result is None

    def test_empty_json_object_uses_defaults(self, tmp_path):
        _write_agent_json(tmp_path, "empty_agent", {})
        result = load_agent_json("empty_agent", search_paths=[tmp_path / "agents"])
        assert result is not None
        assert result.llm_mode == "big_tasks"

    def test_llm_mode_from_file(self, tmp_path):
        _write_agent_json(tmp_path, "smart_agent", {"llm_mode": "big_tasks"})
        result = load_agent_json("smart_agent", search_paths=[tmp_path / "agents"])
        assert result is not None
        assert result.llm_mode == "big_tasks"


# ──────────────────────────────────────────────────────────────
# get_agent_llm_mode
# ──────────────────────────────────────────────────────────────


class TestGetAgentLlmMode:
    def setup_method(self):
        clear_agent_cache()

    def test_returns_big_tasks_for_unknown_agent(self, tmp_path):
        result = get_agent_llm_mode("totally_unknown_agent_zyx")
        assert result == "big_tasks"

    def test_returns_configured_mode(self, tmp_path):
        _write_agent_json(tmp_path, "mode_agent", {"llm_mode": "big_tasks"})
        with patch("navig.agent_config_loader.load_agent_json") as mock_load:
            mock_cfg = AgentJsonConfig(llm_mode="big_tasks")
            mock_load.return_value = mock_cfg
            result = get_agent_llm_mode("mode_agent")
        assert result == "big_tasks"

    def test_returns_big_tasks_when_load_returns_none(self):
        with patch("navig.agent_config_loader.load_agent_json", return_value=None):
            result = get_agent_llm_mode("any_agent")
        assert result == "big_tasks"


# ──────────────────────────────────────────────────────────────
# clear_agent_cache
# ──────────────────────────────────────────────────────────────


class TestClearAgentCache:
    def test_clear_allows_reload(self, tmp_path):
        _write_agent_json(tmp_path, "reload_agent", {"id": "reload_agent"})
        load_agent_json("reload_agent", search_paths=[tmp_path / "agents"])
        clear_agent_cache()
        # After clearing, load_agent_json should try parsing again
        with patch("navig.agent_config_loader._parse_agent_json") as mock_parse:
            mock_parse.return_value = None
            load_agent_json("reload_agent", search_paths=[tmp_path / "agents"])
            mock_parse.assert_called()

    def test_clear_empty_cache_is_safe(self):
        clear_agent_cache()
        clear_agent_cache()  # Should not raise

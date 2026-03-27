"""Tests for Agent JSON config loader."""

import json


class TestAgentConfigLoader:

    def test_agent_with_json(self, tmp_path):
        """Agent with agent.json → llm_mode correctly loaded."""
        from navig.agent_config_loader import clear_agent_cache, load_agent_json

        clear_agent_cache()

        agent_dir = tmp_path / "agents" / "test_agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.json").write_text(
            json.dumps(
                {
                    "id": "test_agent",
                    "name": "Test Agent",
                    "role": "tester",
                    "llm_mode": "coding",
                    "identity": {
                        "domains": ["testing"],
                        "philosophy": "test all things",
                    },
                    "voice": {
                        "traits": ["precise"],
                        "signature_phrases": ["test it"],
                    },
                }
            )
        )

        cfg = load_agent_json("test_agent", search_paths=[tmp_path / "agents"])
        assert cfg is not None
        assert cfg.id == "test_agent"
        assert cfg.llm_mode == "coding"
        assert cfg.name == "Test Agent"
        assert "testing" in cfg.identity.domains

    def test_agent_without_json(self, tmp_path):
        """Agent without agent.json → returns None (defaults to big_tasks)."""
        from navig.agent_config_loader import (
            clear_agent_cache,
            get_agent_llm_mode,
            load_agent_json,
        )

        clear_agent_cache()

        cfg = load_agent_json("nonexistent_agent", search_paths=[tmp_path / "agents"])
        assert cfg is None

        mode = get_agent_llm_mode("nonexistent_agent")
        assert mode == "big_tasks"

    def test_invalid_llm_mode_defaults(self, tmp_path):
        """Invalid llm_mode in agent.json → defaults to big_tasks."""
        from navig.agent_config_loader import clear_agent_cache, load_agent_json

        clear_agent_cache()

        agent_dir = tmp_path / "agents" / "bad_mode"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.json").write_text(
            json.dumps(
                {
                    "id": "bad_mode",
                    "llm_mode": "invalid_mode_name",
                }
            )
        )

        cfg = load_agent_json("bad_mode", search_paths=[tmp_path / "agents"])
        assert cfg is not None
        assert cfg.llm_mode == "big_tasks"

    def test_extra_fields_allowed(self, tmp_path):
        """Extra fields in agent.json don't cause errors."""
        from navig.agent_config_loader import clear_agent_cache, load_agent_json

        clear_agent_cache()

        agent_dir = tmp_path / "agents" / "extra"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.json").write_text(
            json.dumps(
                {
                    "id": "extra",
                    "llm_mode": "research",
                    "custom_field": True,
                    "another": {"nested": "value"},
                }
            )
        )

        cfg = load_agent_json("extra", search_paths=[tmp_path / "agents"])
        assert cfg is not None
        assert cfg.llm_mode == "research"

    def test_malformed_json(self, tmp_path):
        """Malformed JSON returns None (not crash)."""
        from navig.agent_config_loader import clear_agent_cache, load_agent_json

        clear_agent_cache()

        agent_dir = tmp_path / "agents" / "broken"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.json").write_text("{broken json!!}")

        cfg = load_agent_json("broken", search_paths=[tmp_path / "agents"])
        assert cfg is None

    def test_get_agent_llm_mode_helper(self, tmp_path):
        """get_agent_llm_mode returns the configured mode or default."""
        from navig.agent_config_loader import clear_agent_cache, get_agent_llm_mode

        clear_agent_cache()

        # No agent.json → big_tasks
        mode = get_agent_llm_mode("unknown")
        assert mode == "big_tasks"

    def test_cache(self, tmp_path):
        """Results are cached after first load."""
        from navig.agent_config_loader import (
            _agent_config_cache,
            clear_agent_cache,
            load_agent_json,
        )

        clear_agent_cache()

        agent_dir = tmp_path / "agents" / "cached"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.json").write_text(
            json.dumps(
                {
                    "id": "cached",
                    "llm_mode": "summarize",
                }
            )
        )

        # First load
        cfg = load_agent_json("cached", search_paths=[tmp_path / "agents"])
        assert cfg.llm_mode == "summarize"
        assert "cached" in _agent_config_cache

        # Second load uses cache (even if file changes)
        cfg2 = load_agent_json("cached", search_paths=[tmp_path / "agents"])
        assert cfg2 is cfg  # Same object

"""
Tests for the NAVIG Formation System.

Covers:
- Schema validation (agent, formation, profile)
- Dynamic discovery (no hardcoded maps)
- Profile resolution chain
- Formation loading with agents
- Type dataclass serialization
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_formations(tmp_path):
    """Create a temporary formations root with one valid formation."""
    fm_dir = tmp_path / "test_formation"
    agents_dir = fm_dir / "agents"
    agents_dir.mkdir(parents=True)

    # Minimal valid formation manifest
    manifest = {
        "id": "test_formation",
        "name": "Test Formation",
        "version": "1.0.0",
        "description": "A test formation for unit tests",
        "agents": ["agent_a", "agent_b"],
        "default_agent": "agent_a",
        "aliases": ["test", "tf"],
    }
    (fm_dir / "formation.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Minimal valid agents
    for agent_id in ["agent_a", "agent_b"]:
        agent = {
            "id": agent_id,
            "name": f"Agent {agent_id.upper()}",
            "role": "Test Role",
            "traits": ["reliable"],
            "personality": "A dependable test agent.",
            "scope": ["testing"],
            "system_prompt": (
                "You are a test agent used for automated testing. "
                "Respond concisely and accurately to all queries. "
                "Focus on providing correct and verifiable answers."
            ),
            "kpis": ["accuracy"],
            "council_weight": 1.0,
            "tools": ["ai"],
        }
        (agents_dir / f"{agent_id}.agent.json").write_text(json.dumps(agent), encoding="utf-8")

    return tmp_path


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temp workspace with a .navig/profile.json."""
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir()
    profile = {"version": 1, "profile": "test_formation"}
    (navig_dir / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class TestTypes:
    def test_agent_spec_round_trip(self):
        from navig.formations.types import AgentSpec

        data = {
            "id": "x",
            "name": "X",
            "role": "R",
            "traits": ["a"],
            "personality": "P",
            "scope": ["s"],
            "system_prompt": "SP " * 20,
            "kpis": ["k"],
            "council_weight": 1.2,
            "tools": ["ai"],
        }
        agent = AgentSpec.from_dict(data)
        assert agent.id == "x"
        assert agent.council_weight == 1.2
        out = agent.to_dict()
        assert out["id"] == "x"
        assert out["council_weight"] == 1.2

    def test_formation_round_trip(self):
        from navig.formations.types import Formation

        data = {
            "id": "f1",
            "name": "F1",
            "version": "0.1.0",
            "description": "Desc",
            "agents": ["a1"],
            "default_agent": "a1",
            "aliases": ["alias1"],
        }
        fm = Formation.from_dict(data)
        assert fm.id == "f1"
        assert fm.aliases == ["alias1"]
        out = fm.to_dict()
        assert out["default_agent"] == "a1"

    def test_profile_config(self):
        from navig.formations.types import ProfileConfig

        pc = ProfileConfig.from_dict({"version": 1, "profile": "x"})
        assert pc.profile == "x"
        assert pc.version == 1

    def test_profile_config_string_version(self):
        """Backward compat: string version gets converted to int."""
        from navig.formations.types import ProfileConfig

        pc = ProfileConfig.from_dict({"version": "1.0", "profile": "y"})
        assert pc.version == 1
        assert pc.profile == "y"


# ---------------------------------------------------------------------------
# Schema Validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_valid_agent(self, tmp_formations):
        from navig.formations.schema import validate_agent_file

        agent_path = tmp_formations / "test_formation" / "agents" / "agent_a.agent.json"
        agent = validate_agent_file(agent_path)
        assert agent.id == "agent_a"

    def test_invalid_agent_missing_field(self, tmp_path):
        from navig.formations.schema import (
            FormationValidationError,
            validate_agent_file,
        )

        bad = tmp_path / "bad.agent.json"
        bad.write_text(json.dumps({"id": "x"}), encoding="utf-8")
        with pytest.raises(FormationValidationError):
            validate_agent_file(bad)

    def test_valid_formation(self, tmp_formations):
        from navig.formations.schema import validate_formation_file

        manifest = tmp_formations / "test_formation" / "formation.json"
        fm = validate_formation_file(manifest)
        assert fm.id == "test_formation"

    def test_formation_default_agent_not_in_agents(self, tmp_path):
        from navig.formations.schema import validate_formation_data

        data = {
            "id": "bad",
            "name": "Bad",
            "version": "1.0.0",
            "description": "Bad formation",
            "agents": ["a"],
            "default_agent": "z",
        }
        _, errors = validate_formation_data(data)
        assert any("default_agent" in e for e in errors)

    def test_valid_profile(self):
        from navig.formations.schema import validate_profile_data

        errors = validate_profile_data({"version": 1, "profile": "test"})
        assert errors == []

    def test_valid_profile_string_version(self):
        """Backward compat: string version should also validate."""
        from navig.formations.schema import validate_profile_data

        errors = validate_profile_data({"version": "1.0", "profile": "test"})
        assert errors == []

    def test_invalid_profile_missing_profile(self):
        from navig.formations.schema import validate_profile_data

        errors = validate_profile_data({"version": "1.0"})
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# Discovery & Loading
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_discover_formations(self, tmp_formations):
        from navig.formations.loader import (
            clear_formations_roots,
            discover_formations,
            set_formations_roots,
        )

        try:
            set_formations_roots([tmp_formations])
            fm_map = discover_formations()

            # Should find by id and aliases
            assert "test_formation" in fm_map
            assert "test" in fm_map
            assert "tf" in fm_map
        finally:
            clear_formations_roots()

    def test_discover_empty_dir(self, tmp_path):
        from navig.formations.loader import (
            clear_formations_roots,
            discover_formations,
            set_formations_roots,
        )

        try:
            set_formations_roots([tmp_path])
            fm_map = discover_formations()
            assert fm_map == {}
        finally:
            clear_formations_roots()

    def test_load_formation(self, tmp_formations):
        from navig.formations.loader import load_formation

        fm_dir = tmp_formations / "test_formation"
        fm = load_formation(fm_dir)
        assert fm is not None
        assert fm.id == "test_formation"
        assert len(fm.loaded_agents) == 2
        assert "agent_a" in fm.loaded_agents
        assert "agent_b" in fm.loaded_agents

    def test_read_profile(self, tmp_workspace):
        from navig.formations.loader import read_profile

        profile = read_profile(tmp_workspace)
        assert profile is not None
        assert profile.profile == "test_formation"

    def test_read_profile_missing(self, tmp_path):
        from navig.formations.loader import read_profile

        profile = read_profile(tmp_path)
        assert profile is None

    def test_resolve_formation(self, tmp_formations):
        from navig.formations.loader import (
            clear_formations_roots,
            discover_formations,
            resolve_formation,
            set_formations_roots,
        )

        try:
            set_formations_roots([tmp_formations])
            fm_map = discover_formations()
            result = resolve_formation("test", fm_map)
            assert result is not None

            # Unknown profile
            result2 = resolve_formation("nonexistent", fm_map)
            assert result2 is None
        finally:
            clear_formations_roots()

    def test_get_active_formation(self, tmp_formations, tmp_workspace):
        from navig.formations.loader import (
            clear_formations_roots,
            get_active_formation,
            set_formations_roots,
        )

        try:
            set_formations_roots([tmp_formations])
            fm = get_active_formation(tmp_workspace)
            assert fm is not None
            assert fm.id == "test_formation"
            assert len(fm.loaded_agents) == 2
        finally:
            clear_formations_roots()

    def test_list_available_formations(self, tmp_formations):
        from navig.formations.loader import (
            clear_formations_roots,
            list_available_formations,
            set_formations_roots,
        )

        try:
            set_formations_roots([tmp_formations])
            formations = list_available_formations()
            assert len(formations) == 1
            assert formations[0].id == "test_formation"
        finally:
            clear_formations_roots()

    def test_fallback_to_app_project(self, tmp_path):
        """When no profile.json exists, get_active_formation falls back to app_project."""
        from pathlib import Path

        from navig.formations.loader import (
            clear_formations_roots,
            get_active_formation,
            set_formations_roots,
        )

        # Point formation roots at the real built-in formations
        builtin = Path(__file__).parent.parent.parent / "store" / "formations"
        if not builtin.exists():
            pytest.skip("Built-in formations dir not found")

        try:
            set_formations_roots([builtin])
            # tmp_path has no .navig/profile.json → should fallback
            fm = get_active_formation(tmp_path)
            assert fm is not None
            assert fm.id == "navig_app"  # app_project alias resolves to navig_app
        finally:
            clear_formations_roots()

    def test_fallback_on_unknown_profile(self, tmp_path):
        """When profile references unknown formation, falls back to app_project."""
        from pathlib import Path

        from navig.formations.loader import (
            clear_formations_roots,
            get_active_formation,
            set_formations_roots,
        )

        builtin = Path(__file__).parent.parent.parent / "store" / "formations"
        if not builtin.exists():
            pytest.skip("Built-in formations dir not found")

        # Create profile.json pointing to nonexistent formation
        navig_dir = tmp_path / ".navig"
        navig_dir.mkdir()
        (navig_dir / "profile.json").write_text(
            json.dumps({"version": 1, "profile": "nonexistent_pack"}),
            encoding="utf-8",
        )

        try:
            set_formations_roots([builtin])
            fm = get_active_formation(tmp_path)
            assert fm is not None
            assert fm.id == "navig_app"  # fallback to app_project → navig_app
        finally:
            clear_formations_roots()


# ---------------------------------------------------------------------------
# Built-in Formations (Smoke Test)
# ---------------------------------------------------------------------------


class TestBuiltinFormations:
    """Validate the 4 built-in formations shipped with NAVIG."""

    FORMATIONS_DIR = Path(__file__).parent.parent.parent / "store" / "formations"

    @pytest.mark.parametrize(
        "formation_id,expected_agents",
        [
            ("creative_studio", 6),
            ("football_club", 6),
            ("government", 5),
            ("navig_app", 5),
        ],
    )
    def test_builtin_formation_loads(self, formation_id, expected_agents):
        from navig.formations.loader import load_formation

        fm_dir = self.FORMATIONS_DIR / formation_id
        if not fm_dir.exists():
            pytest.skip(f"Formation directory {fm_dir} not found")

        fm = load_formation(fm_dir)
        assert fm is not None, f"Failed to load {formation_id}"
        assert fm.id == formation_id
        assert len(fm.agents) == expected_agents
        assert len(fm.loaded_agents) == expected_agents, (
            f"Only loaded {len(fm.loaded_agents)}/{expected_agents} agents"
        )

    @pytest.mark.parametrize(
        "formation_id",
        ["creative_studio", "football_club", "government", "navig_app"],
    )
    def test_builtin_agents_have_system_prompts(self, formation_id):
        from navig.formations.loader import load_formation

        fm_dir = self.FORMATIONS_DIR / formation_id
        if not fm_dir.exists():
            pytest.skip(f"Formation directory {fm_dir} not found")

        fm = load_formation(fm_dir)
        assert fm is not None

        for agent_id, agent in fm.loaded_agents.items():
            assert len(agent.system_prompt) >= 100, (
                f"Agent {agent_id} system_prompt too short ({len(agent.system_prompt)} chars)"
            )

    def test_builtin_discovery(self):
        from navig.formations.loader import (
            clear_formations_roots,
            discover_formations,
            set_formations_roots,
        )

        if not self.FORMATIONS_DIR.exists():
            pytest.skip("Formations directory not found")

        try:
            set_formations_roots([self.FORMATIONS_DIR])
            fm_map = discover_formations()

            # Should find all 4 formations + their aliases
            assert "creative_studio" in fm_map
            assert "football_club" in fm_map
            assert "government" in fm_map
            assert "navig_app" in fm_map

            # Check aliases
            assert "creative" in fm_map
            assert "football" in fm_map
            assert "gov" in fm_map
            assert "app_project" in fm_map
        finally:
            clear_formations_roots()


# ---------------------------------------------------------------------------
# Council Engine (Unit tests - no AI calls)
# ---------------------------------------------------------------------------


class TestCouncil:
    def test_council_no_agents(self):
        from navig.formations.council import run_council
        from navig.formations.types import Formation

        fm = Formation(
            id="empty",
            name="Empty",
            version="1.0.0",
            description="Empty formation",
            agents=[],
            default_agent="none",
        )
        result = run_council(fm, "test question")
        assert "error" in result

    def test_council_with_mock_agents(self, tmp_formations):
        """Test council structure with mocked AI calls."""
        from navig.formations.council import run_council
        from navig.formations.loader import load_formation

        fm_dir = tmp_formations / "test_formation"
        fm = load_formation(fm_dir)
        assert fm is not None

        mock_response = "This is a test response.\nCONFIDENCE: 0.85"

        # Patch at the import location inside _call_agent
        with patch("navig.ai.ask_ai_with_context", return_value=mock_response, create=True):
            result = run_council(fm, "test question", rounds=1, timeout_per_agent=30)

        assert "question" in result
        assert result["question"] == "test question"
        assert "rounds" in result
        assert "agents_count" in result
        assert result["agents_count"] == 2

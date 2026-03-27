"""
Tests for Section 20 — Agent Goal Orchestration.

Covers:
  - GoalPlanner: CRUD lifecycle, decomposition, dependency tracking, persistence
  - Heart + GoalPlanner: wiring, _process_goals execution via Hands
  - Agent runner: GoalPlanner instantiation
  - Bug fixes: soul.py Tuple import, ai_client.py duplicate except,
    heart.py STARTUP_ORDER no 'memory'
  - Goal/Subtask serialization round-trips
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from navig.agent.goals import Goal, GoalPlanner, GoalState, Subtask, SubtaskState

# ═══════════════════════════════════════════════════════════════
# 1. GoalPlanner — CRUD lifecycle
# ═══════════════════════════════════════════════════════════════


class TestGoalPlannerLifecycle:
    """Tests for GoalPlanner add/get/list/cancel operations."""

    @pytest.fixture
    def planner(self, tmp_path):
        return GoalPlanner(storage_dir=tmp_path)

    def test_add_goal(self, planner):
        goal_id = planner.add_goal("Deploy to production")
        assert goal_id is not None
        goal = planner.get_goal(goal_id)
        assert goal.description == "Deploy to production"
        assert goal.state == GoalState.PENDING

    def test_add_goal_with_metadata(self, planner):
        goal_id = planner.add_goal("Test goal", metadata={"priority": "high"})
        goal = planner.get_goal(goal_id)
        assert goal.metadata == {"priority": "high"}

    def test_get_nonexistent_goal(self, planner):
        assert planner.get_goal("nonexistent") is None

    def test_list_goals_empty(self, planner):
        assert planner.list_goals() == []

    def test_list_goals_all(self, planner):
        planner.add_goal("Goal 1")
        planner.add_goal("Goal 2")
        goals = planner.list_goals()
        assert len(goals) == 2

    def test_list_goals_filtered_by_state(self, planner):
        id1 = planner.add_goal("Goal 1")
        planner.add_goal("Goal 2")
        planner.cancel_goal(id1)
        assert len(planner.list_goals(GoalState.PENDING)) == 1
        assert len(planner.list_goals(GoalState.CANCELLED)) == 1

    def test_cancel_goal(self, planner):
        goal_id = planner.add_goal("Cancel me")
        assert planner.cancel_goal(goal_id) is True
        goal = planner.get_goal(goal_id)
        assert goal.state == GoalState.CANCELLED
        assert goal.completed_at is not None

    def test_cancel_nonexistent(self, planner):
        assert planner.cancel_goal("nope") is False


# ═══════════════════════════════════════════════════════════════
# 2. GoalPlanner — Decomposition
# ═══════════════════════════════════════════════════════════════


class TestGoalPlannerDecomposition:
    """Tests for goal decomposition into subtasks."""

    @pytest.fixture
    def planner(self, tmp_path):
        return GoalPlanner(storage_dir=tmp_path)

    def test_decompose_goal(self, planner):
        goal_id = planner.add_goal("Deploy app")
        result = planner.decompose_goal(
            goal_id,
            [
                {
                    "description": "Backup database",
                    "command": "navig db dump mydb -o backup.sql",
                },
                {
                    "description": "Run migrations",
                    "command": "php artisan migrate",
                    "dependencies": [],
                },
                {"description": "Restart services", "command": "systemctl restart app"},
            ],
        )
        assert result is True
        goal = planner.get_goal(goal_id)
        assert len(goal.subtasks) == 3
        assert goal.subtasks[0].description == "Backup database"
        assert goal.subtasks[0].command == "navig db dump mydb -o backup.sql"

    def test_decompose_nonexistent_goal(self, planner):
        assert planner.decompose_goal("fake", [{"description": "nope"}]) is False

    def test_decompose_with_dependencies(self, planner):
        goal_id = planner.add_goal("Migrate DB")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Backup", "command": "backup"},
                {
                    "description": "Migrate",
                    "command": "migrate",
                    "dependencies": [f"{goal_id}-1"],
                },
                {
                    "description": "Verify",
                    "command": "verify",
                    "dependencies": [f"{goal_id}-2"],
                },
            ],
        )
        goal = planner.get_goal(goal_id)
        assert goal.subtasks[1].dependencies == [f"{goal_id}-1"]
        assert goal.subtasks[2].dependencies == [f"{goal_id}-2"]

    def test_subtask_ids_sequential(self, planner):
        goal_id = planner.add_goal("Test")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Step 1"},
                {"description": "Step 2"},
            ],
        )
        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].id == f"{goal_id}-1"
        assert goal.subtasks[1].id == f"{goal_id}-2"


# ═══════════════════════════════════════════════════════════════
# 3. GoalPlanner — Execution tracking
# ═══════════════════════════════════════════════════════════════


class TestGoalPlannerExecution:
    """Tests for goal start/complete/fail/get_next_subtask."""

    @pytest.fixture
    def planner_with_goal(self, tmp_path):
        planner = GoalPlanner(storage_dir=tmp_path)
        goal_id = planner.add_goal("Test execution")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Step 1", "command": "echo step1"},
                {
                    "description": "Step 2",
                    "command": "echo step2",
                    "dependencies": [f"{goal_id}-1"],
                },
            ],
        )
        return planner, goal_id

    def test_start_goal(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        assert planner.start_goal(goal_id) is True
        goal = planner.get_goal(goal_id)
        assert goal.state == GoalState.IN_PROGRESS
        assert goal.started_at is not None

    def test_start_goal_invalid_state(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        planner.cancel_goal(goal_id)
        assert planner.start_goal(goal_id) is False

    def test_get_next_subtask(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        planner.start_goal(goal_id)
        subtask = planner.get_next_subtask(goal_id)
        assert subtask is not None
        assert subtask.id == f"{goal_id}-1"

    def test_get_next_subtask_respects_dependencies(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        planner.start_goal(goal_id)
        # Step 2 depends on Step 1, so next should be Step 1
        subtask = planner.get_next_subtask(goal_id)
        assert subtask.id == f"{goal_id}-1"

    def test_get_next_subtask_after_dependency_met(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        planner.start_goal(goal_id)
        planner.complete_subtask(goal_id, f"{goal_id}-1", result="done")
        subtask = planner.get_next_subtask(goal_id)
        assert subtask is not None
        assert subtask.id == f"{goal_id}-2"

    def test_get_next_subtask_none_when_blocked(self, planner_with_goal):
        """Step 2 blocked by unfinished Step 1 — no next subtask beyond Step 1."""
        planner, goal_id = planner_with_goal
        planner.start_goal(goal_id)
        # Mark step 1 as in_progress (manually) — step 2 is still blocked
        goal = planner.get_goal(goal_id)
        goal.subtasks[0].state = SubtaskState.IN_PROGRESS
        subtask = planner.get_next_subtask(goal_id)
        # No PENDING subtask with met dependencies
        assert subtask is None

    def test_complete_subtask(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        planner.start_goal(goal_id)
        result = planner.complete_subtask(goal_id, f"{goal_id}-1", result="OK")
        assert result is True
        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].state == SubtaskState.COMPLETED
        assert goal.subtasks[0].result == "OK"
        assert goal.progress == 0.5

    def test_complete_all_subtasks_completes_goal(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        planner.start_goal(goal_id)
        planner.complete_subtask(goal_id, f"{goal_id}-1")
        planner.complete_subtask(goal_id, f"{goal_id}-2")
        goal = planner.get_goal(goal_id)
        assert goal.state == GoalState.COMPLETED
        assert goal.progress == 1.0

    def test_fail_subtask(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        planner.start_goal(goal_id)
        result = planner.fail_subtask(goal_id, f"{goal_id}-1", error="timeout")
        assert result is True
        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].state == SubtaskState.FAILED
        assert goal.subtasks[0].error == "timeout"
        assert goal.state == GoalState.BLOCKED

    def test_complete_nonexistent_subtask(self, planner_with_goal):
        planner, goal_id = planner_with_goal
        assert planner.complete_subtask(goal_id, "fake-id") is False
        assert planner.complete_subtask("fake-goal", "fake-id") is False


# ═══════════════════════════════════════════════════════════════
# 4. Goal/Subtask serialization
# ═══════════════════════════════════════════════════════════════


class TestGoalSerialization:
    """Tests for Goal/Subtask to_dict/from_dict round-trips."""

    def test_subtask_round_trip(self):
        subtask = Subtask(
            id="g1-1",
            description="Test step",
            command="echo hello",
            dependencies=["g1-0"],
        )
        d = subtask.to_dict()
        restored = Subtask.from_dict(d)
        assert restored.id == subtask.id
        assert restored.description == subtask.description
        assert restored.command == subtask.command
        assert restored.dependencies == subtask.dependencies
        assert restored.state == SubtaskState.PENDING

    def test_goal_round_trip(self):
        goal = Goal(
            id="g1",
            description="Test goal",
            subtasks=[
                Subtask(id="g1-1", description="Step 1"),
                Subtask(id="g1-2", description="Step 2", dependencies=["g1-1"]),
            ],
            metadata={"source": "test"},
        )
        d = goal.to_dict()
        restored = Goal.from_dict(d)
        assert restored.id == goal.id
        assert len(restored.subtasks) == 2
        assert restored.metadata == {"source": "test"}

    def test_goal_update_progress(self):
        goal = Goal(id="g1", description="Test")
        assert goal.update_progress() == 0.0  # no subtasks
        goal.subtasks = [
            Subtask(id="1", description="A", state=SubtaskState.COMPLETED),
            Subtask(id="2", description="B", state=SubtaskState.PENDING),
        ]
        assert goal.update_progress() == 0.5


# ═══════════════════════════════════════════════════════════════
# 5. Persistence
# ═══════════════════════════════════════════════════════════════


class TestGoalPlannerPersistence:
    """Tests for GoalPlanner save/load persistence."""

    def test_save_and_load(self, tmp_path):
        planner = GoalPlanner(storage_dir=tmp_path)
        goal_id = planner.add_goal("Persist me")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Step 1", "command": "cmd1"},
            ],
        )
        planner.start_goal(goal_id)

        # Create new planner instance from same dir
        planner2 = GoalPlanner(storage_dir=tmp_path)
        goal = planner2.get_goal(goal_id)
        assert goal is not None
        assert goal.description == "Persist me"
        assert goal.state == GoalState.IN_PROGRESS
        assert len(goal.subtasks) == 1

    def test_persistence_file_format(self, tmp_path):
        planner = GoalPlanner(storage_dir=tmp_path)
        planner.add_goal("Test")

        goals_file = tmp_path / "goals.json"
        assert goals_file.exists()
        data = json.loads(goals_file.read_text())
        assert "goals" in data
        assert "updated_at" in data
        assert len(data["goals"]) == 1


# ═══════════════════════════════════════════════════════════════
# 6. Heart + GoalPlanner wiring
# ═══════════════════════════════════════════════════════════════


class TestHeartGoalPlannerWiring:
    """Tests for Heart's GoalPlanner integration."""

    def test_heart_has_goal_planner_attr(self):
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)
        assert heart.goal_planner is None  # not set yet

    def test_set_goal_planner(self, tmp_path):
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)
        planner = GoalPlanner(storage_dir=tmp_path)
        heart.set_goal_planner(planner)
        assert heart.goal_planner is planner

    @pytest.mark.asyncio
    async def test_process_goals_no_planner(self):
        """_process_goals returns cleanly when no planner attached."""
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)
        # Should not raise
        await heart._process_goals()

    @pytest.mark.asyncio
    async def test_process_goals_executes_subtask(self, tmp_path):
        """_process_goals routes subtask commands through Hands."""
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)

        # Setup planner with a goal
        planner = GoalPlanner(storage_dir=tmp_path)
        goal_id = planner.add_goal("Test goal")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Echo hello", "command": "echo hello"},
            ],
        )
        planner.start_goal(goal_id)
        heart.set_goal_planner(planner)

        # Mock Hands component
        mock_hands = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "hello"
        mock_hands.execute = AsyncMock(return_value=mock_result)
        heart.register_component("hands", mock_hands)
        # Override is_running for the mock
        mock_hands.is_running = True
        mock_hands.state = MagicMock()
        mock_hands.state.name = "RUNNING"

        await heart._process_goals()

        # Verify Hands.execute was called
        mock_hands.execute.assert_called_once_with("echo hello")

        # Subtask should be completed
        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].state == SubtaskState.COMPLETED
        assert goal.subtasks[0].result == "hello"
        assert goal.state == GoalState.COMPLETED

    @pytest.mark.asyncio
    async def test_process_goals_handles_failure(self, tmp_path):
        """_process_goals marks subtask failed when Hands returns failure."""
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)

        planner = GoalPlanner(storage_dir=tmp_path)
        goal_id = planner.add_goal("Fail goal")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Bad cmd", "command": "false"},
            ],
        )
        planner.start_goal(goal_id)
        heart.set_goal_planner(planner)

        mock_hands = MagicMock()
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.stderr = "command not found"
        mock_result.exit_code = 1
        mock_hands.execute = AsyncMock(return_value=mock_result)
        heart.register_component("hands", mock_hands)
        mock_hands.is_running = True
        mock_hands.state = MagicMock()
        mock_hands.state.name = "RUNNING"

        await heart._process_goals()

        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].state == SubtaskState.FAILED
        assert "command not found" in goal.subtasks[0].error
        assert goal.state == GoalState.BLOCKED

    @pytest.mark.asyncio
    async def test_process_goals_no_hands(self, tmp_path):
        """Subtask fails if Hands component not available."""
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)

        planner = GoalPlanner(storage_dir=tmp_path)
        goal_id = planner.add_goal("No hands")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Step", "command": "echo test"},
            ],
        )
        planner.start_goal(goal_id)
        heart.set_goal_planner(planner)
        # Don't register hands

        await heart._process_goals()

        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].state == SubtaskState.FAILED
        assert "not available" in goal.subtasks[0].error

    @pytest.mark.asyncio
    async def test_process_goals_no_command_autocomplete(self, tmp_path):
        """Subtask with no command is auto-completed."""
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)

        planner = GoalPlanner(storage_dir=tmp_path)
        goal_id = planner.add_goal("Descriptive goal")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "A manual step"},  # No command
            ],
        )
        planner.start_goal(goal_id)
        heart.set_goal_planner(planner)

        await heart._process_goals()

        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].state == SubtaskState.COMPLETED
        assert "auto-completed" in goal.subtasks[0].result.lower()

    @pytest.mark.asyncio
    async def test_process_goals_hands_exception(self, tmp_path):
        """_process_goals catches Hands exceptions and fails subtask."""
        from navig.agent.config import AgentConfig
        from navig.agent.heart import Heart
        from navig.agent.nervous_system import NervousSystem

        ns = NervousSystem()
        config = AgentConfig()
        heart = Heart(config=config.heart, nervous_system=ns, agent_config=config)

        planner = GoalPlanner(storage_dir=tmp_path)
        goal_id = planner.add_goal("Exception goal")
        planner.decompose_goal(
            goal_id,
            [
                {"description": "Explode", "command": "boom"},
            ],
        )
        planner.start_goal(goal_id)
        heart.set_goal_planner(planner)

        mock_hands = MagicMock()
        mock_hands.execute = AsyncMock(side_effect=RuntimeError("connection lost"))
        heart.register_component("hands", mock_hands)
        mock_hands.is_running = True
        mock_hands.state = MagicMock()
        mock_hands.state.name = "RUNNING"

        await heart._process_goals()

        goal = planner.get_goal(goal_id)
        assert goal.subtasks[0].state == SubtaskState.FAILED
        assert "connection lost" in goal.subtasks[0].error


# ═══════════════════════════════════════════════════════════════
# 7. Agent runner — GoalPlanner integration
# ═══════════════════════════════════════════════════════════════


class TestAgentRunnerGoalPlanner:
    """Tests for Agent runner GoalPlanner wiring."""

    def test_agent_has_goal_planner(self):
        """Agent.__init__ creates and attaches GoalPlanner to Heart."""
        from navig.agent.config import AgentConfig
        from navig.agent.runner import Agent

        config = AgentConfig()
        config.workspace = Path(tempfile.mkdtemp())

        agent = Agent(config=config)
        assert hasattr(agent, "goal_planner")
        assert isinstance(agent.goal_planner, GoalPlanner)
        assert agent.heart.goal_planner is agent.goal_planner


# ═══════════════════════════════════════════════════════════════
# 8. Bug fix regressions
# ═══════════════════════════════════════════════════════════════


class TestBugFixRegressions:
    """Regression tests for Section 20 bug fixes."""

    def test_soul_tuple_import(self):
        """B1: soul.py should use typing.Tuple, not a shadow function."""
        import inspect

        from navig.agent.soul import Soul

        src = inspect.getsource(Soul.get_mood)
        # The annotation should reference Tuple properly
        assert "tuple[" in src
        # The shadow function should NOT exist
        # Check that 'Tuple' at module level is from typing (not a function)
        from typing import Tuple

        import navig.agent.soul as soul_mod

        # The module should NOT define a Tuple function
        assert not hasattr(soul_mod, "Tuple") or soul_mod.Tuple is Tuple

    def test_ai_client_no_duplicate_except(self):
        """B2: ai_client.py should not have duplicate except blocks."""
        import inspect

        import navig.agent.ai_client as mod

        src = inspect.getsource(mod.AIClient._init_model_router)
        # Count 'except Exception' occurrences — should be exactly 1
        count = src.count("except Exception")
        assert count == 1, f"Expected 1 'except Exception', found {count}"

    def test_heart_startup_order_no_memory(self):
        """G2: Heart.STARTUP_ORDER should not include 'memory'."""
        from navig.agent.heart import Heart

        assert "memory" not in Heart.STARTUP_ORDER

    def test_heart_startup_order_valid(self):
        """Heart.STARTUP_ORDER should list real component names."""
        from navig.agent.heart import Heart

        expected = ["nervous_system", "eyes", "ears", "hands", "brain", "soul"]
        assert Heart.STARTUP_ORDER == expected


# ═══════════════════════════════════════════════════════════════
# 9. GoalState / SubtaskState enums
# ═══════════════════════════════════════════════════════════════


class TestGoalStateEnums:
    """Verify enum completeness."""

    def test_goal_states(self):
        expected = {
            "pending",
            "decomposing",
            "in_progress",
            "blocked",
            "completed",
            "failed",
            "cancelled",
        }
        actual = {s.value for s in GoalState}
        assert actual == expected

    def test_subtask_states(self):
        expected = {"pending", "in_progress", "completed", "failed", "skipped"}
        actual = {s.value for s in SubtaskState}
        assert actual == expected


# ═══════════════════════════════════════════════════════════════
# 10. __init__.py exports
# ═══════════════════════════════════════════════════════════════


class TestAgentExports:
    """Verify GoalPlanner is properly exported from agent package."""

    def test_goal_planner_exported(self):
        from navig.agent import Goal, GoalPlanner, GoalState, Subtask, SubtaskState

        assert GoalPlanner is not None
        assert Goal is not None
        assert GoalState is not None
        assert Subtask is not None
        assert SubtaskState is not None

    def test_all_list_includes_goals(self):
        import navig.agent as agent_mod

        for name in ("GoalPlanner", "Goal", "GoalState", "Subtask", "SubtaskState"):
            assert name in agent_mod.__all__

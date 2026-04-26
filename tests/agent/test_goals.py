"""
Tests for navig.agent.goals — GoalState, SubtaskState, Subtask, Goal, GoalPlanner.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from navig.agent.goals import Goal, GoalPlanner, GoalState, Subtask, SubtaskState


# ─── GoalState ────────────────────────────────────────────────────────────────


def test_goal_state_values():
    assert GoalState.PENDING.value == "pending"
    assert GoalState.IN_PROGRESS.value == "in_progress"
    assert GoalState.COMPLETED.value == "completed"
    assert GoalState.FAILED.value == "failed"
    assert GoalState.CANCELLED.value == "cancelled"
    assert GoalState.BLOCKED.value == "blocked"
    assert GoalState.DECOMPOSING.value == "decomposing"


@pytest.mark.parametrize("state", list(GoalState))
def test_goal_state_round_trip(state):
    assert GoalState(state.value) is state


# ─── SubtaskState ─────────────────────────────────────────────────────────────


def test_subtask_state_values():
    expected = {"pending", "in_progress", "completed", "failed", "skipped"}
    actual = {s.value for s in SubtaskState}
    assert expected == actual


# ─── Subtask ──────────────────────────────────────────────────────────────────


def _make_subtask(**overrides) -> Subtask:
    defaults = dict(
        id="st-1",
        description="Run tests",
        command="pytest",
        dependencies=[],
        state=SubtaskState.PENDING,
    )
    defaults.update(overrides)
    return Subtask(**defaults)


def test_subtask_to_dict_basic():
    st = _make_subtask()
    d = st.to_dict()
    assert d["id"] == "st-1"
    assert d["description"] == "Run tests"
    assert d["command"] == "pytest"
    assert d["state"] == "pending"
    assert d["dependencies"] == []
    assert d["started_at"] is None
    assert d["completed_at"] is None
    assert d["error"] is None
    assert d["result"] is None


def test_subtask_from_dict_roundtrip():
    original = _make_subtask(
        state=SubtaskState.COMPLETED,
        error=None,
        result="all passed",
    )
    restored = Subtask.from_dict(original.to_dict())
    assert restored.id == original.id
    assert restored.state == original.state
    assert restored.result == "all passed"
    assert restored.description == original.description


def test_subtask_from_dict_partial():
    now = datetime.now()
    data = {
        "id": "x",
        "description": "desc",
        "state": "pending",
        "created_at": now.isoformat(),
    }
    st = Subtask.from_dict(data)
    assert st.command is None
    assert st.dependencies == []
    assert st.error is None


@pytest.mark.parametrize("state", list(SubtaskState))
def test_subtask_all_states_roundtrip(state):
    st = _make_subtask(state=state)
    restored = Subtask.from_dict(st.to_dict())
    assert restored.state == state


def test_subtask_with_dependencies():
    st = _make_subtask(dependencies=["st-0", "st-00"])
    d = st.to_dict()
    assert d["dependencies"] == ["st-0", "st-00"]
    restored = Subtask.from_dict(d)
    assert restored.dependencies == ["st-0", "st-00"]


# ─── Goal ─────────────────────────────────────────────────────────────────────


def _make_goal(**overrides) -> Goal:
    defaults = dict(
        id="goal-1",
        description="Deploy the app",
        state=GoalState.PENDING,
    )
    defaults.update(overrides)
    return Goal(**defaults)


def test_goal_defaults():
    g = _make_goal()
    assert g.subtasks == []
    assert g.progress == 0.0
    assert g.metadata == {}
    assert g.started_at is None
    assert g.completed_at is None


def test_goal_to_dict_basic():
    g = _make_goal()
    d = g.to_dict()
    assert d["id"] == "goal-1"
    assert d["description"] == "Deploy the app"
    assert d["state"] == "pending"
    assert d["subtasks"] == []
    assert d["progress"] == 0.0


def test_goal_from_dict_roundtrip():
    g = _make_goal(state=GoalState.IN_PROGRESS)
    restored = Goal.from_dict(g.to_dict())
    assert restored.id == g.id
    assert restored.state == GoalState.IN_PROGRESS
    assert restored.description == g.description


def test_goal_with_subtasks_roundtrip():
    st = _make_subtask()
    g = _make_goal()
    g.subtasks.append(st)
    d = g.to_dict()
    restored = Goal.from_dict(d)
    assert len(restored.subtasks) == 1
    assert restored.subtasks[0].id == "st-1"


@pytest.mark.parametrize("state", list(GoalState))
def test_goal_all_states_roundtrip(state):
    g = _make_goal(state=state)
    restored = Goal.from_dict(g.to_dict())
    assert restored.state == state


# ─── Goal.update_progress ─────────────────────────────────────────────────────


def test_update_progress_no_subtasks():
    g = _make_goal()
    assert g.update_progress() == 0.0


def test_update_progress_all_completed():
    g = _make_goal()
    for i in range(4):
        st = _make_subtask(id=f"st-{i}", state=SubtaskState.COMPLETED)
        g.subtasks.append(st)
    assert g.update_progress() == pytest.approx(1.0)


def test_update_progress_half_completed():
    g = _make_goal()
    for i in range(2):
        g.subtasks.append(_make_subtask(id=f"done-{i}", state=SubtaskState.COMPLETED))
    for i in range(2):
        g.subtasks.append(_make_subtask(id=f"pend-{i}", state=SubtaskState.PENDING))
    assert g.update_progress() == pytest.approx(0.5)


def test_update_progress_none_completed():
    g = _make_goal()
    for i in range(3):
        g.subtasks.append(_make_subtask(id=f"p-{i}", state=SubtaskState.PENDING))
    assert g.update_progress() == pytest.approx(0.0)


def test_update_progress_updates_attribute():
    g = _make_goal()
    g.subtasks.append(_make_subtask(state=SubtaskState.COMPLETED))
    g.subtasks.append(_make_subtask(id="st-2", state=SubtaskState.PENDING))
    g.update_progress()
    assert g.progress == pytest.approx(0.5)


# ─── GoalPlanner ──────────────────────────────────────────────────────────────


def test_goal_planner_init_empty(tmp_path):
    planner = GoalPlanner(storage_dir=tmp_path)
    assert planner._goals == {}
    assert planner.goals_file == tmp_path / "goals.json"


def test_goal_planner_storage_dir_created(tmp_path):
    new_dir = tmp_path / "deep" / "path"
    GoalPlanner(storage_dir=new_dir)
    assert new_dir.exists()


def test_goal_planner_save_creates_file(tmp_path):
    planner = GoalPlanner(storage_dir=tmp_path)
    g = _make_goal()
    planner._goals[g.id] = g
    planner._save_goals()
    assert (tmp_path / "goals.json").exists()


def test_goal_planner_save_and_reload(tmp_path):
    planner = GoalPlanner(storage_dir=tmp_path)
    g = _make_goal(id="persist-1", description="Persistent goal")
    planner._goals[g.id] = g
    planner._save_goals()

    planner2 = GoalPlanner(storage_dir=tmp_path)
    assert "persist-1" in planner2._goals
    assert planner2._goals["persist-1"].description == "Persistent goal"

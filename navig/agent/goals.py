"""
Goal Planning System for NAVIG Agent

Enables autonomous goal decomposition and execution tracking.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from navig.debug_logger import DebugLogger
from navig.platform.paths import config_dir


class GoalState(Enum):
    """State of a goal."""

    PENDING = "pending"
    DECOMPOSING = "decomposing"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubtaskState(Enum):
    """State of a subtask."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Subtask:
    """A single subtask of a goal."""

    id: str
    description: str
    command: str | None = None
    dependencies: list[str] = field(default_factory=list)
    state: SubtaskState = SubtaskState.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "command": self.command,
            "dependencies": self.dependencies,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
            "error": self.error,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subtask:
        return cls(
            id=data["id"],
            description=data["description"],
            command=data.get("command"),
            dependencies=data.get("dependencies", []),
            state=SubtaskState(data["state"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=(
                datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            error=data.get("error"),
            result=data.get("result"),
        )


@dataclass
class Goal:
    """A high-level goal to be achieved."""

    id: str
    description: str
    state: GoalState = GoalState.PENDING
    subtasks: list[Subtask] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: float = 0.0  # 0.0 to 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "state": self.state.value,
            "subtasks": [st.to_dict() for st in self.subtasks],
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
            "progress": self.progress,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Goal:
        return cls(
            id=data["id"],
            description=data["description"],
            state=GoalState(data["state"]),
            subtasks=[Subtask.from_dict(st) for st in data.get("subtasks", [])],
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=(
                datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
            ),
            progress=data.get("progress", 0.0),
            metadata=data.get("metadata", {}),
        )

    def update_progress(self) -> float:
        """Update and return progress (0.0 to 1.0)."""
        if not self.subtasks:
            self.progress = 0.0
            return self.progress

        completed = sum(1 for st in self.subtasks if st.state == SubtaskState.COMPLETED)
        self.progress = completed / len(self.subtasks)
        return self.progress


class GoalPlanner:
    """
    Goal planning and execution system.

    Manages goal decomposition, dependency tracking, and execution.
    """

    def __init__(self, storage_dir: Path | None = None):
        self.storage_dir = storage_dir or config_dir() / "workspace"
        self.goals_file = self.storage_dir / "goals.json"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.logger = DebugLogger()
        self._goals: dict[str, Goal] = {}
        self._load_goals()

    def _load_goals(self) -> None:
        """Load goals from storage."""
        if not self.goals_file.exists():
            return

        try:
            with open(self.goals_file, encoding="utf-8") as f:
                data = json.load(f)

            for goal_data in data.get("goals", []):
                goal = Goal.from_dict(goal_data)
                self._goals[goal.id] = goal

            self.logger.log_operation("goals", {"action": "load", "count": len(self._goals)})

        except Exception as e:
            self.logger.log_operation("goals", {"action": "load", "error": str(e)})

    def _save_goals(self) -> None:
        """Save goals to storage."""
        try:
            data = {
                "goals": [goal.to_dict() for goal in self._goals.values()],
                "updated_at": datetime.now().isoformat(),
            }

            _tmp_path: Path | None = None
            try:
                _fd, _tmp = tempfile.mkstemp(dir=self.goals_file.parent, suffix=".tmp")
                _tmp_path = Path(_tmp)
                with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                    json.dump(data, _fh, indent=2)
                os.replace(_tmp_path, self.goals_file)
                _tmp_path = None
            finally:
                if _tmp_path is not None:
                    _tmp_path.unlink(missing_ok=True)

            self.logger.log_operation("goals", {"action": "save", "count": len(self._goals)})

        except Exception as e:
            self.logger.log_operation("goals", {"action": "save", "error": str(e)})

    def add_goal(self, description: str, metadata: dict[str, Any] | None = None) -> str:
        """
        Add a new goal.

        Args:
            description: Goal description
            metadata: Optional metadata

        Returns:
            Goal ID
        """
        goal_id = str(uuid.uuid4())[:8]
        goal = Goal(id=goal_id, description=description, metadata=metadata or {})

        self._goals[goal_id] = goal
        self._save_goals()

        self.logger.log_operation(
            "goals", {"action": "add", "id": goal_id, "description": description}
        )
        return goal_id

    def get_goal(self, goal_id: str) -> Goal | None:
        """Get a goal by ID."""
        return self._goals.get(goal_id)

    def list_goals(self, state: GoalState | None = None) -> list[Goal]:
        """
        List goals, optionally filtered by state.

        Args:
            state: Optional state filter

        Returns:
            List of goals
        """
        goals = list(self._goals.values())

        if state:
            goals = [g for g in goals if g.state == state]

        # Sort by created_at descending
        goals.sort(key=lambda g: g.created_at, reverse=True)
        return goals

    def decompose_goal(self, goal_id: str, subtasks: list[dict[str, Any]]) -> bool:
        """
        Decompose a goal into subtasks.

        Args:
            goal_id: Goal ID
            subtasks: List of subtask definitions with:
                - description: Subtask description
                - command: Optional command to execute
                - dependencies: Optional list of subtask IDs this depends on

        Returns:
            True if successful
        """
        goal = self.get_goal(goal_id)
        if not goal:
            return False

        goal.state = GoalState.DECOMPOSING
        goal.subtasks = []

        for i, st_def in enumerate(subtasks):
            subtask_id = f"{goal_id}-{i + 1}"
            subtask = Subtask(
                id=subtask_id,
                description=st_def["description"],
                command=st_def.get("command"),
                dependencies=st_def.get("dependencies", []),
            )
            goal.subtasks.append(subtask)

        goal.state = GoalState.PENDING
        self._save_goals()

        self.logger.log_operation(
            "goals",
            {"action": "decompose", "id": goal_id, "subtask_count": len(goal.subtasks)},
        )
        return True

    def start_goal(self, goal_id: str) -> bool:
        """
        Start executing a goal.

        Args:
            goal_id: Goal ID

        Returns:
            True if started successfully
        """
        goal = self.get_goal(goal_id)
        if not goal or goal.state not in (GoalState.PENDING, GoalState.BLOCKED):
            return False

        goal.state = GoalState.IN_PROGRESS
        goal.started_at = datetime.now()
        self._save_goals()

        self.logger.log_operation("goals", {"action": "start", "id": goal_id})
        return True

    def complete_subtask(self, goal_id: str, subtask_id: str, result: str | None = None) -> bool:
        """
        Mark a subtask as completed.

        Args:
            goal_id: Goal ID
            subtask_id: Subtask ID
            result: Optional result description

        Returns:
            True if successful
        """
        goal = self.get_goal(goal_id)
        if not goal:
            return False

        subtask = next((st for st in goal.subtasks if st.id == subtask_id), None)
        if not subtask:
            return False

        subtask.state = SubtaskState.COMPLETED
        subtask.completed_at = datetime.now()
        subtask.result = result

        # Update goal progress
        goal.update_progress()

        # Check if goal is complete
        if all(st.state == SubtaskState.COMPLETED for st in goal.subtasks):
            goal.state = GoalState.COMPLETED
            goal.completed_at = datetime.now()

        self._save_goals()

        self.logger.log_operation(
            "goals",
            {
                "action": "complete_subtask",
                "goal_id": goal_id,
                "subtask_id": subtask_id,
                "progress": goal.progress,
            },
        )
        return True

    def fail_subtask(self, goal_id: str, subtask_id: str, error: str) -> bool:
        """
        Mark a subtask as failed.

        Args:
            goal_id: Goal ID
            subtask_id: Subtask ID
            error: Error description

        Returns:
            True if successful
        """
        goal = self.get_goal(goal_id)
        if not goal:
            return False

        subtask = next((st for st in goal.subtasks if st.id == subtask_id), None)
        if not subtask:
            return False

        subtask.state = SubtaskState.FAILED
        subtask.error = error

        # Mark goal as blocked
        goal.state = GoalState.BLOCKED

        self._save_goals()

        self.logger.log_operation(
            "goals",
            {
                "action": "fail_subtask",
                "goal_id": goal_id,
                "subtask_id": subtask_id,
                "error": error,
            },
        )
        return True

    def cancel_goal(self, goal_id: str) -> bool:
        """
        Cancel a goal.

        Args:
            goal_id: Goal ID

        Returns:
            True if cancelled
        """
        goal = self.get_goal(goal_id)
        if not goal:
            return False

        goal.state = GoalState.CANCELLED
        goal.completed_at = datetime.now()
        self._save_goals()

        self.logger.log_operation("goals", {"action": "cancel", "id": goal_id})
        return True

    def get_next_subtask(self, goal_id: str) -> Subtask | None:
        """
        Get the next executable subtask for a goal.

        Returns the first subtask that:
        1. Is in PENDING state
        2. Has all dependencies completed

        Args:
            goal_id: Goal ID

        Returns:
            Next subtask or None
        """
        goal = self.get_goal(goal_id)
        if not goal or goal.state != GoalState.IN_PROGRESS:
            return None

        for subtask in goal.subtasks:
            if subtask.state != SubtaskState.PENDING:
                continue

            # Check dependencies
            dependencies_met = all(
                any(st.id == dep_id and st.state == SubtaskState.COMPLETED for st in goal.subtasks)
                for dep_id in subtask.dependencies
            )

            if dependencies_met:
                return subtask

        return None

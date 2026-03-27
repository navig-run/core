"""
Contracts for the NAVIG installer.

InstallerModule  – protocol every module must satisfy
Action           – a single idempotent step (what to do + how to reverse it)
Result           – outcome of applying one Action
ModuleState      – enum of possible outcomes
InstallerContext – immutable shared state passed to every module
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class ModuleState(Enum):
    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


@dataclass
class Action:
    """A single atomic installer action."""

    id: str
    description: str
    module: str
    data: dict[str, Any] = field(default_factory=dict)
    reversible: bool = True
    undo_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    """Result of applying a single Action."""

    action_id: str
    state: ModuleState
    message: str = ""
    error: str | None = None
    undo_data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state in (ModuleState.APPLIED, ModuleState.SKIPPED)


@dataclass
class InstallerContext:
    """Shared context passed to every module plan/apply/rollback call."""

    profile: str
    dry_run: bool = False
    quiet: bool = False
    config_dir: Path = field(default_factory=lambda: Path.home() / ".navig")
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class InstallerModule(Protocol):
    """Every module in navig/installer/modules/ must expose these names."""

    name: str
    description: str

    def plan(self, ctx: InstallerContext) -> list[Action]:  # pragma: no cover
        ...

    def apply(
        self, action: Action, ctx: InstallerContext
    ) -> Result:  # pragma: no cover
        ...

    def rollback(
        self, action: Action, result: Result, ctx: InstallerContext
    ) -> None:  # pragma: no cover
        ...

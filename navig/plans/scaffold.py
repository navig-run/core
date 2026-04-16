"""
navig.plans.scaffold — Canonical ``.navig/`` directory structure scaffolding.

Creates the full plans directory tree with template files containing
canonical frontmatter schemas.  Safe to call repeatedly — only creates
missing directories and files, never overwrites existing content.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

# ── Directory tree ────────────────────────────────────────────

_DIRS: list[str] = [
    "inbox",
    "plans/milestones",
    "plans/phases",
    "plans/tasks/active",
    "plans/tasks/done",
    "plans/tasks/review",
    "plans/decisions",
    "staging",
]


# ── Template files ────────────────────────────────────────────

def _vision_template() -> str:
    return (
        "---\n"
        "title: Project Vision\n"
        f"created: {date.today().isoformat()}\n"
        "---\n\n"
        "# Vision\n\n"
        "What does this project aim to achieve?\n\n"
        "## Success Criteria\n\n"
        "- [ ] Define measurable outcome 1\n"
        "- [ ] Define measurable outcome 2\n"
    )


def _roadmap_template() -> str:
    return (
        "---\n"
        "title: Roadmap\n"
        f"created: {date.today().isoformat()}\n"
        "---\n\n"
        "# Roadmap\n\n"
        "## Milestones\n\n"
        "→ milestones/MVP1_initial.md\n"
    )


def _spec_template() -> str:
    return (
        "---\n"
        "title: Specification\n"
        f"created: {date.today().isoformat()}\n"
        "---\n\n"
        "# Spec\n\n"
        "## Architecture\n\n"
        "## Data Model\n\n"
        "## Constraints\n"
    )


def _current_phase_template() -> str:
    return (
        "---\n"
        "phase: 01\n"
        "title: Bootstrap\n"
        f"started: {date.today().isoformat()}\n"
        "milestone: MVP1\n"
        "status: active\n"
        "blocked_by: ~\n"
        "---\n\n"
        "## Objective\n\n"
        "Define and complete the initial project setup.\n\n"
        "## Acceptance Criteria\n\n"
        "- [ ] Canonical directory structure created\n"
        "- [ ] First milestone file present\n\n"
        "## Active Tasks\n\n"
        "## Decisions Made This Phase\n\n"
        "## Notes\n"
    )


def _milestone_template() -> str:
    return (
        "---\n"
        "milestone: MVP1\n"
        "title: Initial Milestone\n"
        "target: initial_milestone\n"
        "tasks_total: 0\n"
        "tasks_done: 0\n"
        "status: in_progress\n"
        "blocked_by: ~\n"
        "---\n\n"
        "# MVP1 — Initial Milestone\n\n"
        "## Tasks\n\n"
        "## Notes\n"
    )


_TEMPLATE_FILES: dict[str, tuple[str, str]] = {
    "plans/VISION.md": ("plans/VISION.md", "vision"),
    "plans/ROADMAP.md": ("plans/ROADMAP.md", "roadmap"),
    "plans/SPEC.md": ("plans/SPEC.md", "spec"),
    "plans/phases/CURRENT_PHASE.md": ("plans/phases/CURRENT_PHASE.md", "current_phase"),
    "plans/milestones/MVP1_initial_milestone.md": (
        "plans/milestones/MVP1_initial_milestone.md",
        "milestone",
    ),
}

_TEMPLATE_GENERATORS = {
    "vision": _vision_template,
    "roadmap": _roadmap_template,
    "spec": _spec_template,
    "current_phase": _current_phase_template,
    "milestone": _milestone_template,
}


def scaffold_plans_structure(root: Path) -> list[Path]:
    """Create the canonical ``.navig/`` plans directory tree.

    Parameters
    ----------
    root:
        Project root directory.  The ``.navig/`` folder is created here.

    Returns
    -------
    list[Path]
        Paths of newly created files (directories are not listed).
    """
    navig_dir = root / ".navig"
    created: list[Path] = []

    # Create directories
    for rel in _DIRS:
        (navig_dir / rel).mkdir(parents=True, exist_ok=True)

    # Create template files (never overwrite)
    from navig.core.yaml_io import atomic_write_text

    for rel_path, (_, template_key) in _TEMPLATE_FILES.items():
        target = navig_dir / rel_path
        if not target.exists():
            atomic_write_text(
                target,
                _TEMPLATE_GENERATORS[template_key](),
            )
            created.append(target)

    # Ensure staging/reconciliation_queue.json exists
    queue_file = navig_dir / "staging" / "reconciliation_queue.json"
    if not queue_file.exists():
        atomic_write_text(queue_file, "")
        created.append(queue_file)

    return created

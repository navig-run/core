"""
Context-aware skill activation for the agent system (FB-02).

Skills are Markdown files with optional YAML frontmatter that provide
domain-specific instructions to the LLM.  They are auto-activated based
on the current file paths and user message keywords.

Skill directories scanned (in priority order):
  1. <workspace>/.navig/skills/   (project-level, higher priority)
  2. ~/.navig/skills/              (global, lower priority)

Skill file format (any ``*.md`` file)::

    ---
    name: Django Best Practices
    activation_paths: ["*.py", "models.py"]
    activation_keywords: ["django", "queryset"]
    priority: 10
    ---
    # Django Best Practices
    Use select_related() for joins ...

Files *without* frontmatter are still loaded — the filename (stem) becomes
the skill name, and activation rules default to empty (manual-only).

Integration:
    The agent calls ``SkillsContext.activate()`` each turn with the current
    file paths and user message.  Matching skills are injected into the
    system prompt via ``format_for_system_prompt()``.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ACTIVE_SKILLS = 3
MAX_SKILL_CHARS = 8000  # per skill character budget (~2K tokens)
PROJECT_SKILLS_DIR = ".navig/skills"
GLOBAL_SKILLS_DIR = config_dir() / "skills"

# Scoring weights
PATH_MATCH_SCORE = 10
KEYWORD_MATCH_SCORE = 5


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ContextSkill:
    """A skill loaded for context-aware activation.

    Attributes:
        name:                Human-readable skill name.
        content:             Markdown body (instructions for the LLM).
        activation_paths:    Glob patterns matched against current file paths.
        activation_keywords: Keywords matched (case-insensitive) in user message.
        priority:            Static priority bonus (higher = more important).
        source:              ``"project"`` or ``"global"``.
        file_path:           Absolute path to the skill file on disk.
    """

    name: str
    content: str
    activation_paths: list[str] = field(default_factory=list)
    activation_keywords: list[str] = field(default_factory=list)
    priority: int = 0
    source: str = "project"  # "project" | "global"
    file_path: str = ""

    # -- helpers --------------------------------------------------------------

    def summary(self, max_len: int = 80) -> str:
        """One-line summary for listing purposes."""
        body = self.content.replace("\n", " ").strip()
        if len(body) > max_len:
            body = body[: max_len - 1] + "…"
        return body


# ---------------------------------------------------------------------------
# YAML frontmatter parsing
# ---------------------------------------------------------------------------


def _load_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter_dict, body_markdown)`` from a Markdown string."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        import yaml  # lazy; keep import fast for CLI path
    except ImportError:
        return {}, parts[2].strip()

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:  # noqa: BLE001
        fm = {}

    return fm, parts[2].strip()


def _parse_skill_file(path: Path, source: str) -> ContextSkill | None:
    """Parse a single ``.md`` file into a :class:`ContextSkill`.

    Returns ``None`` on unrecoverable I/O or parse error.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("skills_context: cannot read %s: %s", path, exc)
        return None

    fm, body = _load_frontmatter(text)

    # Truncate body to budget
    if len(body) > MAX_SKILL_CHARS:
        body = body[:MAX_SKILL_CHARS] + "\n…(truncated)"

    def _str_list(key: str) -> list[str]:
        val = fm.get(key, [])
        if isinstance(val, list):
            return [str(v) for v in val]
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        return []

    return ContextSkill(
        name=fm.get("name", path.stem) if fm else path.stem,
        content=body,
        activation_paths=_str_list("activation_paths"),
        activation_keywords=_str_list("activation_keywords"),
        priority=int(fm.get("priority", 0)),
        source=source,
        file_path=str(path),
    )


# ---------------------------------------------------------------------------
# SkillsContext — discovery + activation
# ---------------------------------------------------------------------------


class SkillsContext:
    """Discovers and scores skills for context-aware injection.

    Parameters:
        workspace_dir: Project root directory.
        max_active:    Maximum skills to inject per turn.
        extra_dirs:    Additional directories to scan.
    """

    def __init__(
        self,
        workspace_dir: str = ".",
        *,
        max_active: int = MAX_ACTIVE_SKILLS,
        extra_dirs: list[Path] | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.max_active = max_active
        self._extra_dirs = extra_dirs or []
        self._skills: list[ContextSkill] = []
        self._loaded = False
        self._force_activated: set[str] = set()
        self._force_deactivated: set[str] = set()

    # -- Discovery ------------------------------------------------------------

    def load(self) -> list[ContextSkill]:
        """Scan project and global skill directories.

        Returns the full list of discovered skills (regardless of activation).
        """
        skills: list[ContextSkill] = []

        # Project skills (higher priority — source="project")
        project_dir = self.workspace_dir / PROJECT_SKILLS_DIR
        if project_dir.is_dir():
            for f in sorted(project_dir.rglob("*.md")):
                skill = _parse_skill_file(f, source="project")
                if skill:
                    skills.append(skill)

        # Global skills (lower priority — source="global")
        if GLOBAL_SKILLS_DIR.is_dir():
            for f in sorted(GLOBAL_SKILLS_DIR.rglob("*.md")):
                skill = _parse_skill_file(f, source="global")
                if skill:
                    skills.append(skill)

        # Extra directories (e.g. test injection)
        for d in self._extra_dirs:
            if d.is_dir():
                for f in sorted(d.rglob("*.md")):
                    skill = _parse_skill_file(f, source="extra")
                    if skill:
                        skills.append(skill)

        self._skills = skills
        self._loaded = True
        logger.debug(
            "skills_context: loaded %d skills (%d project, %d global, %d extra)",
            len(skills),
            sum(1 for s in skills if s.source == "project"),
            sum(1 for s in skills if s.source == "global"),
            sum(1 for s in skills if s.source == "extra"),
        )
        return skills

    # -- Activation -----------------------------------------------------------

    def activate(
        self,
        current_files: list[str] | None = None,
        user_message: str = "",
    ) -> list[ContextSkill]:
        """Return the skills that should be active given current context.

        Scoring:
        - +10 per glob pattern that matches any current file path
        - +5 per keyword found (case-insensitive) in the user message
        - +priority (static bonus from frontmatter)

        Skills with score ≤ 0 are excluded unless force-activated.
        Force-deactivated skills are always excluded.
        """
        if not self._loaded:
            self.load()

        current_files = current_files or []
        msg_lower = user_message.lower()

        scored: list[tuple[ContextSkill, int]] = []

        for skill in self._skills:
            # Force-deactivation overrides everything
            if skill.name in self._force_deactivated:
                continue

            # Force-activation guarantees inclusion
            if skill.name in self._force_activated:
                scored.append((skill, 10_000))
                continue

            score = _compute_activation_score(skill, current_files, msg_lower)
            if score > 0:
                scored.append((skill, score))

        # Sort by score desc, then priority desc, then project before global
        scored.sort(
            key=lambda x: (
                x[1],
                x[0].priority,
                0 if x[0].source == "project" else -1,
            ),
            reverse=True,
        )

        return [s for s, _ in scored[: self.max_active]]

    # -- Force activation / deactivation --------------------------------------

    def force_activate(self, skill_name: str) -> bool:
        """Force a skill to be active regardless of scoring.

        Returns ``True`` if the skill exists.
        """
        self._force_deactivated.discard(skill_name)

        if not self._loaded:
            self.load()

        if any(s.name == skill_name for s in self._skills):
            self._force_activated.add(skill_name)
            return True
        return False

    def force_deactivate(self, skill_name: str) -> bool:
        """Force a skill to be excluded from activation.

        Returns ``True`` if the skill exists.
        """
        self._force_activated.discard(skill_name)

        if not self._loaded:
            self.load()

        if any(s.name == skill_name for s in self._skills):
            self._force_deactivated.add(skill_name)
            return True
        return False

    def reset_overrides(self) -> None:
        """Clear all force-activation and force-deactivation overrides."""
        self._force_activated.clear()
        self._force_deactivated.clear()

    # -- Formatting -----------------------------------------------------------

    def format_for_system_prompt(self, active_skills: list[ContextSkill]) -> str:
        """Render active skills as a system-prompt section."""
        if not active_skills:
            return ""

        parts = ["\n## Active Skills\n"]
        for skill in active_skills:
            source_tag = f" ({skill.source})" if skill.source != "project" else ""
            parts.append(f"### {skill.name}{source_tag}\n{skill.content}\n")
        return "\n".join(parts)

    # -- Queries --------------------------------------------------------------

    @property
    def all_skills(self) -> list[ContextSkill]:
        """Return all loaded skills."""
        if not self._loaded:
            self.load()
        return list(self._skills)

    @property
    def skill_count(self) -> int:
        """Total number of loaded skills."""
        if not self._loaded:
            self.load()
        return len(self._skills)

    def get_skill(self, name: str) -> ContextSkill | None:
        """Look up a skill by name (case-sensitive)."""
        if not self._loaded:
            self.load()
        for s in self._skills:
            if s.name == name:
                return s
        return None

    def reload(self) -> list[ContextSkill]:
        """Force a reload of all skill files from disk."""
        self._loaded = False
        return self.load()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _compute_activation_score(
    skill: ContextSkill,
    current_files: list[str],
    msg_lower: str,
) -> int:
    """Compute how relevant *skill* is given current context."""
    score = 0

    # Path matching — each pattern that matches any file adds PATH_MATCH_SCORE
    for pattern in skill.activation_paths:
        for file_path in current_files:
            # Match against basename and full path
            if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(
                Path(file_path).name, pattern
            ):
                score += PATH_MATCH_SCORE
                break  # one match per pattern is enough

    # Keyword matching
    for keyword in skill.activation_keywords:
        if keyword.lower() in msg_lower:
            score += KEYWORD_MATCH_SCORE

    # Priority bonus
    score += skill.priority

    return score

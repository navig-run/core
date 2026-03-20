"""
Skill Eligibility — filter skills by platform, safety, and invocability.

The Skill dataclass (navig.skills.loader) carries three enforcement fields
that were historically populated but never checked at injection time:

    platforms      List[str]  — "linux", "darwin", "windows", "all"
    safety         str        — "safe" | "elevated" | "destructive"
    user_invocable bool       — whether a human can directly invoke the skill

This module provides:

    SkillEligibilityContext  — describes the current execution environment
    is_eligible(skill, ctx)  — single-skill gate
    filter_skills(ids, all_skills, ctx) → List[str]  — bulk filter by ID list

Usage::

    from navig.skills.eligibility import SkillEligibilityContext, filter_skills
    from navig.skills.loader import load_all_skills

    skills = {s.id: s for s in load_all_skills()}
    ctx = SkillEligibilityContext.default()
    active_ids = filter_skills(list(skills), skills, ctx)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from navig.skills.loader import Skill


# =============================================================================
# Constants
# =============================================================================

_PLATFORM_MAP: Dict[str, str] = {
    "linux":  "linux",
    "darwin": "darwin",
    "win32":  "windows",
    "cygwin": "windows",
}

# Safety level ordering (lower index = less permissive)
_SAFETY_ORDER = ["safe", "elevated", "destructive"]


# =============================================================================
# SkillEligibilityContext
# =============================================================================

@dataclass
class SkillEligibilityContext:
    """
    Describes the current runtime environment used to decide whether a skill
    is eligible to be injected into the LLM context.

    Attributes:
        platform:           Current OS: "linux" | "darwin" | "windows" | "all"
        safety_max:         Maximum permitted safety level (inclusive).
                            "safe" only allows safe skills.
                            "elevated" allows safe + elevated.
                            "destructive" allows all.
        user_invocable_only: When True, skills with user_invocable=False are excluded.
        required_tags:      All listed tags must appear in skill.tags (AND logic).
        excluded_tags:      Any listed tag disqualifies the skill (OR logic).
    """
    platform: str = "all"
    safety_max: str = "elevated"
    user_invocable_only: bool = False
    required_tags: List[str] = field(default_factory=list)
    excluded_tags: List[str] = field(default_factory=list)

    # -- Constructors ---------------------------------------------------------

    @classmethod
    def default(cls) -> "SkillEligibilityContext":
        """Factory: sensible defaults for interactive agent sessions.

        Detects the current OS automatically.
        Allows safe + elevated skills, user-invocable and non-invocable.
        """
        current_platform = _PLATFORM_MAP.get(sys.platform, "all")
        return cls(
            platform=current_platform,
            safety_max="elevated",
            user_invocable_only=False,
        )

    @classmethod
    def permissive(cls) -> "SkillEligibilityContext":
        """Factory: no restrictions — all skills eligible.

        Useful for trusted daemon contexts or testing.
        """
        return cls(
            platform="all",
            safety_max="destructive",
            user_invocable_only=False,
        )

    @classmethod
    def strict(cls) -> "SkillEligibilityContext":
        """Factory: safe skills only, user-invocable only.

        Suitable for untrusted or sandboxed user-facing deployments.
        """
        current_platform = _PLATFORM_MAP.get(sys.platform, "all")
        return cls(
            platform=current_platform,
            safety_max="safe",
            user_invocable_only=True,
        )


# =============================================================================
# Single-skill gate
# =============================================================================

def is_eligible(skill: "Skill", ctx: SkillEligibilityContext) -> bool:
    """Return True if *skill* meets all criteria in *ctx*.

    Checks (in order, short-circuits on first failure):
    1. Platform match
    2. Safety ceiling
    3. user_invocable gate
    4. Required tags (AND)
    5. Excluded tags (OR)
    """
    # 1. Platform
    skill_platforms = [p.lower() for p in (skill.platforms or [])]
    if (
        skill_platforms
        and "all" not in skill_platforms
        and ctx.platform != "all"
        and ctx.platform not in skill_platforms
    ):
        return False

    # 2. Safety ceiling
    skill_safety = (skill.safety or "safe").lower()
    max_safety = ctx.safety_max.lower()

    try:
        skill_idx = _SAFETY_ORDER.index(skill_safety)
    except ValueError:
        # Unknown safety level — treat as elevated (conservative)
        skill_idx = 1

    try:
        max_idx = _SAFETY_ORDER.index(max_safety)
    except ValueError:
        max_idx = 1

    if skill_idx > max_idx:
        return False

    # 3. user_invocable gate
    if ctx.user_invocable_only and not skill.user_invocable:
        return False

    # 4. Required tags (all must be present)
    if ctx.required_tags:
        skill_tags = set(t.lower() for t in (skill.tags or []))
        for required in ctx.required_tags:
            if required.lower() not in skill_tags:
                return False

    # 5. Excluded tags (none may be present)
    if ctx.excluded_tags:
        skill_tags = set(t.lower() for t in (skill.tags or []))
        for excluded in ctx.excluded_tags:
            if excluded.lower() in skill_tags:
                return False

    return True


# =============================================================================
# Bulk filter
# =============================================================================

def filter_skills(
    skill_ids: List[str],
    all_skills: Dict[str, "Skill"],
    ctx: SkillEligibilityContext,
) -> List[str]:
    """
    Filter *skill_ids* to those whose Skill objects pass is_eligible().

    Args:
        skill_ids:  IDs to evaluate (typically from the conversation context).
        all_skills: Mapping of id → Skill (e.g. from load_all_skills()).
        ctx:        Eligibility context describing the runtime environment.

    Returns:
        Ordered list of IDs that remain eligible.
        IDs that are not present in *all_skills* are silently dropped.
    """
    result: List[str] = []
    for sid in skill_ids:
        skill = all_skills.get(sid)
        if skill is None:
            continue
        if is_eligible(skill, ctx):
            result.append(sid)
    return result

"""Hermetic unit tests for navig.skills.eligibility."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from navig.skills.eligibility import (
    SkillEligibilityContext,
    filter_skills,
    is_eligible,
)

# ---------------------------------------------------------------------------
# Minimal Skill stub (avoids importing loader)
# ---------------------------------------------------------------------------


@dataclass
class _Skill:
    id: str
    platforms: list[str] = field(default_factory=lambda: ["all"])
    safety: str = "safe"
    user_invocable: bool = True
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SkillEligibilityContext factories
# ---------------------------------------------------------------------------


class TestContextFactories:
    def test_permissive_allows_all_platforms(self):
        ctx = SkillEligibilityContext.permissive()
        assert ctx.platform == "all"
        assert ctx.safety_max == "destructive"
        assert ctx.user_invocable_only is False

    def test_strict_safe_and_invocable_only(self):
        ctx = SkillEligibilityContext.strict()
        assert ctx.safety_max == "safe"
        assert ctx.user_invocable_only is True

    def test_default_elevated_max(self):
        ctx = SkillEligibilityContext.default()
        assert ctx.safety_max == "elevated"
        assert ctx.user_invocable_only is False


# ---------------------------------------------------------------------------
# is_eligible — platform checks
# ---------------------------------------------------------------------------


class TestIsEligiblePlatform:
    def test_all_platform_skill_always_passes(self):
        skill = _Skill("s1", platforms=["all"])
        ctx = SkillEligibilityContext(platform="linux")
        assert is_eligible(skill, ctx)

    def test_matching_platform_passes(self):
        skill = _Skill("s1", platforms=["linux"])
        ctx = SkillEligibilityContext(platform="linux")
        assert is_eligible(skill, ctx)

    def test_mismatched_platform_fails(self):
        skill = _Skill("s1", platforms=["linux"])
        ctx = SkillEligibilityContext(platform="windows")
        assert not is_eligible(skill, ctx)

    def test_context_all_accepts_any_skill_platform(self):
        skill = _Skill("s1", platforms=["linux"])
        ctx = SkillEligibilityContext(platform="all")
        assert is_eligible(skill, ctx)

    def test_empty_platforms_list_always_passes(self):
        skill = _Skill("s1", platforms=[])
        ctx = SkillEligibilityContext(platform="linux")
        assert is_eligible(skill, ctx)


# ---------------------------------------------------------------------------
# is_eligible — safety ceiling
# ---------------------------------------------------------------------------


class TestIsEligibleSafety:
    def test_safe_skill_under_elevated_ceiling(self):
        skill = _Skill("s1", safety="safe")
        ctx = SkillEligibilityContext(safety_max="elevated")
        assert is_eligible(skill, ctx)

    def test_elevated_skill_under_elevated_ceiling(self):
        skill = _Skill("s1", safety="elevated")
        ctx = SkillEligibilityContext(safety_max="elevated")
        assert is_eligible(skill, ctx)

    def test_destructive_skill_blocked_by_elevated_ceiling(self):
        skill = _Skill("s1", safety="destructive")
        ctx = SkillEligibilityContext(safety_max="elevated")
        assert not is_eligible(skill, ctx)

    def test_destructive_skill_allowed_by_destructive_ceiling(self):
        skill = _Skill("s1", safety="destructive")
        ctx = SkillEligibilityContext(safety_max="destructive")
        assert is_eligible(skill, ctx)

    def test_elevated_skill_blocked_by_safe_ceiling(self):
        skill = _Skill("s1", safety="elevated")
        ctx = SkillEligibilityContext(safety_max="safe")
        assert not is_eligible(skill, ctx)


# ---------------------------------------------------------------------------
# is_eligible — user_invocable gate
# ---------------------------------------------------------------------------


class TestIsEligibleUserInvocable:
    def test_non_invocable_blocked_when_gate_on(self):
        skill = _Skill("s1", user_invocable=False)
        ctx = SkillEligibilityContext(user_invocable_only=True)
        assert not is_eligible(skill, ctx)

    def test_non_invocable_allowed_when_gate_off(self):
        skill = _Skill("s1", user_invocable=False)
        ctx = SkillEligibilityContext(user_invocable_only=False)
        assert is_eligible(skill, ctx)

    def test_invocable_always_passes_gate(self):
        skill = _Skill("s1", user_invocable=True)
        ctx = SkillEligibilityContext(user_invocable_only=True)
        assert is_eligible(skill, ctx)


# ---------------------------------------------------------------------------
# is_eligible — tag filters
# ---------------------------------------------------------------------------


class TestIsEligibleTags:
    def test_required_tag_present_passes(self):
        skill = _Skill("s1", tags=["docker", "infra"])
        ctx = SkillEligibilityContext(required_tags=["docker"])
        assert is_eligible(skill, ctx)

    def test_required_tag_missing_fails(self):
        skill = _Skill("s1", tags=["docker"])
        ctx = SkillEligibilityContext(required_tags=["kubernetes"])
        assert not is_eligible(skill, ctx)

    def test_all_required_tags_must_be_present(self):
        skill = _Skill("s1", tags=["docker"])
        ctx = SkillEligibilityContext(required_tags=["docker", "infra"])
        assert not is_eligible(skill, ctx)

    def test_excluded_tag_present_fails(self):
        skill = _Skill("s1", tags=["dangerous"])
        ctx = SkillEligibilityContext(excluded_tags=["dangerous"])
        assert not is_eligible(skill, ctx)

    def test_excluded_tag_absent_passes(self):
        skill = _Skill("s1", tags=["safe"])
        ctx = SkillEligibilityContext(excluded_tags=["dangerous"])
        assert is_eligible(skill, ctx)

    def test_tag_matching_is_case_insensitive(self):
        skill = _Skill("s1", tags=["Docker"])
        ctx = SkillEligibilityContext(required_tags=["docker"])
        assert is_eligible(skill, ctx)


# ---------------------------------------------------------------------------
# filter_skills
# ---------------------------------------------------------------------------


class TestFilterSkills:
    def _skills(self) -> dict:
        return {
            "s1": _Skill("s1", safety="safe"),
            "s2": _Skill("s2", safety="destructive"),
            "s3": _Skill("s3", safety="safe", tags=["cloud"]),
        }

    def test_all_pass_permissive(self):
        ctx = SkillEligibilityContext.permissive()
        ids = list(self._skills())
        result = filter_skills(ids, self._skills(), ctx)
        assert set(result) == {"s1", "s2", "s3"}

    def test_destructive_filtered_with_elevated_ceiling(self):
        ctx = SkillEligibilityContext(safety_max="elevated")
        ids = list(self._skills())
        result = filter_skills(ids, self._skills(), ctx)
        assert "s2" not in result
        assert "s1" in result

    def test_missing_ids_silently_dropped(self):
        ctx = SkillEligibilityContext.permissive()
        result = filter_skills(["nonexistent"], self._skills(), ctx)
        assert result == []

    def test_preserves_order(self):
        ctx = SkillEligibilityContext.permissive()
        ordered = ["s3", "s1"]
        result = filter_skills(ordered, self._skills(), ctx)
        assert result == ["s3", "s1"]

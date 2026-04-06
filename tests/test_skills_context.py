"""Tests for navig.agent.skills_context and navig.agent.tools.skill_tools (FB-02)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

import navig.agent.tools.skill_tools as skill_tools_mod
from navig.agent.skills_context import (
    KEYWORD_MATCH_SCORE,
    MAX_ACTIVE_SKILLS,
    MAX_SKILL_CHARS,
    PATH_MATCH_SCORE,
    ContextSkill,
    SkillsContext,
    _compute_activation_score,
    _load_frontmatter,
    _parse_skill_file,
)
from navig.agent.tools.skill_tools import (
    MANAGE_SKILLS_SCHEMA,
    get_skill_schemas,
    handle_manage_skills,
    register_skill_tools,
)

# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _write_skill(
    path: Path,
    *,
    name: str = "Test Skill",
    content: str = "Do the thing.",
    activation_paths: list[str] | None = None,
    activation_keywords: list[str] | None = None,
    priority: int = 0,
) -> Path:
    """Write a skill Markdown file with optional YAML frontmatter."""
    lines: list[str] = ["---"]
    lines.append(f"name: {name}")
    if activation_paths:
        lines.append(f"activation_paths: {json.dumps(activation_paths)}")
    if activation_keywords:
        lines.append(f"activation_keywords: {json.dumps(activation_keywords)}")
    if priority:
        lines.append(f"priority: {priority}")
    lines.append("---")
    lines.append(content)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_plain_skill(path: Path, body: str) -> Path:
    """Write a plain Markdown skill file (no frontmatter)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════
# ContextSkill dataclass
# ═══════════════════════════════════════════════════════════════


class TestContextSkill:
    def test_defaults(self):
        s = ContextSkill(name="foo", content="bar")
        assert s.activation_paths == []
        assert s.activation_keywords == []
        assert s.priority == 0
        assert s.source == "project"
        assert s.file_path == ""

    def test_summary_short(self):
        s = ContextSkill(name="x", content="Short text.")
        assert s.summary() == "Short text."

    def test_summary_truncated(self):
        s = ContextSkill(name="x", content="A" * 200)
        result = s.summary(max_len=80)
        assert len(result) == 80
        assert result.endswith("…")

    def test_summary_multiline(self):
        s = ContextSkill(name="x", content="Line1\nLine2\nLine3")
        assert "Line1" in s.summary()
        assert "\n" not in s.summary()


# ═══════════════════════════════════════════════════════════════
# Frontmatter parsing
# ═══════════════════════════════════════════════════════════════


class TestFrontmatter:
    def test_no_frontmatter(self):
        fm, body = _load_frontmatter("# Just Markdown\nHello world")
        assert fm == {}
        assert body == "# Just Markdown\nHello world"

    def test_valid_frontmatter(self):
        text = "---\nname: Test\npriority: 5\n---\nBody here"
        fm, body = _load_frontmatter(text)
        assert fm["name"] == "Test"
        assert fm["priority"] == 5
        assert body == "Body here"

    def test_empty_frontmatter(self):
        text = "---\n---\nBody"
        fm, body = _load_frontmatter(text)
        assert fm == {}
        assert body == "Body"

    def test_malformed_yaml_returns_empty(self):
        text = "---\n: : : invalid\n---\nBody"
        fm, body = _load_frontmatter(text)
        # Either empty dict or partial parse — body must survive
        assert "Body" in body

    def test_single_separator_no_split(self):
        text = "---\nno end separator"
        fm, body = _load_frontmatter(text)
        assert fm == {}


# ═══════════════════════════════════════════════════════════════
# Skill file parsing
# ═══════════════════════════════════════════════════════════════


class TestParseSkillFile:
    def test_full_frontmatter(self, tmp_path: Path):
        p = _write_skill(
            tmp_path / "django.md",
            name="Django",
            content="Use ORM.",
            activation_paths=["*.py"],
            activation_keywords=["django"],
            priority=10,
        )
        skill = _parse_skill_file(p, source="project")
        assert skill is not None
        assert skill.name == "Django"
        assert skill.content == "Use ORM."
        assert skill.activation_paths == ["*.py"]
        assert skill.activation_keywords == ["django"]
        assert skill.priority == 10
        assert skill.source == "project"

    def test_no_frontmatter_uses_stem(self, tmp_path: Path):
        p = _write_plain_skill(tmp_path / "git-tips.md", "# Git Tips\nCommit often.")
        skill = _parse_skill_file(p, source="global")
        assert skill is not None
        assert skill.name == "git-tips"
        assert skill.source == "global"
        assert skill.activation_paths == []
        assert skill.activation_keywords == []

    def test_truncates_long_content(self, tmp_path: Path):
        long_body = "x" * (MAX_SKILL_CHARS + 500)
        p = _write_plain_skill(tmp_path / "big.md", long_body)
        skill = _parse_skill_file(p, source="project")
        assert skill is not None
        assert len(skill.content) < MAX_SKILL_CHARS + 50  # includes truncation marker
        assert "truncated" in skill.content

    def test_missing_file_returns_none(self, tmp_path: Path):
        result = _parse_skill_file(tmp_path / "nope.md", source="project")
        assert result is None

    def test_csv_activation_keywords(self, tmp_path: Path):
        p = tmp_path / "test.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\nname: CSV\nactivation_keywords: docker, compose, container\n---\nBody",
            encoding="utf-8",
        )
        skill = _parse_skill_file(p, source="project")
        assert skill is not None
        assert len(skill.activation_keywords) == 3
        assert "docker" in skill.activation_keywords


# ═══════════════════════════════════════════════════════════════
# Activation scoring
# ═══════════════════════════════════════════════════════════════


class TestActivationScoring:
    def test_no_match_zero_score(self):
        skill = ContextSkill(name="x", content="y")
        assert _compute_activation_score(skill, [], "") == 0

    def test_path_match(self):
        skill = ContextSkill(name="x", content="y", activation_paths=["*.py"])
        score = _compute_activation_score(skill, ["src/app.py"], "")
        assert score == PATH_MATCH_SCORE

    def test_keyword_match(self):
        skill = ContextSkill(
            name="x", content="y", activation_keywords=["django"]
        )
        score = _compute_activation_score(skill, [], "help with django models")
        assert score == KEYWORD_MATCH_SCORE

    def test_keyword_case_insensitive(self):
        skill = ContextSkill(
            name="x", content="y", activation_keywords=["Django"]
        )
        score = _compute_activation_score(skill, [], "using django orm")
        assert score == KEYWORD_MATCH_SCORE

    def test_priority_bonus(self):
        skill = ContextSkill(name="x", content="y", priority=7)
        score = _compute_activation_score(skill, [], "")
        assert score == 7

    def test_combined_score(self):
        skill = ContextSkill(
            name="x",
            content="y",
            activation_paths=["*.py", "*.html"],
            activation_keywords=["django", "queryset"],
            priority=3,
        )
        score = _compute_activation_score(
            skill,
            ["views.py", "template.html"],
            "fix django queryset",
        )
        # 2 path matches (10 each) + 2 keyword matches (5 each) + 3 priority
        assert score == 2 * PATH_MATCH_SCORE + 2 * KEYWORD_MATCH_SCORE + 3

    def test_basename_matching(self):
        """Pattern 'models.py' should match 'src/app/models.py'."""
        skill = ContextSkill(
            name="x", content="y", activation_paths=["models.py"]
        )
        score = _compute_activation_score(
            skill, ["src/app/models.py"], ""
        )
        assert score == PATH_MATCH_SCORE

    def test_multiple_files_one_pattern_scores_once(self):
        """A single pattern should score +10 even if it matches multiple files."""
        skill = ContextSkill(
            name="x", content="y", activation_paths=["*.py"]
        )
        score = _compute_activation_score(
            skill, ["a.py", "b.py", "c.py"], ""
        )
        # One pattern → one match score
        assert score == PATH_MATCH_SCORE


# ═══════════════════════════════════════════════════════════════
# SkillsContext loading
# ═══════════════════════════════════════════════════════════════


class TestSkillsContextLoading:
    def test_load_project_skills(self, tmp_path: Path):
        proj = tmp_path / "project"
        proj.mkdir()
        _write_skill(proj / ".navig" / "skills" / "a.md", name="Skill A")
        _write_skill(proj / ".navig" / "skills" / "b.md", name="Skill B")

        ctx = SkillsContext(workspace_dir=str(proj))
        skills = ctx.load()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"Skill A", "Skill B"}

    def test_load_extra_dirs(self, tmp_path: Path):
        extra = tmp_path / "extra_skills"
        _write_skill(extra / "ext.md", name="Extra Skill")

        ctx = SkillsContext(workspace_dir=str(tmp_path), extra_dirs=[extra])
        skills = ctx.load()
        assert any(s.name == "Extra Skill" for s in skills)
        assert any(s.source == "extra" for s in skills)

    def test_empty_directory(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        skills = ctx.load()
        assert skills == []

    def test_skill_count_property(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _write_skill(proj / ".navig" / "skills" / "one.md", name="One")
        ctx = SkillsContext(workspace_dir=str(proj))
        assert ctx.skill_count == 1

    def test_reload_picks_up_new_files(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        skills_dir = proj / ".navig" / "skills"
        _write_skill(skills_dir / "a.md", name="A")

        ctx = SkillsContext(workspace_dir=str(proj))
        assert ctx.skill_count == 1

        _write_skill(skills_dir / "b.md", name="B")
        ctx.reload()
        assert ctx.skill_count == 2

    def test_get_skill_by_name(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(proj / ".navig" / "skills" / "django.md", name="Django")
        ctx = SkillsContext(workspace_dir=str(proj))
        assert ctx.get_skill("Django") is not None
        assert ctx.get_skill("Nonexistent") is None

    def test_nested_directories(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(proj / ".navig" / "skills" / "web" / "django.md", name="Django")
        _write_skill(proj / ".navig" / "skills" / "devops" / "docker.md", name="Docker")
        ctx = SkillsContext(workspace_dir=str(proj))
        assert ctx.skill_count == 2


# ═══════════════════════════════════════════════════════════════
# SkillsContext activation
# ═══════════════════════════════════════════════════════════════


class TestSkillsContextActivation:
    def test_activate_by_keyword(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "django.md",
            name="Django",
            activation_keywords=["django", "queryset"],
        )
        _write_skill(
            proj / ".navig" / "skills" / "react.md",
            name="React",
            activation_keywords=["react", "component"],
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        active = ctx.activate(user_message="help with django models")
        assert len(active) == 1
        assert active[0].name == "Django"

    def test_activate_by_path(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "py.md",
            name="Python",
            activation_paths=["*.py"],
        )
        _write_skill(
            proj / ".navig" / "skills" / "ts.md",
            name="TypeScript",
            activation_paths=["*.ts", "*.tsx"],
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        active = ctx.activate(current_files=["src/main.py"])
        assert len(active) == 1
        assert active[0].name == "Python"

    def test_max_active_limit(self, tmp_path: Path):
        proj = tmp_path / "proj"
        skills_dir = proj / ".navig" / "skills"
        for i in range(6):
            _write_skill(
                skills_dir / f"skill{i}.md",
                name=f"Skill{i}",
                activation_keywords=["common"],
            )

        ctx = SkillsContext(workspace_dir=str(proj))
        active = ctx.activate(user_message="common topic")
        assert len(active) == MAX_ACTIVE_SKILLS

    def test_custom_max_active(self, tmp_path: Path):
        proj = tmp_path / "proj"
        skills_dir = proj / ".navig" / "skills"
        for i in range(5):
            _write_skill(
                skills_dir / f"s{i}.md",
                name=f"S{i}",
                activation_keywords=["test"],
            )

        ctx = SkillsContext(workspace_dir=str(proj), max_active=5)
        active = ctx.activate(user_message="test stuff")
        assert len(active) == 5

    def test_no_match_returns_empty(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "django.md",
            name="Django",
            activation_keywords=["django"],
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        active = ctx.activate(user_message="fix the rust compiler")
        assert active == []

    def test_priority_ordering(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "low.md",
            name="Low",
            activation_keywords=["common"],
            priority=1,
        )
        _write_skill(
            proj / ".navig" / "skills" / "high.md",
            name="High",
            activation_keywords=["common"],
            priority=100,
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        active = ctx.activate(user_message="common topic")
        assert active[0].name == "High"

    def test_project_preferred_over_global(self, tmp_path: Path):
        """When two skills score equally, project-source wins."""
        proj = tmp_path / "proj"
        extra = tmp_path / "global_skills"
        _write_skill(
            proj / ".navig" / "skills" / "p.md",
            name="ProjectSkill",
            activation_keywords=["test"],
            priority=5,
        )
        _write_skill(
            extra / "g.md",
            name="GlobalSkill",
            activation_keywords=["test"],
            priority=5,
        )

        ctx = SkillsContext(workspace_dir=str(proj), extra_dirs=[extra])
        active = ctx.activate(user_message="test question")
        # ProjectSkill should come first (project > extra/global)
        assert active[0].name == "ProjectSkill"


# ═══════════════════════════════════════════════════════════════
# Force activate / deactivate
# ═══════════════════════════════════════════════════════════════


class TestForceOverrides:
    def test_force_activate(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "hidden.md",
            name="Hidden",
            # No activation rules — would never score > 0
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        # Without force, nothing activates
        assert ctx.activate(user_message="anything") == []

        # Force-activate
        assert ctx.force_activate("Hidden") is True
        active = ctx.activate(user_message="anything")
        assert len(active) == 1
        assert active[0].name == "Hidden"

    def test_force_deactivate(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "django.md",
            name="Django",
            activation_keywords=["django"],
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        # Normally activates
        assert len(ctx.activate(user_message="django question")) == 1

        # Force-deactivate
        assert ctx.force_deactivate("Django") is True
        assert ctx.activate(user_message="django question") == []

    def test_force_activate_unknown_returns_false(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        assert ctx.force_activate("Nonexistent") is False

    def test_force_deactivate_unknown_returns_false(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        assert ctx.force_deactivate("Nonexistent") is False

    def test_reset_overrides(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "a.md",
            name="A",
            activation_keywords=["test"],
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        ctx.force_deactivate("A")
        assert ctx.activate(user_message="test") == []

        ctx.reset_overrides()
        assert len(ctx.activate(user_message="test")) == 1

    def test_activate_overrides_deactivate(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "a.md",
            name="A",
        )

        ctx = SkillsContext(workspace_dir=str(proj))
        ctx.force_deactivate("A")
        assert ctx.activate() == []

        # Force-activate should clear the deactivation
        ctx.force_activate("A")
        active = ctx.activate()
        assert len(active) == 1


# ═══════════════════════════════════════════════════════════════
# System prompt formatting
# ═══════════════════════════════════════════════════════════════


class TestFormatForPrompt:
    def test_empty_returns_empty_string(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        assert ctx.format_for_system_prompt([]) == ""

    def test_single_skill(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        skill = ContextSkill(name="Django", content="Use ORM.")
        result = ctx.format_for_system_prompt([skill])
        assert "## Active Skills" in result
        assert "### Django" in result
        assert "Use ORM." in result

    def test_source_tag_for_global(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        skill = ContextSkill(name="Global", content="Info.", source="global")
        result = ctx.format_for_system_prompt([skill])
        assert "(global)" in result

    def test_no_source_tag_for_project(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        skill = ContextSkill(name="Local", content="Info.", source="project")
        result = ctx.format_for_system_prompt([skill])
        assert "(project)" not in result

    def test_multiple_skills_ordered(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        skills = [
            ContextSkill(name="A", content="First."),
            ContextSkill(name="B", content="Second."),
        ]
        result = ctx.format_for_system_prompt(skills)
        assert result.index("### A") < result.index("### B")


# ═══════════════════════════════════════════════════════════════
# Skill tools
# ═══════════════════════════════════════════════════════════════


class TestSkillToolSchema:
    def test_schema_has_required_fields(self):
        assert MANAGE_SKILLS_SCHEMA["name"] == "manage_skills"
        assert "parameters" in MANAGE_SKILLS_SCHEMA
        props = MANAGE_SKILLS_SCHEMA["parameters"]["properties"]
        assert "action" in props
        assert "skill_name" in props

    def test_action_enum(self):
        enum = MANAGE_SKILLS_SCHEMA["parameters"]["properties"]["action"]["enum"]
        assert set(enum) == {"list", "activate", "deactivate"}

    def test_get_skill_schemas_returns_list(self):
        schemas = get_skill_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "manage_skills"


class TestSkillToolHandler:
    def test_not_initialised(self):
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = None
            result = handle_manage_skills(action="list")
            assert "not initialised" in result.lower()
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_unknown_action(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="destroy")
            assert "unknown action" in result.lower()
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_list_empty(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="list")
            assert "no skills found" in result.lower()
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_list_with_skills(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "django.md",
            name="Django",
            activation_keywords=["django"],
            priority=10,
        )
        ctx = SkillsContext(workspace_dir=str(proj))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="list")
            parsed = json.loads(result)
            assert len(parsed) == 1
            assert parsed[0]["name"] == "Django"
            assert parsed[0]["priority"] == 10
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_activate_skill(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(proj / ".navig" / "skills" / "a.md", name="A")
        ctx = SkillsContext(workspace_dir=str(proj))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="activate", skill_name="A")
            assert "force-activated" in result.lower()
            assert "A" in ctx._force_activated
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_activate_missing_name(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="activate", skill_name="")
            assert "required" in result.lower()
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_activate_unknown_skill(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="activate", skill_name="Ghost")
            assert "not found" in result.lower()
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_deactivate_skill(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(proj / ".navig" / "skills" / "a.md", name="A")
        ctx = SkillsContext(workspace_dir=str(proj))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="deactivate", skill_name="A")
            assert "force-deactivated" in result.lower()
            assert "A" in ctx._force_deactivated
        finally:
            skill_tools_mod._skills_ctx = old_ctx

    def test_deactivate_missing_name(self, tmp_path: Path):
        ctx = SkillsContext(workspace_dir=str(tmp_path))
        old_ctx = skill_tools_mod._skills_ctx
        try:
            skill_tools_mod._skills_ctx = ctx
            result = handle_manage_skills(action="deactivate", skill_name="")
            assert "required" in result.lower()
        finally:
            skill_tools_mod._skills_ctx = old_ctx


# ═══════════════════════════════════════════════════════════════
# Constants & edge cases
# ═══════════════════════════════════════════════════════════════


class TestConstants:
    def test_max_active_skills(self):
        assert MAX_ACTIVE_SKILLS == 3

    def test_max_skill_chars(self):
        assert MAX_SKILL_CHARS == 8000

    def test_path_match_score(self):
        assert PATH_MATCH_SCORE == 10

    def test_keyword_match_score(self):
        assert KEYWORD_MATCH_SCORE == 5


class TestEdgeCases:
    def test_skill_with_no_activation_rules_scores_zero(self):
        skill = ContextSkill(name="x", content="y")
        assert _compute_activation_score(skill, ["a.py"], "hello") == 0

    def test_skills_all_property(self, tmp_path: Path):
        proj = tmp_path / "proj"
        _write_skill(proj / ".navig" / "skills" / "a.md", name="A")
        _write_skill(proj / ".navig" / "skills" / "b.md", name="B")

        ctx = SkillsContext(workspace_dir=str(proj))
        all_skills = ctx.all_skills
        assert len(all_skills) == 2
        # Should return a copy
        all_skills.clear()
        assert ctx.skill_count == 2

    def test_auto_load_on_activate(self, tmp_path: Path):
        """Calling activate() without explicit load() should auto-load."""
        proj = tmp_path / "proj"
        _write_skill(
            proj / ".navig" / "skills" / "a.md",
            name="A",
            activation_keywords=["test"],
        )
        ctx = SkillsContext(workspace_dir=str(proj))
        # Don't call load() explicitly
        active = ctx.activate(user_message="test topic")
        assert len(active) == 1

    def test_unicode_content(self, tmp_path: Path):
        p = _write_plain_skill(
            tmp_path / "unicode.md", "# Übersicht\nÄpfel und Birnen 🍎"
        )
        skill = _parse_skill_file(p, source="project")
        assert skill is not None
        assert "Übersicht" in skill.content
        assert "🍎" in skill.content

    def test_empty_md_file(self, tmp_path: Path):
        p = _write_plain_skill(tmp_path / "empty.md", "")
        skill = _parse_skill_file(p, source="project")
        assert skill is not None
        assert skill.content == ""
        assert skill.name == "empty"

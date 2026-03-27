"""
Skills Prompt Renderer — JSON manifests + Jinja2 templates.

Supports three modes (configurable via context_skills.mode):
  - "md"   → existing behavior (inject skills/*.md content)
  - "json" → use only JSON manifests + Jinja2 template
  - "auto" → prefer JSON if it exists; fall back to first 50 lines of .md

Does NOT touch SOUL.md / PERSONALITY.md / PLAYBOOK.md.
Only refactors skills prompt injection.

Usage:
    from navig.skills_renderer import render_skills_prompt
    prompt = render_skills_prompt(["ahk", "git-basics", "file-ops"])
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.skills_renderer")

# ─────────────────────────────────────────────────────────────
# Pydantic Schema for Skill Manifests
# ─────────────────────────────────────────────────────────────

try:
    from pydantic import BaseModel, Field

    PYDANTIC_OK = True
except ImportError:
    PYDANTIC_OK = False

if PYDANTIC_OK:

    class SkillCommand(BaseModel):
        """A single command exposed by a skill."""

        name: str = ""
        signature: str = ""
        description: str = ""

    class SkillManifest(BaseModel):
        """JSON skill manifest (skills/<skill_id>.json)."""

        id: str = ""
        name: str = ""
        summary: str = ""
        commands: list[SkillCommand] = Field(default_factory=list)

else:
    SkillManifest = None
    SkillCommand = None


# ─────────────────────────────────────────────────────────────
# Jinja2 Template (embedded default)
# ─────────────────────────────────────────────────────────────

DEFAULT_SKILLS_TEMPLATE = """\
You have access to the following tools/skills:

{% for skill in skills %}
[{{skill.id}}] {{skill.name}} — {{skill.summary}}
Commands:
{% for cmd in skill.commands %}
- {{cmd.signature}} : {{cmd.description}}
{% endfor %}
{% endfor %}"""


# ─────────────────────────────────────────────────────────────
# Path Resolution
# ─────────────────────────────────────────────────────────────


def _get_skills_dirs() -> list[Path]:
    """Get all directories where skills may live.

    Delegates to ``navig.skills.loader.get_skill_dirs()`` so renderer and
    loader always search the same roots (builtin store, user store, packages).
    Falls back to a minimal path list when the loader is unavailable.
    """
    try:
        from navig.skills.loader import get_skill_dirs

        return get_skill_dirs()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Minimal fallback when loader is not importable
    dirs: list[Path] = []
    try:
        from navig.platform.paths import builtin_store_dir

        d = builtin_store_dir() / "skills"
        if d.exists():
            dirs.append(d)
    except Exception:
        pkg = Path(__file__).resolve().parent.parent / "store" / "skills"
        if pkg.exists():
            dirs.append(pkg)
    return dirs


def _find_skill_json(skill_id: str) -> Path | None:
    """Find a skill's JSON manifest."""
    for d in _get_skills_dirs():
        # Direct: skills/<skill_id>.json
        p = d / f"{skill_id}.json"
        if p.exists():
            return p
        # Nested: skills/<skill_id>/skill.json
        p = d / skill_id / "skill.json"
        if p.exists():
            return p
        # Nested: skills/<category>/<skill_id>.json
        for sub in d.iterdir() if d.exists() else []:
            if sub.is_dir():
                p = sub / f"{skill_id}.json"
                if p.exists():
                    return p
    return None


def _find_skill_md(skill_id: str) -> Path | None:
    """Find a skill's markdown file."""
    for d in _get_skills_dirs():
        # Direct: skills/<skill_id>.md
        p = d / f"{skill_id}.md"
        if p.exists():
            return p
        # Nested: skills/<skill_id>/README.md
        p = d / skill_id / "README.md"
        if p.exists():
            return p
        # Nested: skills/<category>/<skill_id>/README.md or <skill_id>.md
        for sub in d.iterdir() if d.exists() else []:
            if sub.is_dir():
                p = sub / skill_id / "README.md"
                if p.exists():
                    return p
                p = sub / f"{skill_id}.md"
                if p.exists():
                    return p
    return None


# ─────────────────────────────────────────────────────────────
# Loaders
# ─────────────────────────────────────────────────────────────


def _load_skill_json(skill_id: str) -> dict[str, Any] | None:
    """Load a skill JSON manifest and return as dict."""
    path = _find_skill_json(skill_id)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if PYDANTIC_OK:
            manifest = SkillManifest.model_validate(data)
            return manifest.model_dump()
        return data
    except Exception as e:
        logger.warning("Failed to load skill JSON %s: %s", path, e)
        return None


def _load_skill_md(skill_id: str, max_lines: int = 50) -> str | None:
    """Load first N lines of a skill's markdown file."""
    path = _find_skill_md(skill_id)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
        return "".join(lines)
    except Exception as e:
        logger.warning("Failed to load skill MD %s: %s", path, e)
        return None


# ─────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────


def _get_context_skills_mode() -> str:
    """Get the configured context_skills.mode from config. Default: 'auto'."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        raw = cm.global_config or {}
        ctx = raw.get("context_skills", {})
        if isinstance(ctx, dict):
            return ctx.get("mode", "auto")
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical
    return "auto"


def render_skills_prompt(
    skill_ids: list[str],
    mode: str | None = None,
    template_str: str | None = None,
) -> str:
    """
    Render a skills prompt for injection into the LLM system prompt.

    Args:
        skill_ids: List of skill IDs to include.
        mode: Override the context_skills.mode config ("md", "json", "auto").
        template_str: Override the Jinja2 template.

    Returns:
        Rendered prompt string with skill descriptions.
    """
    mode = mode or _get_context_skills_mode()
    skills_data = []

    for sid in skill_ids:
        if mode == "json":
            data = _load_skill_json(sid)
            if data:
                skills_data.append(data)
            else:
                logger.debug("No JSON manifest for skill '%s', skipping", sid)

        elif mode == "md":
            md = _load_skill_md(sid)
            if md:
                skills_data.append(
                    {
                        "id": sid,
                        "name": sid,
                        "summary": "",
                        "commands": [],
                        "_md_content": md,
                    }
                )

        else:  # "auto" — prefer JSON, fall back to .md
            data = _load_skill_json(sid)
            if data:
                skills_data.append(data)
            else:
                md = _load_skill_md(sid)
                if md:
                    skills_data.append(
                        {
                            "id": sid,
                            "name": sid,
                            "summary": "",
                            "commands": [],
                            "_md_content": md,
                        }
                    )

    if not skills_data:
        return ""

    # If any skills are md-only, render differently
    md_skills = [s for s in skills_data if "_md_content" in s]
    json_skills = [s for s in skills_data if "_md_content" not in s]

    parts = []

    # Render JSON skills via Jinja2 template
    if json_skills:
        template = template_str or _load_template() or DEFAULT_SKILLS_TEMPLATE
        try:
            from jinja2 import Template

            tmpl = Template(template)
            rendered = tmpl.render(skills=json_skills)
            parts.append(rendered.strip())
        except ImportError:
            # No Jinja2 — manual render
            parts.append(_manual_render(json_skills))

    # Append MD skill content
    for s in md_skills:
        content = s["_md_content"]
        parts.append(f"\n[{s['id']}]\n{content}")

    return "\n\n".join(parts)


def _load_template() -> str | None:
    """Try to load the skills template from store/templates/skills_prompt.txt.

    Walks up from each skill root (e.g. store/skills/ → store/) and checks
    for a sibling templates/skills_prompt.txt. Falls back to
    navig/scaffold-templates/skills_prompt.txt if none found.
    """
    for d in _get_skills_dirs():
        parent = d.parent
        p = parent / "templates" / "skills_prompt.txt"
        if p.exists():
            return p.read_text(encoding="utf-8")
    # Also check navig/scaffold-templates/
    built_in = Path(__file__).parent / "scaffold-templates"
    p = built_in / "skills_prompt.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


def _manual_render(skills: list[dict[str, Any]]) -> str:
    """Render skills prompt without Jinja2."""
    lines = ["You have access to the following tools/skills:", ""]
    for s in skills:
        lines.append(
            f"[{s.get('id', '?')}] {s.get('name', '')} — {s.get('summary', '')}"
        )
        lines.append("Commands:")
        for cmd in s.get("commands", []):
            lines.append(f"- {cmd.get('signature', '')} : {cmd.get('description', '')}")
        lines.append("")
    return "\n".join(lines)

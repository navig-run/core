"""
Agent tools for runtime skill management (FB-02).

Provides three agent-callable tools that let the LLM inspect and override
the context-aware skill system at runtime:

    manage_skills(action="list")
        → list all discovered skills with activation status

    manage_skills(action="activate", skill_name="Django Best Practices")
        → force-activate a named skill

    manage_skills(action="deactivate", skill_name="Django Best Practices")
        → force-deactivate a named skill

Registration::

    from navig.agent.tools.skill_tools import register_skill_tools
    register_skill_tools(skills_context)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.agent.skills_context import SkillsContext

logger = logging.getLogger(__name__)

# Module-level reference set by register_skill_tools()
_skills_ctx: SkillsContext | None = None


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

MANAGE_SKILLS_SCHEMA: dict[str, Any] = {
    "name": "manage_skills",
    "description": (
        "List, activate, or deactivate project skills. "
        "Skills provide domain-specific instructions that are auto-injected "
        "into the system prompt based on file patterns and keywords."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "activate", "deactivate"],
                "description": (
                    "Action to perform: 'list' shows all skills, "
                    "'activate' forces a skill on, "
                    "'deactivate' forces a skill off."
                ),
            },
            "skill_name": {
                "type": "string",
                "description": (
                    "Name of the skill to activate or deactivate. "
                    "Required for 'activate' and 'deactivate' actions."
                ),
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


def handle_manage_skills(
    action: str,
    skill_name: str = "",
    **_kwargs: Any,
) -> str:
    """Execute a skill management action.

    Returns a human-readable string (or JSON for ``list``).
    """
    if _skills_ctx is None:
        return "Skills system not initialised."

    if action == "list":
        return _list_skills()
    elif action == "activate":
        return _activate_skill(skill_name)
    elif action == "deactivate":
        return _deactivate_skill(skill_name)
    else:
        return f"Unknown action: {action!r}. Use 'list', 'activate', or 'deactivate'."


def _list_skills() -> str:
    """Return a formatted listing of all skills."""
    assert _skills_ctx is not None  # noqa: S101

    skills = _skills_ctx.all_skills
    if not skills:
        return "No skills found. Add .md files to .navig/skills/ or ~/.navig/skills/."

    entries: list[dict[str, Any]] = []
    for s in skills:
        entries.append({
            "name": s.name,
            "source": s.source,
            "priority": s.priority,
            "activation_paths": s.activation_paths,
            "activation_keywords": s.activation_keywords,
            "summary": s.summary(60),
            "force_activated": s.name in _skills_ctx._force_activated,
            "force_deactivated": s.name in _skills_ctx._force_deactivated,
        })

    return json.dumps(entries, indent=2, ensure_ascii=False)


def _activate_skill(skill_name: str) -> str:
    """Force-activate a skill by name."""
    assert _skills_ctx is not None  # noqa: S101

    if not skill_name:
        return "Error: skill_name is required for 'activate' action."

    if _skills_ctx.force_activate(skill_name):
        return f"Skill '{skill_name}' force-activated. It will be included in the next turn."
    return f"Skill '{skill_name}' not found. Use action='list' to see available skills."


def _deactivate_skill(skill_name: str) -> str:
    """Force-deactivate a skill by name."""
    assert _skills_ctx is not None  # noqa: S101

    if not skill_name:
        return "Error: skill_name is required for 'deactivate' action."

    if _skills_ctx.force_deactivate(skill_name):
        return f"Skill '{skill_name}' force-deactivated. It will be excluded from the next turn."
    return f"Skill '{skill_name}' not found. Use action='list' to see available skills."


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_skill_tools(skills_context: SkillsContext) -> None:
    """Register the ``manage_skills`` tool into the agent registry.

    Parameters:
        skills_context: The :class:`SkillsContext` instance to operate on.
    """
    global _skills_ctx
    _skills_ctx = skills_context

    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY

        _AGENT_REGISTRY.register_function(
            name="manage_skills",
            fn=handle_manage_skills,
            schema=MANAGE_SKILLS_SCHEMA,
            toolset="skills",
        )
        logger.debug("Agent skill tools registered: manage_skills")
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to register skill tools: %s", exc)


def get_skill_schemas() -> list[dict[str, Any]]:
    """Return tool schemas for the skills system."""
    return [{"type": "function", "function": MANAGE_SKILLS_SCHEMA}]

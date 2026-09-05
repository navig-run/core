"""
Agent tool for runtime skill management (FB-02).

One agent-callable tool, ``manage_skills``, letting the model inspect and override the
context-aware skill system at runtime:

    manage_skills(action="list")
        → list all discovered skills with activation status

    manage_skills(action="activate", skill_name="Django Best Practices")
        → force-activate a named skill

    manage_skills(action="deactivate", skill_name="Django Best Practices")
        → force-deactivate a named skill

It is the escape hatch for automatic activation: `_build_skills_section` matches skills
against the user's message by keyword and path, bounded by ``max_active``. When the right
skill does not surface on its own, this is the only way the agent can pull it in.

Registration is via ``register_all_tools()`` like every other group. Two things this
module got wrong for its whole life, worth not repeating:

* it called ``_AGENT_REGISTRY.register_function(...)``, which has never existed — inside a
  bare ``except`` that logged at debug, so the tool silently never registered;
* it held the ``SkillsContext`` in a module global captured at registration. The daemon
  serves many spaces from one process, and — worse — a force-activation has to land on the
  **same instance the prompt is built from** or the next turn simply ignores it. The
  context is now resolved per call via
  :func:`navig.agent.skills_context.get_skills_context`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

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


def _active_context() -> SkillsContext | None:
    """The SkillsContext for the space this turn belongs to.

    Resolved per call rather than captured at registration. The daemon never chdirs and
    the operator can switch the active space while it runs, so a context captured once
    would let the agent list and activate the PREVIOUS space's skills.

    ``get_skills_context`` returns the *same* instance the prompt builder renders from,
    which is what makes ``activate`` actually change the next turn instead of mutating an
    object nobody reads.
    """
    if _skills_ctx is not None:  # explicit override (tests, embedders)
        return _skills_ctx
    try:
        from navig.agent.skills_context import get_skills_context
        from navig.spaces.active import get_active_working_dir

        return get_skills_context(str(get_active_working_dir()))
    except Exception as exc:  # noqa: BLE001 — a tool must never raise
        logger.debug("skills context unavailable: %s", exc)
        return None


def handle_manage_skills(
    action: str,
    skill_name: str = "",
    **_kwargs: Any,
) -> str:
    """Execute a skill management action.

    Returns a human-readable string (or JSON for ``list``).
    """
    ctx = _active_context()
    if ctx is None:
        # The model reads this and repeats it to the user, so name the actual failure
        # rather than a generic state — "not initialised" alone sends someone looking for
        # a setup step that does not exist.
        return (
            "Skills system not initialised: could not resolve the active space. "
            "Check `navig space list` and that a space is active."
        )

    if action == "list":
        return _list_skills(ctx)
    elif action == "activate":
        return _activate_skill(ctx, skill_name)
    elif action == "deactivate":
        return _deactivate_skill(ctx, skill_name)
    else:
        return f"Unknown action: {action!r}. Use 'list', 'activate', or 'deactivate'."


def _list_skills(ctx: SkillsContext) -> str:
    """Return a formatted listing of all skills."""
    skills = ctx.all_skills
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
            "force_activated": s.name in ctx._force_activated,
            "force_deactivated": s.name in ctx._force_deactivated,
        })

    return json.dumps(entries, indent=2, ensure_ascii=False)


def _activate_skill(ctx: SkillsContext, skill_name: str) -> str:
    """Force-activate a skill by name."""
    if not skill_name:
        return "Error: skill_name is required for 'activate' action."

    if ctx.force_activate(skill_name):
        return f"Skill '{skill_name}' force-activated. It will be included in the next turn."
    return f"Skill '{skill_name}' not found. Use action='list' to see available skills."


def _deactivate_skill(ctx: SkillsContext, skill_name: str) -> str:
    """Force-deactivate a skill by name."""
    if not skill_name:
        return "Error: skill_name is required for 'deactivate' action."

    if ctx.force_deactivate(skill_name):
        return f"Skill '{skill_name}' force-deactivated. It will be excluded from the next turn."
    return f"Skill '{skill_name}' not found. Use action='list' to see available skills."


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class ManageSkillsTool(BaseTool):
    """List, force-activate, or force-deactivate skills for the current space.

    The complement to automatic activation: `_build_skills_section` matches skills against
    the user's message by keyword and path, bounded by ``max_active``. When that misses —
    the user phrased it differently, or wants a skill the scorer ranked out — this is the
    only way the agent can pull one in.
    """

    name = "manage_skills"
    description = (
        "List, activate, or deactivate project skills. Skills provide domain-specific "
        "instructions that are auto-injected into the system prompt based on file "
        "patterns and keywords; use this when the right skill did not activate on its own."
    )
    owner_only = False
    parameters = [
        {
            "name": "action",
            "type": "string",
            "description": (
                "'list' shows all skills, 'activate' forces a skill on, "
                "'deactivate' forces a skill off."
            ),
            "required": True,
            "enum": ["list", "activate", "deactivate"],
        },
        {
            "name": "skill_name",
            "type": "string",
            "description": (
                "Name of the skill to activate or deactivate. "
                "Required for 'activate' and 'deactivate'."
            ),
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        action = str(args.get("action") or "").strip()
        skill_name = str(args.get("skill_name") or "").strip()
        await self._emit(on_status, "manage_skills", action or "(no action)", 50)
        try:
            output = handle_manage_skills(action=action, skill_name=skill_name)
        except Exception as exc:  # noqa: BLE001 — run() must never raise
            return ToolResult(name=self.name, success=False, error=str(exc))
        return ToolResult(name=self.name, success=True, output=output)


def register_skill_tools(skills_context: SkillsContext | None = None) -> None:
    """Register the ``manage_skills`` tool into the agent registry.

    Parameters:
        skills_context: Optional explicit context to operate on. Normally omitted — the
            tool resolves the active space's shared context per call, which is what lets
            one daemon serve many spaces. Passing one here pins every call to it.
    """
    global _skills_ctx
    if skills_context is not None:
        _skills_ctx = skills_context

    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    # Deliberately NOT wrapped in try/except. This call used to sit inside one, and the
    # method it called (`register_function`) has never existed — so the AttributeError was
    # logged at debug and the tool silently never registered. `register_all_tools` already
    # isolates each group; a second swallow here only hides the failure from the first.
    _AGENT_REGISTRY.register(ManageSkillsTool(), toolset="skills")
    logger.debug("Agent skill tools registered: manage_skills")


def get_skill_schemas() -> list[dict[str, Any]]:
    """Return tool schemas for the skills system."""
    return [{"type": "function", "function": MANAGE_SKILLS_SCHEMA}]

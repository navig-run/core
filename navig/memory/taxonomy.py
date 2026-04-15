"""
Memory Taxonomy — structured 4-type memory guidance system.

Ported from .lab/claude/memdir/memoryTypes.ts (MIT, Anthropic).  Adapted to
Python with Pydantic-fallback identical to the pattern in skills_renderer.py.

The taxonomy enriches the NAVIG memory system prompt with per-type guidance
so the LLM understands *when* to save a fact, *what scope* it has, and *how*
to structure the content.

Four memory types:
  USER      — facts about the human user (preferences, identity, patterns)
  FEEDBACK  — the user's quality signals about previous AI responses
  PROJECT   — technical context for the current project / codebase
  REFERENCE — external knowledge the AI should recall (docs, libs, standards)

Integration::

    from navig.memory.taxonomy import build_memory_guidance, MemoryType

    guidance = build_memory_guidance()
    # Prepend ``guidance`` to the memory section of your system prompt.

Config gate::

    memory:
      taxonomy_enabled: true   # set false to disable injection
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger("navig.memory.taxonomy")


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class MemoryType(str, Enum):
    """The four canonical memory scopes."""

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


# ---------------------------------------------------------------------------
# Taxonomy registry — every string here is intentional; these are system-
# prompt guidance texts, not configurable user values.  They encode how the
# AI should classify and structure memories.
# ---------------------------------------------------------------------------


class _MemoryTypeConfig:
    """Metadata for a single memory type."""

    __slots__ = (
        "scope",
        "label",
        "description",
        "when_to_save",
        "how_to_use",
        "body_structure",
        "examples",
    )

    def __init__(
        self,
        *,
        scope: str,
        label: str,
        description: str,
        when_to_save: str,
        how_to_use: str,
        body_structure: str,
        examples: list[str],
    ) -> None:
        self.scope = scope
        self.label = label
        self.description = description
        self.when_to_save = when_to_save
        self.how_to_use = how_to_use
        self.body_structure = body_structure
        self.examples = examples


MEMORY_TAXONOMY: dict[MemoryType, _MemoryTypeConfig] = {
    MemoryType.USER: _MemoryTypeConfig(
        scope="private",
        label="User Memory",
        description=(
            "Persistent facts about the human user: their name, role, "
            "communication style, recurring preferences, and identity signals."
        ),
        when_to_save=(
            "Save when the user explicitly states a preference, provides personal "
            "context, corrects the assistant's assumptions about them, or reveals "
            "a consistent behavioural pattern across multiple interactions."
        ),
        how_to_use=(
            "Personalise tone and suggestions.  Reference stored preferences "
            "before asking clarifying questions the user has already answered.  "
            "Respect stated communication style (terse vs verbose, formal vs casual)."
        ),
        body_structure=(
            "- name: <name if known>\n"
            "- role: <job title / area of work>\n"
            "- timezone: <if mentioned>\n"
            "- communication_style: <terse | verbose | formal | casual | ...>\n"
            "- preferences: <bulleted list of stated preferences>\n"
            "- recurring_patterns: <behaviours observed across sessions>"
        ),
        examples=[
            "User prefers concise answers with code examples over long prose.",
            "User works in fintech; regulatory context is frequently relevant.",
            "User is based in Berlin (UTC+1); schedule references should reflect this.",
        ],
    ),
    MemoryType.FEEDBACK: _MemoryTypeConfig(
        scope="private",
        label="Feedback Memory",
        description=(
            "The user's quality signals about previous AI responses: what they "
            "liked, disliked, found helpful, or explicitly asked to change."
        ),
        when_to_save=(
            "Save when the user expresses explicit satisfaction or dissatisfaction "
            "('that was perfect', 'stop doing X', 'more like that last response'), "
            "or when they heavily edit or reject a response."
        ),
        how_to_use=(
            "Adjust future responses to reinforce positive patterns and avoid "
            "negatively-marked ones.  Do not repeat mistakes the user has "
            "corrected.  Surface relevant past feedback when generating similar "
            "content."
        ),
        body_structure=(
            "- positive: <what the user praised>\n"
            "- negative: <what the user rejected or asked to stop>\n"
            "- style_signals: <implicit quality signals from edits>\n"
            "- date: <ISO-8601 date of the feedback>"
        ),
        examples=[
            "User dislikes bullet-point-heavy answers; prefers flowing prose.",
            "User found step-by-step SQL explanations very helpful on 2024-11-20.",
            "User asked to always include type annotations in Python snippets.",
        ],
    ),
    MemoryType.PROJECT: _MemoryTypeConfig(
        scope="team",
        label="Project Memory",
        description=(
            "Technical context specific to the current codebase or project: "
            "architecture decisions, key constraints, naming conventions, "
            "dependency choices, and team norms."
        ),
        when_to_save=(
            "Save when the user describes a design decision that will recur "
            "('we use X for Y because Z'), explains a non-obvious project "
            "constraint, or establishes a convention the assistant should follow."
        ),
        how_to_use=(
            "Apply project conventions without being asked.  Reference stored "
            "architecture decisions to justify suggestions.  Avoid recommending "
            "approaches already rejected for this project."
        ),
        body_structure=(
            "- stack: <languages, frameworks, key libraries>\n"
            "- architecture: <brief description + major components>\n"
            "- conventions: <naming, file structure, code style>\n"
            "- constraints: <performance, security, licensing, ...>\n"
            "- rejected_approaches: <what was tried and why it was dropped>\n"
            "- decision_log: <ADR-style entries>"
        ),
        examples=[
            "Project uses Typer for CLI; do not suggest argparse or click.",
            "All async code must be compatible with Python 3.10 (no 3.12+ syntax).",
            "Database layer is read-only in the API tier; writes go through a job queue.",
        ],
    ),
    MemoryType.REFERENCE: _MemoryTypeConfig(
        scope="team",
        label="Reference Memory",
        description=(
            "External knowledge the user wants the assistant to recall across "
            "sessions: library documentation excerpts, standards, reference "
            "material, and domain knowledge."
        ),
        when_to_save=(
            "Save when the user pastes documentation and says 'remember this', "
            "explicitly requests that a resource be kept for future use, or "
            "repeatedly provides the same background material."
        ),
        how_to_use=(
            "Treat stored references as authoritative context.  Cite them when "
            "relevant.  Prefer stored reference content over internet searches "
            "when the domain matches."
        ),
        body_structure=(
            "- source: <URL or document title>\n"
            "- content: <verbatim excerpt or structured summary>\n"
            "- relevance: <when this reference applies>\n"
            "- saved_at: <ISO-8601 date>"
        ),
        examples=[
            "Internal REST API contract for the payments service (pasted 2025-01-10).",
            "Company security policy: all secrets must be stored in Vault, never in env.",
            "Relevant excerpt from PEP 695 on type parameter syntax.",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_memory_guidance(
    types: list[MemoryType] | None = None,
) -> str:
    """
    Build an XML-structured memory guidance block for injection into the
    LLM system prompt.

    Args:
        types: Subset of ``MemoryType`` values to include.  Default: all four.

    Returns:
        A multi-line string beginning with ``<memory_taxonomy>`` that describes
        each requested memory type's scope, when to save, and how to use.
    """
    if types is None:
        types = list(MEMORY_TAXONOMY.keys())

    parts: list[str] = ["<memory_taxonomy>"]

    for mt in types:
        cfg = MEMORY_TAXONOMY.get(mt)
        if cfg is None:
            continue

        example_block = "\n".join(f"    - {ex}" for ex in cfg.examples)
        parts.append(
            f"""  <memory_type id="{mt.value}" scope="{cfg.scope}">
    <label>{cfg.label}</label>
    <description>{cfg.description}</description>
    <when_to_save>{cfg.when_to_save}</when_to_save>
    <how_to_use>{cfg.how_to_use}</how_to_use>
    <body_structure>
{cfg.body_structure}
    </body_structure>
    <examples>
{example_block}
    </examples>
  </memory_type>"""
        )

    parts.append("</memory_taxonomy>")
    return "\n".join(parts)


def is_taxonomy_enabled() -> bool:
    """
    Return ``True`` if the taxonomy feature is enabled in config.

    Default: ``True`` (opt-out rather than opt-in).
    """
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        return bool(cm.get("memory.taxonomy_enabled", True))
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory.taxonomy: config check failed: %s", exc)
        return True

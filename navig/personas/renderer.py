"""Persona renderer — format persona listings and info for Telegram / TUI output."""
from __future__ import annotations

from navig.personas.contracts import PersonaConfig

_TONE_ICONS = {
    "direct": "⚡",
    "warm": "☀️",
    "playful": "🎭",
    "formal": "📋",
    "philosophical": "🔮",
}


def render_persona_list(personas: list[PersonaConfig], active: str) -> str:
    """Return Telegram Markdown-safe persona catalogue."""
    if not personas:
        return "No personas available."

    lines = ["🎭 *Available Personas:*\n"]
    for p in sorted(personas, key=lambda x: (x.name != "default", x.name)):
        icon = _TONE_ICONS.get(p.tone, "🤖")
        active_marker = " ← *active*" if p.name == active else ""
        voice_tag = f" `{p.voice_id}`" if p.voice_id else ""
        lines.append(
            f"{icon} *{p.display_name}* (`{p.name}`){active_marker}\n"
            f"   tone: {p.tone}{voice_tag}"
        )
    lines.append("\nUse `/persona <name>` to switch.")
    return "\n".join(lines)


def render_persona_info(config: PersonaConfig, soul_content: str) -> str:
    """Return a compact persona info card."""
    icon = _TONE_ICONS.get(config.tone, "🤖")
    lines = [
        f"{icon} *Persona: {config.display_name}* (`{config.name}`)",
        f"Tone: {config.tone}",
    ]
    if config.model_hint:
        lines.append(f"Model: `{config.model_hint}`")
    if config.voice_id:
        lines.append(f"Voice: `{config.voice_id}`")
    if config.soul_extends:
        lines.append(f"Extends: `{config.soul_extends}`")

    if soul_content:
        # Trim to ~300 chars for Telegram display
        excerpt = soul_content[:300]
        if len(soul_content) > 300:
            excerpt += "…"
        lines.append(f"\n_{excerpt}_")

    return "\n".join(lines)


def render_switch_confirmation(config: PersonaConfig) -> str:
    """Return a persona-voiced switch confirmation.

    The confirmation is written from the perspective of the *new* persona so the
    switch feels immediate and sensory.
    """
    confirmations = {
        "default": f"✅ Back to default mode. How can I help?",
        "assistant": f"✅ Assistant persona active. Ready to help — what shall we tackle?",
        "tyler": f"✅ Tyler is in. What do you need?",
        "storyteller": (
            f"✅ The stage is set. The storyteller awakens — speak your tale, "
            f"and let's see where it leads."
        ),
        "philosopher": (
            f"✅ A welcome return to the examined life. "
            f"What question sits with you today?"
        ),
        "teacher": (
            f"✅ Lesson mode engaged. "
            f"What would you like to understand more deeply?"
        ),
    }
    # Custom / user-installed personas get a generic styled line
    base = confirmations.get(
        config.name,
        f"✅ Switched to {config.display_name}.",
    )
    return base

"""navig.agent.conv.style — the user's answer-style dials, as prompt rules.

Five dials, each ``less | default | more`` (the same three steps ChatGPT's
"Characteristics" use), stored under ``style.*`` in the global config:

    style.warmth · style.enthusiasm · style.emoji · style.headers_lists · style.visual

They EXTEND the persona's tone (never replace it): every non-default dial adds one line
to the system prompt's "How to Talk" block. All-default adds nothing, so a user who never
touches them gets byte-identical prompts.

The resolved style is a sorted tuple of ``(dial, level)`` pairs — hashable, so it can be
part of the prompt memo key, and stable, so the system prompt stays cache-friendly.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DIALS: tuple[str, ...] = ("warmth", "enthusiasm", "emoji", "headers_lists", "visual")
LEVELS: tuple[str, ...] = ("less", "default", "more")

Style = tuple[tuple[str, str], ...]

_RULES: dict[tuple[str, str], str] = {
    ("warmth", "more"): "Be noticeably warm and personable; acknowledge how the user feels.",
    ("warmth", "less"): "Keep a neutral, matter-of-fact register; skip pleasantries.",
    ("enthusiasm", "more"): "Bring visible energy; celebrate wins and good ideas.",
    ("enthusiasm", "less"): "Stay calm and understated: no hype, no exclamation marks.",
    ("emoji", "more"): "Use emoji to add tone — a few per reply is welcome.",
    ("emoji", "less"): "Never use emoji.",
    ("headers_lists", "more"): (
        "Structure answers with short headers and bullet lists whenever there is more "
        "than one point."
    ),
    ("headers_lists", "less"): (
        "Write in plain prose paragraphs; avoid headers and bullet lists unless asked."
    ),
    ("visual", "more"): (
        "Prefer visual answers: tables, diagrams (Mermaid or ASCII) and code blocks "
        "whenever they make the answer clearer."
    ),
    ("visual", "less"): "Avoid tables and diagrams; answer in text.",
}


def normalize_level(value: Any) -> str:
    """Coerce a raw config value to a level; anything unknown reads as ``default``."""
    text = str(value or "").strip().lower()
    return text if text in LEVELS else "default"


def style_from_mapping(raw: Any) -> Style:
    """The non-default dials in *raw* (a ``style`` config mapping), in DIALS order."""
    if not isinstance(raw, dict):
        return ()
    pairs = []
    for dial in DIALS:
        level = normalize_level(raw.get(dial))
        if level != "default":
            pairs.append((dial, level))
    return tuple(pairs)


def read_style() -> Style:
    """The user's style from the global config. Unreadable config → no style rules."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        read = getattr(cm, "get_live", cm.get)  # a terminal `config set` applies at once
        return style_from_mapping(read("style", {}) or {})
    except Exception as exc:  # noqa: BLE001 — a bad setting must never break a turn
        logger.debug("style settings unreadable (%s); using defaults", exc)
        return ()


def style_rules(style: Style) -> list[str]:
    """One prompt rule per non-default dial."""
    return [rule for pair in style if (rule := _RULES.get(pair))]

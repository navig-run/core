"""
navig.ui — Centralized design system for NAVIG CLI output.

All command output must go through this package. Zero print() / console.print()
outside navig/ui/ after migration.

Public API:
    from navig.ui import renderer
    renderer.render_status_header(chips)
    renderer.render_primary_state(label, icon, detail, style)
    # ... see renderer.py for full surface

    from navig.ui import Answer, validate_answer
    # ... see answer.py for the unified answer contract and renderers
"""

from navig.ui import renderer  # noqa: F401
from navig.ui.answer import (
    ANSWER_FIELDS,
    ANSWER_SCHEMA_VERSION,
    ANSWER_WIRE_FIELDS,
    DIRECTIVE_TYPES,
    Answer,
    render_cli,
    to_alexa,
    to_alice,
    to_deck,
    to_json,
    to_openai,
    to_payload,
    to_telegram,
    validate_answer,
)

__all__ = [
    "ANSWER_FIELDS",
    "ANSWER_SCHEMA_VERSION",
    "ANSWER_WIRE_FIELDS",
    "DIRECTIVE_TYPES",
    "Answer",
    "render_cli",
    "renderer",
    "to_alexa",
    "to_alice",
    "to_deck",
    "to_json",
    "to_openai",
    "to_payload",
    "to_telegram",
    "validate_answer",
]

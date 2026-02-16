"""
Data Tool Pack - json_parse, text_summarize (stub).

Lightweight data processing tools.
"""
from __future__ import annotations
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.tools.router import ToolRegistry


def _json_parse(text: str, **kwargs):
    """Parse a JSON string and return the structured data."""
    try:
        return {"parsed": json.loads(text)}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}


def register_tools(registry: "ToolRegistry") -> None:
    from navig.tools.router import ToolMeta, ToolDomain, SafetyLevel

    registry.register(
        ToolMeta(
            name="json_parse",
            domain=ToolDomain.DATA,
            description="Parse a JSON string into structured data.",
            safety=SafetyLevel.SAFE,
            parameters_schema={
                "text": {"type": "string", "required": True, "description": "JSON string to parse"},
            },
            tags=["json", "parse", "data"],
        ),
        handler=_json_parse,
    )

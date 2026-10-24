"""Catalogue signals shared by the quality census and its regression budget.

``tools/mcp_inventory.py`` publishes the numbers; ``tests/quality/test_mcp_definition_budget.py``
enforces them. Both import this module so a published number and an enforced number cannot
drift apart — the failure mode that makes an audit stop meaning anything.

The signals are heuristics and are named as such: a description that mentions a sibling
tool is *probably* giving routing guidance, and one that says "returns" is *probably*
stating its result. They are cheap enough to run over the whole catalogue on every CI run,
which is the point — a deeper judgement stays in review.
"""

from __future__ import annotations

import re
from typing import Any

#: A description that names another tool — the cheapest evidence that an agent is told
#: which sibling continues the workflow.
SIBLING_TOOL = re.compile(
    r"\b(connector_[a-z0-9_]+|navig_[a-z0-9_]+|cdp_[a-z0-9_]+|desktop_[a-z0-9_]+|"
    r"recall_[a-z0-9_]+|memory_[a-z0-9_]+|firecrawl_[a-z0-9_]+|perplexity_[a-z0-9_]+|"
    r"music_[a-z0-9_]+)\b"
)

#: A description that says what comes back.
STATES_RESULT = re.compile(
    r"\b(returns?|returned|yield(?:s)?|result|responds|output)\b", re.IGNORECASE
)


def signals(tool: dict[str, Any]) -> dict[str, Any]:
    """The audit signals for one tool definition."""
    description = tool.get("description") or ""
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    annotations = tool.get("annotations") or {}
    required = schema.get("required") or []
    return {
        "title": tool.get("title") or "",
        "description_chars": len(description),
        "read_only": bool(annotations.get("readOnlyHint")),
        "destructive": bool(annotations.get("destructiveHint")),
        "open_world": annotations.get("openWorldHint"),
        "idempotent": annotations.get("idempotentHint"),
        "required": list(required),
        "optional": sorted(set(properties) - set(required)),
        "all_params_documented": all(
            isinstance(spec, dict) and bool(spec.get("description"))
            for spec in properties.values()
        ),
        "strict_schema": schema.get("additionalProperties") is False,
        "mentions_sibling_tool": bool(SIBLING_TOOL.search(description)),
        "states_result": bool(STATES_RESULT.search(description)),
    }


def counts(rows: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Aggregate the signals for every tool, keyed by name."""
    values = list(rows.values())
    return {
        "tools": len(values),
        "no_sibling_hint": sum(1 for row in values if not row["mentions_sibling_tool"]),
        "no_result_statement": sum(1 for row in values if not row["states_result"]),
        "undocumented_params": sum(1 for row in values if not row["all_params_documented"]),
        "non_strict_schema": sum(1 for row in values if not row["strict_schema"]),
        "read_only": sum(1 for row in values if row["read_only"]),
        "destructive": sum(1 for row in values if row["destructive"]),
    }

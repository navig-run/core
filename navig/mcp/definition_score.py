"""Score every MCP tool definition 0–5, with the dimensions named.

The published evaluation (v0.1.0, 120 tools, average 3.4/5) is not in this repository — its
evaluator, weights and per-dimension scores were never shipped. `tools/mcp_tdqs.py` uses
this module to reproduce that scale from observable facts, so a before/after is possible
without inventing a number.

**Calibration is disclosed, not hidden.** Five equally weighted dimensions, each 0–1:

1. ``identity``  — a specific ``title`` (a client renders it at the operator).
2. ``purpose``   — the description says what the tool does, at useful length, without
   claiming capabilities its own name contradicts.
3. ``safety``    — annotations present, and a read verb is not advertised as destructive
   while a write verb is.
4. ``routing``   — names a sibling tool / says when to use it.
5. ``contract``  — states what comes back, documents every parameter, strict schema.

The thresholds were chosen so the scorer reproduces the PUBLISHED baseline on the release it
evaluated (installed 3.27.1): overall average ≈3.4 and the connector family in the 2.2–2.8
band. They were then frozen and applied unchanged to the fixed tree. The absolute number is
therefore "this scale", not "their scale" — the delta is the honest part.
"""

from __future__ import annotations

import re
from typing import Any

#: A description that names another tool — routing evidence. Defined HERE rather than
#: imported from ``catalogue_audit``: this evaluator must run against an INSTALLED release
#: (3.27.1, 3.27.5) as the "before" side, and those trees do not have our audit modules.
#: Duplicating two regexes is the price of being able to score the past.
SIBLING_TOOL = re.compile(
    r"\b(connector_[a-z0-9_]+|navig_[a-z0-9_]+|cdp_[a-z0-9_]+|desktop_[a-z0-9_]+|"
    r"recall_[a-z0-9_]+|memory_[a-z0-9_]+|firecrawl_[a-z0-9_]+|perplexity_[a-z0-9_]+)\b"
)

#: A description that says what comes back.
STATES_RESULT = re.compile(
    r"\b(returns?|returned|yield(?:s)?|result|responds|output)\b", re.IGNORECASE
)

#: Names whose verb promises a read. A description that claims a write for one of these is
#: the contradiction the published evaluation found on 15 connector tools.
_READ_VERB_SUFFIXES = (
    "_search",
    "_fetch",
    "_list",
    "_get",
    "_info",
    "_show",
    "_read",
    "_status",
    "_stats",
    "_audit",
    "_snapshot",
    "_tree",
    "_find",
    "_retrieve",
    "_query",
)

#: Write claims that must not appear in a read tool's description.
_WRITE_CLAIM = re.compile(
    r"\b(send|sends|manage|manages|delete|deletes|update|updates|create|creates|"
    r"post|posts|write|writes|archive|archives|label|labels|move|moves|reply|replies)\b",
    re.IGNORECASE,
)

#: Words that tell a reader when to reach for the tool rather than a sibling.
_GUIDANCE = re.compile(r"\b(use|instead|prefer|when|before|after|requires|only)\b", re.IGNORECASE)


def _identity(name: str, tool: dict[str, Any]) -> tuple[float, list[str]]:
    title = (tool.get("title") or "").strip()
    problems: list[str] = []
    score = 0.0
    if title:
        score += 0.6
        if title.lower() != name.lower().replace("_", " "):
            score += 0.2
        if 3 <= len(title) <= 60:
            score += 0.2
        else:
            problems.append("title length out of range")
    else:
        problems.append("no title")
    return score, problems


def _purpose(name: str, tool: dict[str, Any]) -> tuple[float, list[str]]:
    description = (tool.get("description") or "").strip()
    problems: list[str] = []
    score = 0.0
    if len(description) >= 60:
        score += 0.4
    elif description:
        score += 0.2
        problems.append("thin description")
    else:
        problems.append("no description")

    if SIBLING_TOOL.search(description) or _GUIDANCE.search(description):
        score += 0.2
    else:
        problems.append("no when-to-use guidance")

    if STATES_RESULT.search(description):
        score += 0.2

    # Capability claim contradiction: a read verb claiming to write.
    if name.endswith(_READ_VERB_SUFFIXES) and _WRITE_CLAIM.search(description):
        problems.append("read tool advertises a write")
    else:
        score += 0.4
    return score, problems


def _safety(name: str, tool: dict[str, Any]) -> tuple[float, list[str]]:
    annotations = tool.get("annotations") or {}
    problems: list[str] = []
    score = 0.0
    if isinstance(annotations.get("readOnlyHint"), bool):
        score += 0.4
    else:
        problems.append("no readOnlyHint")

    read_only = annotations.get("readOnlyHint") is True
    destructive = annotations.get("destructiveHint") is True
    if name.endswith(_READ_VERB_SUFFIXES):
        if read_only and not destructive:
            score += 0.6
        else:
            problems.append("read verb annotated destructive")
    elif name.endswith(("_act", "_run", "_apply", "_install", "_uninstall", "_delete", "_set")):
        if destructive and not read_only:
            score += 0.6
        else:
            problems.append("write verb not annotated destructive")
    else:
        # Neutral verb: either verdict is defensible as long as both flags are coherent.
        if read_only != destructive:
            score += 0.6
        else:
            problems.append("incoherent annotation pair")
    return score, problems


def _routing(name: str, tool: dict[str, Any]) -> tuple[float, list[str]]:
    description = tool.get("description") or ""
    score = 0.0
    problems: list[str] = []
    if SIBLING_TOOL.search(description):
        score += 0.6
    else:
        problems.append("names no sibling tool")
    if _GUIDANCE.search(description):
        score += 0.4
    else:
        problems.append("no use-instead language")
    return score, problems


def _contract(name: str, tool: dict[str, Any]) -> tuple[float, list[str]]:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    description = tool.get("description") or ""
    problems: list[str] = []
    score = 0.0
    if STATES_RESULT.search(description):
        score += 0.4
    else:
        problems.append("does not state its result")
    if all(isinstance(spec, dict) and spec.get("description") for spec in properties.values()):
        score += 0.3
    else:
        problems.append("undocumented parameter")
    if schema.get("additionalProperties") is False:
        score += 0.3
    else:
        problems.append("non-strict schema")
    return score, problems


_DIMENSIONS = (
    ("identity", _identity),
    ("purpose", _purpose),
    ("safety", _safety),
    ("routing", _routing),
    ("contract", _contract),
)


def score_tool(name: str, tool: dict[str, Any]) -> dict[str, Any]:
    """Score one tool: five dimensions in 0–1, total in 0–5."""
    dimensions: dict[str, float] = {}
    problems: list[str] = []
    for label, scorer in _DIMENSIONS:
        value, found = scorer(name, tool)
        dimensions[label] = round(min(value, 1.0), 2)
        problems.extend(found)
    total = round(sum(dimensions.values()) / len(dimensions) * 5, 2)
    return {
        "name": name,
        "family": name.split("_", 1)[0],
        "score": total,
        "dimensions": dimensions,
        "problems": problems,
    }


def score_catalogue(tools: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Score every tool and aggregate, by family and overall."""
    rows = [score_tool(name, tool) for name, tool in sorted(tools.items())]
    families: dict[str, list[float]] = {}
    for row in rows:
        families.setdefault(row["family"], []).append(row["score"])
    overall = round(sum(row["score"] for row in rows) / len(rows), 2) if rows else 0.0
    return {
        "tools": len(rows),
        "average": overall,
        "below_target": sum(1 for row in rows if row["score"] < 4.5),
        "target": 4.5,
        "families": {
            family: round(sum(scores) / len(scores), 2) for family, scores in sorted(families.items())
        },
        "results": rows,
    }

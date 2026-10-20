"""Recall extraction — the one place Recall talks to a language model.

Builds the prompt from ``builtin/prompts/recall/extract.md``, sends it through
:func:`navig.llm.generate.complete_raw` (no context enrichment, no fallback to another model
— exactly the text Recall assembled leaves the machine), and parses the JSON answer.
Validation of quotes, status rules and reconciliation live in :mod:`navig.recall.extract`,
which calls :func:`extract_items` through an injectable callable so tests never hit a network.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PROMPT_SLUG = "recall/extract"
PROMPT_VERSION = "extract-v1"

FOCUS = {
    "all": ("everything: ideas, alternatives weighed, names/terms (adopted or rejected), decisions, "
            "plans, open and answered questions, findings, risks, architecture, lore"),
    "ideas": "ideas, alternatives weighed, names/terms coined or rejected, lore, speculative concepts",
    "decisions": "decisions (and their reasons), plans and milestones, questions asked (answered or not)",
    "findings": "research findings, risks and hidden problems, architecture and technical designs",
}


class ExtractorUnavailable(RuntimeError):
    """The model could not be called (no key, provider down, prompt missing)."""


@dataclass
class ExtractResult:
    items: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    estimated_usage: bool
    raw_chars: int
    parse_error: str = ""


def build_prompt(transcript: str, existing: str, focus: str) -> str:
    from navig.prompts.loader import load_prompt  # noqa: PLC0415

    template = load_prompt(PROMPT_SLUG)
    if template.startswith("Warning: Prompt"):
        raise ExtractorUnavailable(f"prompt {PROMPT_SLUG} is missing from the builtin store")
    # str.replace, not str.format: the template contains literal JSON braces.
    return (template.replace("{focus}", FOCUS.get(focus, FOCUS["all"]))
            .replace("{existing}", existing or "(none)")
            .replace("{transcript}", transcript))


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)


def parse_items(raw: str) -> list[dict[str, Any]]:
    """The ``items`` list from a model answer.

    Tolerant in the ways real answers fail (measured on MiMo Flash): code fences, leading
    prose, trailing text after a valid object ("Extra data"), and one malformed item in an
    otherwise good list — every well-formed item object is salvaged individually. Raises
    ``ValueError`` only when nothing at all can be recovered.
    """
    text = _FENCE.sub("", raw.strip())
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the model answer")
    try:
        data, _end = json.JSONDecoder().raw_decode(text, start)  # ignores trailing text
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    except ValueError:
        pass
    salvaged = _salvage_items(text)
    if salvaged:
        return salvaged
    raise ValueError("model answer is not valid JSON and no item could be salvaged")


def _salvage_items(text: str) -> list[dict[str, Any]]:
    """Parse each top-level object inside the ``items`` array on its own; drop the broken ones."""
    m = re.search(r'"items"\s*:\s*\[', text)
    if not m:
        return []
    out: list[dict[str, Any]] = []
    i, n = m.end(), len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] != "{":
            break
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            ch = text[j]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        try:
            obj = json.loads(text[i:j + 1])
            if isinstance(obj, dict):
                out.append(obj)
        except ValueError:
            pass  # this one item is malformed — keep the rest
        i = j + 1
    return out


def extract_items(prompt: str, *, provider: str, model: str, max_tokens: int = 8192,
                  timeout: float = 180.0) -> ExtractResult:
    """Call the model once and parse its items. Raises ``ExtractorUnavailable`` on a
    transport/credential failure; an unparseable answer comes back with ``parse_error`` set
    (and its token usage, which was still spent)."""
    from navig.llm.generate import complete_raw  # noqa: PLC0415

    try:
        res = complete_raw([{"role": "user", "content": prompt}], provider=provider, model=model,
                           max_tokens=max_tokens, temperature=0.0, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as one typed error
        raise ExtractorUnavailable(f"{provider}:{model} call failed: {type(exc).__name__}: {exc}"[:300]) from exc
    if not res.content.strip():
        # A one-off empty answer is a failed WINDOW (retried on the next run), not a dead
        # provider; the runner stops only after several consecutive failures.
        return ExtractResult(items=[], prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                             estimated_usage=True, raw_chars=0, parse_error="empty answer")
    try:
        items, err = parse_items(res.content), ""
    except ValueError as exc:
        items, err = [], str(exc)
    return ExtractResult(items=items, prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                         estimated_usage=str(res.finish_reason or "").endswith("estimated_usage"),
                         raw_chars=len(res.content), parse_error=err)

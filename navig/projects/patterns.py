"""The Idea Fabric — named AI patterns run over a project's material.

A pattern is a markdown prompt with a small contract in its frontmatter::

    ---
    title: Expand into a brief
    description: one line shown in the UI
    input: project | idea | doc | sources
    output: ideas | doc | plan | todos
    ---
    <the instructions>

Built-in patterns ship in ``builtin/prompts/project/patterns/``; a project adds
its own in ``.navig/patterns/*.md`` (same format, same id space — a project
pattern with a built-in's id overrides it for that project). The runner
(:func:`navig.agent.project_analyst.run_pattern`) supplies the context pack and
the input, and the OUTPUT kind decides what comes back and where an accepted
result goes: ``ideas`` → the ideas inbox, ``doc`` → a brief in ``ideas/``,
``plan`` → a new plan doc beside the others, ``todos`` → a plan doc's list.
Every result is a proposal; nothing is written until the operator accepts it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["INPUTS", "OUTPUTS", "PatternError", "get_pattern", "list_patterns"]

INPUTS = ("project", "idea", "doc", "sources")
OUTPUTS = ("ideas", "doc", "plan", "todos")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_MAX_BYTES = 32 * 1024


class PatternError(ValueError):
    pass


def _builtin_dir() -> Path:
    from navig.platform.paths import builtin_store_dir  # noqa: PLC0415

    return builtin_store_dir() / "prompts" / "project" / "patterns"


def _parse(path: Path, scope: str) -> dict[str, Any] | None:
    from navig.plans.frontmatter import parse_frontmatter  # noqa: PLC0415

    pid = path.stem.lower()
    if not _ID_RE.match(pid):
        return None
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = parse_frontmatter(text) or {}
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S).strip()
    inp = str(fm.get("input") or "project").strip().lower()
    out = str(fm.get("output") or "doc").strip().lower()
    if inp not in INPUTS or out not in OUTPUTS or not body:
        logger.debug("pattern %s skipped: input=%s output=%s", path, inp, out)
        return None
    return {
        "id": pid,
        "title": str(fm.get("title") or pid.replace("-", " ").capitalize()),
        "description": str(fm.get("description") or ""),
        "input": inp,
        "output": out,
        "scope": scope,
        "path": str(path),
        "body": body,
    }


def list_patterns(root: Path | None = None) -> list[dict[str, Any]]:
    """Built-in patterns, overridden/extended by the project's own."""
    found: dict[str, dict[str, Any]] = {}
    dirs: list[tuple[Path, str]] = [(_builtin_dir(), "builtin")]
    if root is not None:
        dirs.append((Path(root) / ".navig" / "patterns", "project"))
    for folder, scope in dirs:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            p = _parse(path, scope)
            if p:
                found[p["id"]] = p
    order = {"brainstorm": 0, "harvest": 1, "expand": 2, "critique": 3, "plan": 4}
    return sorted(found.values(), key=lambda p: (order.get(p["id"], 50), p["scope"] != "builtin", p["id"]))


def get_pattern(pattern_id: str, root: Path | None = None) -> dict[str, Any]:
    pid = (pattern_id or "").strip().lower()
    for p in list_patterns(root):
        if p["id"] == pid:
            return p
    raise PatternError(f"no pattern named {pattern_id!r}")

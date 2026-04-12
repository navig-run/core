"""Shared Markdown frontmatter utilities for the plans and spaces packages.

Canonical home for:
- ``FRONTMATTER_RE`` — compiled pattern matching ``---`` blocks
- :func:`parse_frontmatter` — dict-only parse
- :func:`parse_frontmatter_with_body` — dict + body tuple
- :func:`render_frontmatter` — dict → ``---`` block
- :func:`first_h1` — first ``# Heading`` extractor
- :func:`_safe_read` — UTF-8 file reader (returns '' on failure)
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n?", re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str]:
    """Return a dict of key→value from ``---``-delimited frontmatter."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        values[key.strip()] = val.strip()
    return values


def parse_frontmatter_with_body(text: str) -> tuple[dict[str, str], str]:
    """Parse ``---``-delimited frontmatter.

    Returns
    -------
    tuple[dict[str, str], str]
        ``(frontmatter_dict, body_after_frontmatter)``
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        values[key.strip()] = val.strip()
    return values, text[match.end():]


def render_frontmatter(fm: dict[str, str]) -> str:
    """Render a frontmatter dict as a ``---``-delimited block.

    The returned string ends with ``---\\n`` (one trailing newline).
    Callers that need a blank line before the body should append ``"\\n"``.
    """
    lines = ["---"]
    for key, val in fm.items():
        lines.append(f"{key}: {val}")
    lines.append("---\n")
    return "\n".join(lines)


def first_h1(text: str) -> str:
    """Extract the first ``# Heading`` from Markdown text."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _safe_read(path: Path) -> str:
    """Read *path* as UTF-8, returning ``''`` on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

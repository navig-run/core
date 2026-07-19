"""
Claude Code subagents → NAVIG agent surface.

A Claude Code plugin ships `agents/*.md` — each a subagent with YAML frontmatter
(`name`, `description`, `tools`, `model`, `color`) and a system-prompt body::

    ---
    name: security-reviewer
    description: Audits diffs for vulnerabilities.
    tools: [Read, Grep]
    model: sonnet
    ---
    You are a meticulous security reviewer…

NAVIG surfaces agents in the deck (`/api/deck/admin/agents`) as
`{key, icon, label, subtitle, builtin, enabled}`. This module discovers installed
plugins' agent files (via the plugin-host seam) and translates them into that
shape so a CC plugin's subagents appear live in the agent roster.

Reuses the skills loader's frontmatter parser — never reimplements parsing. Never
raises: a malformed agent file is skipped, not fatal.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.plugins.cc_agents")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(text).lower()).strip("-")


def _parse_agent_file(path: Path) -> dict[str, Any] | None:
    """Parse one CC `agents/*.md` into the NAVIG admin-agent dict, or None."""
    try:
        from navig.skills.loader import _load_frontmatter

        fm, body = _load_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("cc_agents: unparseable agent %s: %s", path, exc)
        return None

    name = str(fm.get("name") or path.stem).strip()
    if not name:
        return None
    key = _slug(name) or _slug(path.stem)
    description = str(fm.get("description") or "").strip()
    if not description:
        # first non-empty body line as a fallback subtitle
        description = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return {
        "key": key,
        "icon": str(fm.get("icon") or fm.get("color") or "🧩"),
        "label": name,
        "subtitle": description[:140],
        "builtin": False,
        "enabled": True,
        "source": "plugin",
        "model": str(fm.get("model") or ""),
    }


def discover_plugin_agents() -> list[dict[str, Any]]:
    """Every installed plugin's `agents/*.md`, translated to admin-agent dicts.

    Deduped by key (first wins). Best-effort — a bad plugin/file is skipped.
    """
    try:
        from navig.plugins.package import plugin_capability_dirs
    except Exception:  # noqa: BLE001
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for agents_dir in plugin_capability_dirs("agents"):
        for md in sorted(agents_dir.glob("*.md")):
            agent = _parse_agent_file(md)
            if agent is None or agent["key"] in seen:
                continue
            seen.add(agent["key"])
            out.append(agent)
    return out

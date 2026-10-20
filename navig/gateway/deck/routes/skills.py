"""Skills browser — discover registered NAVIG skills for the Deck UI.

Skills are markdown documents with frontmatter (id, name, category, tags,
platforms, tools, safety…) executed by the agent layer. This route exposes
just discovery + read for v1; execution is a future iteration.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)


def _ok(data: object, status: int = 200) -> "web.Response":
    return web.json_response({"ok": True, "data": data}, status=status)


def _err(msg: str, status: int = 500) -> "web.Response":
    return web.json_response({"ok": False, "error": msg}, status=status)


def load_skills() -> tuple[list[Any], str | None]:
    """All skills, plus the loader's error if it failed.

    The error is returned rather than swallowed: this used to turn ANY loader failure
    into ``[]`` with a debug-level log, so a broken loader rendered as "you have 0
    skills" — indistinguishable from a fresh install. Shared with the customize
    overview, so the count and the list can never disagree.
    """
    try:
        from navig.skills.loader import load_all_skills  # type: ignore[import]

        return list(load_all_skills() or []), None
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as `error`
        logger.warning("skills loader failed: %s", exc)
        return [], f"skills loader failed: {exc}"


def _summary(s: Any) -> str:
    """The skill's frontmatter ``description``, else the first non-heading body line."""
    desc = (getattr(s, "description", "") or "").strip()
    if desc:
        return desc
    for line in (getattr(s, "body_markdown", "") or "").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text
    return ""


def skill_to_dict(s: Any) -> dict[str, Any]:
    return {
        "id": getattr(s, "id", "") or "",
        "name": getattr(s, "name", "") or "",
        "description": getattr(s, "description", "") or "",
        "version": getattr(s, "version", "") or "",
        "category": getattr(s, "category", "") or "",
        "tags": list(getattr(s, "tags", []) or []),
        "platforms": list(getattr(s, "platforms", []) or []),
        "tools": list(getattr(s, "tools", []) or []),
        "safety": getattr(s, "safety", "") or "",
        "source_path": str(getattr(s, "source_path", "") or ""),
        "summary": _summary(s),
    }


async def handle_deck_skills(request: "web.Request") -> "web.Response":
    """List all registered skills with metadata + a one-line summary."""
    skills, error = load_skills()
    out = [skill_to_dict(s) for s in skills]

    # By-category counts for the overview header
    by_category: dict[str, int] = {}
    for s in out:
        cat = s["category"] or "uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1

    return _ok({
        "count": len(out),
        "by_category": by_category,
        "skills": sorted(out, key=lambda x: (x.get("category", ""), x.get("name", ""))),
        "error": error,
    })


async def handle_deck_skill_detail(request: "web.Request") -> "web.Response":
    """Full skill body (markdown) + metadata for the detail view."""
    skill_id = request.match_info.get("skill_id", "")
    if not skill_id:
        return _err("missing skill id", status=400)
    skills, error = load_skills()
    if error:
        return _err(error, status=503)
    match = next((s for s in skills if getattr(s, "id", "") == skill_id
                  or getattr(s, "name", "") == skill_id), None)
    if match is None:
        return _err(f"skill '{skill_id}' not found", status=404)

    return _ok({
        **skill_to_dict(match),
        "body_markdown": getattr(match, "body_markdown", "") or "",
        "examples": list(getattr(match, "examples", []) or []),
    })

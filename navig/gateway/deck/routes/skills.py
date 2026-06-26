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


def _load() -> list[Any]:
    try:
        from navig.skills.loader import load_all_skills  # type: ignore[import]
        return list(load_all_skills() or [])
    except Exception as exc:
        logger.debug("skills loader unavailable: %s", exc)
        return []


def _skill_to_dict(s: Any) -> dict[str, Any]:
    return {
        "id": getattr(s, "id", "") or "",
        "name": getattr(s, "name", "") or "",
        "version": getattr(s, "version", "") or "",
        "category": getattr(s, "category", "") or "",
        "tags": list(getattr(s, "tags", []) or []),
        "platforms": list(getattr(s, "platforms", []) or []),
        "tools": list(getattr(s, "tools", []) or []),
        "safety": getattr(s, "safety", "") or "",
        "source_path": str(getattr(s, "source_path", "") or ""),
        "summary": (getattr(s, "body_markdown", "") or "").strip().splitlines()[0:1] or [""],
    }


async def handle_deck_skills(request: "web.Request") -> "web.Response":
    """List all registered skills with metadata + first-line summary."""
    skills = _load()
    out = [{**_skill_to_dict(s), "summary": _skill_to_dict(s)["summary"][0] if _skill_to_dict(s)["summary"] else ""}
           for s in skills]

    # By-category counts for the overview header
    by_category: dict[str, int] = {}
    for s in out:
        cat = s["category"] or "uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1

    return _ok({
        "count": len(out),
        "by_category": by_category,
        "skills": sorted(out, key=lambda x: (x.get("category", ""), x.get("name", ""))),
    })


async def handle_deck_skill_detail(request: "web.Request") -> "web.Response":
    """Full skill body (markdown) + metadata for the detail view."""
    skill_id = request.match_info.get("skill_id", "")
    if not skill_id:
        return _err("missing skill id", status=400)
    skills = _load()
    match = next((s for s in skills if getattr(s, "id", "") == skill_id
                  or getattr(s, "name", "") == skill_id), None)
    if match is None:
        return _err(f"skill '{skill_id}' not found", status=404)

    body = getattr(match, "body_markdown", "") or ""
    examples = list(getattr(match, "examples", []) or [])
    return _ok({
        **_skill_to_dict(match),
        "summary": (body.strip().splitlines() or [""])[0],
        "body_markdown": body,
        "examples": examples,
    })

"""navig.agent.tools.prompt_tools: the agent's read-only view of the prompt library.

The tool set has two parts:

* ``prompt_library_search`` finds prompts and returns *when to use* each one, so
  the agent can answer "which prompt should I launch for X?".
* ``prompt_library_get`` returns one prompt's text, so the agent can quote it or
  adapt it.

Delivering a prompt to Telegram word for word is the job of ``/prompt`` and the
"send me the X prompt" sniff (``navig.telegram.prompt_actions``), not of these
tools. A tool's output goes back to the model, which would paraphrase it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_BODY_CAP = 12_000


class PromptLibrarySearchTool(BaseTool):
    name = "prompt_library_search"
    description = (
        "Search the user's saved prompt library (their own reusable prompts for coding, "
        "writing, business, health, images…). Returns id, title, domain/category and "
        "'when to use' for each match. Use it when the user asks which prompt to use, "
        "or for a prompt they saved. Read-only."
    )
    owner_only = False
    parameters = [
        {"name": "query", "type": "string", "description": "Words describing the task or prompt.", "required": True},
        {"name": "domain", "type": "string", "description": "Optional domain filter (dev, human, business, creative, system, projects).", "required": False},
        {"name": "limit", "type": "integer", "description": "Max results (default 8).", "required": False},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(name=self.name, success=False, error="'query' is required")
        try:
            from navig.store.prompt_library import get_prompt_store

            limit = max(1, min(int(args.get("limit") or 8), 25))
            rows = get_prompt_store().list(q=query, domain=str(args.get("domain") or ""), limit=limit)
            out = [
                {
                    "id": r.id, "title": r.title, "domain": r.domain, "category": r.category,
                    "workflow": r.collection or None, "step": r.step, "when_to_use": r.use_when,
                    "fill_in": r.variables[:8], "favorite": r.favorite,
                }
                for r in rows
            ]
            note = "" if out else "No saved prompt matches. Try fewer or different words."
            return ToolResult(
                name=self.name, success=True,
                output=json.dumps({"matches": out, "note": note, "send_hint": "/prompt <id>"}, ensure_ascii=False),
            )
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.debug("prompt_library_search failed: %s", exc)
            return ToolResult(name=self.name, success=False, error=str(exc))


class PromptLibraryGetTool(BaseTool):
    name = "prompt_library_get"
    description = (
        "Read one saved prompt from the user's prompt library by id (from "
        "prompt_library_search). Returns its full text and placeholders. Read-only."
    )
    owner_only = False
    parameters = [
        {"name": "id", "type": "string", "description": "Prompt id, e.g. dev/coding/debug-root-cause.", "required": True},
    ]

    async def run(self, args: dict[str, Any], on_status: StatusCallback | None = None) -> ToolResult:
        pid = str(args.get("id") or "").strip()
        if not pid:
            return ToolResult(name=self.name, success=False, error="'id' is required")
        try:
            from navig.store.prompt_library import get_prompt_store

            store = get_prompt_store()
            rec = store.get(pid)
            if rec is None:
                hits = store.resolve(pid, limit=1)
                rec = hits[0] if hits else None
            if rec is None:
                return ToolResult(name=self.name, success=False, error=f"no prompt {pid!r}")
            body = rec.body if len(rec.body) <= _BODY_CAP else rec.body[:_BODY_CAP] + "\n…[truncated]"
            return ToolResult(
                name=self.name, success=True,
                output=json.dumps(
                    {"id": rec.id, "title": rec.title, "when_to_use": rec.use_when,
                     "fill_in": rec.variables, "body": body},
                    ensure_ascii=False,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — tools never raise
            logger.debug("prompt_library_get failed: %s", exc)
            return ToolResult(name=self.name, success=False, error=str(exc))


def register_prompt_tools() -> None:
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(PromptLibrarySearchTool(), toolset="prompts")
    _AGENT_REGISTRY.register(PromptLibraryGetTool(), toolset="prompts")

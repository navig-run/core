"""
navig.agent.tools.wiki_tools — Agent tools wrapping navig's wiki/RAG system.

Tool catalog
------------
``wiki_search``  — BM25 search over the project wiki.
``wiki_read``    — Read a specific wiki page by path.
``wiki_write``   — Drop a new page into the wiki inbox for AI processing.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

_MAX_PAGE_CHARS = 3_000


def _resolve_wiki_root() -> Path:
    try:
        from navig.wiki_rag import get_wiki_rag

        return get_wiki_rag().wiki_path
    except Exception:
        return Path(".navig/wiki")


def search(query: str, space: str | None = None, limit: int = 5) -> list[dict[str, str]]:
    """Search wiki content and return compact entries for PlanContext.

    Args:
        query: Search query text.
        space: Optional space name (currently best-effort contextual hint).
        limit: Maximum number of results.

    Returns:
        List of dicts with keys ``title``, ``excerpt``, ``path``.
    """
    from navig.wiki_rag import get_wiki_rag

    q = (query or "").strip()
    if not q:
        return []

    # space is currently a hint only; retained for API compatibility
    _ = space

    rag = get_wiki_rag()
    raw = rag.search(q, top_k=max(1, limit))
    out: list[dict[str, str]] = []
    for row in raw[:limit]:
        out.append(
            {
                "title": str(row.get("title", "")),
                "excerpt": str(row.get("chunk", ""))[:240],
                "path": str(row.get("path", "")),
            }
        )
    return out


class WikiSearchTool(BaseTool):
    """Search the project wiki using BM25 full-text ranking."""

    name = "wiki_search"
    description = (
        "Search the project wiki for relevant pages and documentation.  "
        "Uses BM25 ranking to return the most relevant results.  "
        "Use this before taking action to look up runbooks, decisions, or project context."
    )
    owner_only = False
    parameters = [
        {
            "name": "query",
            "type": "string",
            "description": "Search query for the project wiki",
            "required": True,
        },
        {
            "name": "limit",
            "type": "integer",
            "description": "Maximum results to return (default 5)",
            "required": False,
        },
        {
            "name": "folder",
            "type": "string",
            "description": "Filter results to a specific folder (e.g. 'technical', 'hub/tasks')",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(name=self.name, success=False, error="'query' arg is required")

        limit = int(args.get("limit") or 5)
        folder_filter = args.get("folder") or None

        try:
            from navig.wiki_rag import get_wiki_rag

            rag = get_wiki_rag()
            raw_results = rag.search(query, top_k=limit * 2)
        except Exception as exc:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Wiki search failed: {exc}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        # Filter by folder if requested
        if folder_filter:
            raw_results = [r for r in raw_results if folder_filter in r.get("folder", "")]

        results = raw_results[:limit]

        if not results:
            return ToolResult(
                name=self.name,
                success=True,
                output={"query": query, "results": [], "message": "No wiki pages found."},
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        return ToolResult(
            name=self.name,
            success=True,
            output={"query": query, "results": results, "count": len(results)},
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


class WikiReadTool(BaseTool):
    """Read the full content of a specific wiki page."""

    name = "wiki_read"
    description = (
        "Read the full content of a wiki page by its relative path.  "
        "Use wiki_search first to find the path."
    )
    owner_only = False
    parameters = [
        {
            "name": "page",
            "type": "string",
            "description": "Relative page path as returned by wiki_search (e.g. 'technical/architecture/overview')",
            "required": True,
        }
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        page = args.get("page", "").strip()
        if not page:
            return ToolResult(name=self.name, success=False, error="'page' arg is required")

        # Resolve path against known wiki roots
        candidates: list[Path] = []
        for base in [_resolve_wiki_root(), Path("~/.navig/wiki").expanduser()]:
            p = base / page
            if not p.suffix:
                p = p.with_suffix(".md")
            candidates.append(p)

        found: Path | None = None
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                found = candidate
                break

        if found is None:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Wiki page not found: {page!r}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        try:
            content = found.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Permission denied reading wiki page: {found}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        if len(content) > _MAX_PAGE_CHARS:
            content = content[:_MAX_PAGE_CHARS] + f"\n…[truncated at {_MAX_PAGE_CHARS} chars]"

        return ToolResult(
            name=self.name,
            success=True,
            output={"page": page, "path": str(found), "content": content},
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


class WikiWriteTool(BaseTool):
    """Write a new page to the wiki inbox for AI categorisation."""

    name = "wiki_write"
    description = (
        "Create a new wiki page by writing it to the inbox for categorisation.  "
        "The page will be automatically moved to the right folder by the wiki AI processor."
    )
    owner_only = False
    parameters = [
        {
            "name": "title",
            "type": "string",
            "description": "Title of the wiki page",
            "required": True,
        },
        {
            "name": "content",
            "type": "string",
            "description": "Markdown content of the page",
            "required": True,
        },
        {
            "name": "folder",
            "type": "string",
            "description": "Optional target folder hint (e.g. 'technical/troubleshooting')",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        title = args.get("title", "").strip()
        content = args.get("content", "").strip()
        folder = args.get("folder", "").strip()

        if not title:
            return ToolResult(name=self.name, success=False, error="'title' arg is required")
        if not content:
            return ToolResult(name=self.name, success=False, error="'content' arg is required")

        # Sanitise filename
        import re
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")[:80]
        filename = f"{slug}.md"

        inbox = _resolve_wiki_root() / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        # Add folder hint as frontmatter comment if provided
        header = f"---\ntitle: {title}\n"
        if folder:
            header += f"folder: {folder}\n"
        header += "---\n\n"

        file_path = inbox / filename
        try:
            file_path.write_text(header + content, encoding="utf-8")
        except PermissionError:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Permission denied writing wiki page: {file_path}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        return ToolResult(
            name=self.name,
            success=True,
            output={
                "written": True,
                "title": title,
                "inbox_path": str(file_path),
                "message": "Page written to wiki inbox. Run 'navig wiki inbox process' to categorise.",
            },
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )

"""
NAVIG Tools Package

Extended capabilities for AI agents including:
- Sandboxed command execution
- Image generation
- Web tools (fetch, search, docs)
"""

from typing import TYPE_CHECKING

# Lazy imports for optional dependencies
_sandbox = None
_image_gen = None
_web = None


def get_sandbox():
    """Get sandbox module (lazy load)."""
    global _sandbox
    if _sandbox is None:
        from . import sandbox as _sandbox
    return _sandbox


def get_image_generator():
    """Get image generation module (lazy load)."""
    global _image_gen
    if _image_gen is None:
        from . import image_generation as _image_gen
    return _image_gen


def get_web_tools():
    """Get web tools module (lazy load)."""
    global _web
    if _web is None:
        from . import web as _web
    return _web


def is_sandbox_available() -> bool:
    """Check if Docker sandbox is available."""
    try:
        from .sandbox import is_sandbox_available as _check
        return _check()
    except ImportError:
        return False


def is_image_generation_available() -> bool:
    """Check if image generation is available."""
    try:
        from .image_generation import is_image_generation_available as _check
        return _check()
    except ImportError:
        return False


def is_web_tools_available() -> bool:
    """Check if web tools are available."""
    try:
        from .web import web_fetch
        return True
    except ImportError:
        return False


# Direct imports for common web tools (these don't have heavy dependencies)
try:
    from .web import (
        SearchResult,
        WebFetchResult,
        WebSearchResult,
        search_docs,
        web_fetch,
        web_search,
    )
except ImportError:
    web_fetch = None  # type: ignore[assignment, misc]
    web_search = None  # type: ignore[assignment, misc]
    search_docs = None  # type: ignore[assignment, misc]
    WebFetchResult = None  # type: ignore[assignment, misc]
    WebSearchResult = None  # type: ignore[assignment, misc]
    SearchResult = None  # type: ignore[assignment, misc]


__all__ = [
    # Lazy loaders
    "get_sandbox",
    "get_image_generator",
    "get_web_tools",
    # Availability checks
    "is_sandbox_available",
    "is_image_generation_available",
    "is_web_tools_available",
    # Web tools (direct access)
    "web_fetch",
    "web_search",
    "search_docs",
    "WebFetchResult",
    "WebSearchResult",
    "SearchResult",
    # Tool Router & Registry (Section 17)
    "get_tool_registry",
    "get_tool_router",
    "parse_llm_action",
    "get_pipeline_registry",
    "ToolCallAction",
    "RespondAction",
    "ToolResult",
]


# ─── Tool Router & Registry (lazy) ────────────────────────────
def get_tool_registry():
    """Get the global ToolRegistry singleton (lazy load)."""
    from .router import get_tool_registry as _get
    return _get()


def get_tool_router(safety_policy=None):
    """Get the global ToolRouter singleton (lazy load)."""
    from .router import get_tool_router as _get
    return _get(safety_policy=safety_policy)


def parse_llm_action(text: str):
    """Parse LLM response text into a typed action (lazy load)."""
    from .schemas import parse_llm_action as _parse
    return _parse(text)


# ─── Pipeline ToolRegistry singleton ──────────────────────────
_pipeline_registry = None


def get_pipeline_registry():
    """Get the global pipeline ToolRegistry singleton (lazy, pre-seeded)."""
    global _pipeline_registry
    if _pipeline_registry is None:
        from .registry import BaseTool, ToolRegistry, ToolResult  # type: ignore[import]

        class _StubTool(BaseTool):
            description: str = ""

            async def run(self, args, on_status=None):  # type: ignore[override]
                return ToolResult(name=self.name, success=True)

        def _make(tool_name: str) -> BaseTool:
            t = _StubTool.__new__(_StubTool)
            t.name = tool_name
            t.description = tool_name.replace("_", " ").title()
            return t

        reg = ToolRegistry()
        for _name in ("site_check", "browser_fetch", "search", "code_exec_sandbox", "skill_run"):
            reg.register(_make(_name))
        _pipeline_registry = reg
    return _pipeline_registry


# Re-export key types (lazy via TYPE_CHECKING for zero import cost)
if TYPE_CHECKING:
    from .schemas import RespondAction, ToolCallAction, ToolResult  # noqa: F811

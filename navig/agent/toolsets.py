"""
navig.agent.toolsets — Named toolset definitions for the agentic ReAct loop.

Toolsets scope which tools the LLM can see in each `tools=[]` parameter.
Narrower toolsets = cheaper schemas + better tool selection + lower latency.

Usage:
    from navig.agent.toolsets import TOOLSETS, resolve_toolset_names, validate_toolset
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Toolset Definitions
# ─────────────────────────────────────────────────────────────

#: Core read/write/execute tools available in every agentic session.
NAVIG_CORE_TOOLS: frozenset[str] = frozenset({"bash_exec", "read_file", "write_file", "list_files"})

#: Complete toolset registry. ``None`` means "all registered tools".
TOOLSETS: dict[str, list[str] | None] = {
    # Minimal safe surface for general tasks
    "core": list(NAVIG_CORE_TOOLS),
    # Web retrieval only
    "search": ["search", "web_fetch"],
    # Core + search combined (good for research tasks)
    "research": [
        "bash_exec",
        "read_file",
        "write_file",
        "list_files",
        "search",
        "web_fetch",
        "wiki_search",
        "wiki_read",
        "kb_lookup",
        "memory_read",
    ],
    # Software development tasks
    "code": ["bash_exec", "read_file", "write_file", "list_files", "search", "web_fetch"],
    # Git operations (status, diff, log, commit, stash)
    "git": ["git_status", "git_diff", "git_log", "git_commit", "git_stash"],
    # Structured DevOps command surface (requires owner_only clearance)
    "devops": [
        "bash_exec",
        "read_file",
        "write_file",
        "list_files",
        "navig_run",
        "navig_file_add",
        "navig_file_get",
        "navig_file_show",
        "navig_db_query",
        "navig_db_dump",
        "navig_db_list",
        "navig_docker_ps",
        "navig_docker_logs",
        "navig_docker_exec",
        "navig_docker_restart",
        "navig_host_show",
        "navig_host_test",
        "navig_host_monitor",
        "navig_web_vhosts",
        "navig_web_reload",
        "navig_app_list",
        "navig_app_show",
        "git_status",
        "git_diff",
        "git_log",
        "git_stash",
    ],
    # Knowledge base + wiki access
    "memory": ["memory_read", "memory_write", "memory_delete", "kb_lookup", "fts_search"],
    # Wiki-specific toolset
    "wiki": ["wiki_search", "wiki_read", "wiki_write"],
    # Sub-agent delegation (requires MVP2 delegate.py)
    "delegation": ["delegate_task"],
    # All registered tools (use sparingly — large schema)
    "full": None,
}

# ─────────────────────────────────────────────────────────────
# Parallel-safety classifications
# ─────────────────────────────────────────────────────────────

#: Tools that are safe to execute concurrently within a single turn.
PARALLEL_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "search",
        "web_fetch",
        "read_file",
        "list_files",
        "wiki_search",
        "wiki_read",
        "kb_lookup",
        "memory_read",
        "navig_db_list",
        "navig_host_show",
        "navig_host_test",
        "navig_host_monitor",
        "navig_docker_ps",
        "navig_web_vhosts",
        "navig_app_list",
        "navig_app_show",
        "fts_search",
        "git_status",
        "git_diff",
        "git_log",
        "git_stash",
    }
)

#: Tools that MUST run sequentially — any mutation or stateful side effect.
NEVER_PARALLEL_TOOLS: frozenset[str] = frozenset(
    {
        "bash_exec",
        "write_file",
        "memory_write",
        "memory_delete",
        "wiki_write",
        "navig_run",
        "navig_file_add",
        "navig_db_query",
        "navig_db_dump",
        "navig_docker_exec",
        "navig_docker_restart",
        "navig_web_reload",
        "delegate_task",
        "git_commit",
    }
)


# ─────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────


def validate_toolset(name: str) -> None:
    """Raise ``ValueError`` if *name* is not a known toolset.

    Args:
        name: Toolset name to validate.

    Raises:
        ValueError: If the toolset name is not registered.
    """
    if name not in TOOLSETS:
        known = ", ".join(sorted(TOOLSETS))
        raise ValueError(f"Unknown toolset {name!r}. Valid options: {known}")


def resolve_toolset_names(name: str) -> list[str] | None:
    """Return the list of tool names for a given toolset.

    Args:
        name: Toolset name (must be in :data:`TOOLSETS`).

    Returns:
        List of tool names, or ``None`` if the toolset is ``"full"``
        (meaning all registered tools should be used).

    Raises:
        ValueError: If the toolset name is unknown.
    """
    validate_toolset(name)
    return TOOLSETS[name]


def merge_toolsets(toolsets: list[str]) -> list[str] | None:
    """Merge multiple toolset names into a deduplicated tool list.

    Returns ``None`` if any toolset resolves to ``"full"`` (all tools).

    Args:
        toolsets: List of toolset names to merge.

    Returns:
        Deduplicated tool list in stable order, or ``None`` for "all tools".
    """
    seen: dict[str, None] = {}  # use dict to maintain insertion order
    for ts_name in toolsets:
        names = resolve_toolset_names(ts_name)
        if names is None:
            return None  # "full" trumps everything
        for n in names:
            seen[n] = None
    return list(seen)


def is_parallel_safe(tool_name: str) -> bool:
    """Return True if *tool_name* is safe to run concurrently."""
    if tool_name in NEVER_PARALLEL_TOOLS:
        return False
    return tool_name in PARALLEL_SAFE_TOOLS

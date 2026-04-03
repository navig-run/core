"""
navig.agent.tools — Agent tool registration package.

This package registers all built-in navig tools into the :data:`_AGENT_REGISTRY`
singleton.  Call :func:`register_core_tools()` once at ``ConversationalAgent``
init time when ``agentic=True``.

Tool groups
-----------
``register_core_tools()``   — bash_exec, read_file, write_file, list_files

``register_search_tools()`` — search, web_fetch

``register_memory_tools()`` — memory_read, memory_write, memory_delete, kb_lookup

``register_wiki_tools()``   — wiki_search, wiki_read, wiki_write

``register_devops_tools()`` — navig_run, navig_file_*, navig_db_*, navig_docker_*,
                              navig_host_*, navig_web_*, navig_app_*

``register_background_task_tools()`` — background_task_start, _status, _output, _kill

``register_worktree_tools()``       — worktree_create, _list, _merge, _remove

``register_all_tools()``    — convenience wrapper that calls all of the above

Usage::

    from navig.agent.tools import register_all_tools
    register_all_tools()
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register_core_tools() -> None:
    """Register bash_exec, read_file, write_file, list_files."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools.file_tools import ListFilesTool, ReadFileTool, WriteFileTool
    from navig.tools.bash_exec import BashExecTool

    _AGENT_REGISTRY.register(BashExecTool(), toolset="core")
    _AGENT_REGISTRY.register(ReadFileTool(), toolset="core")
    _AGENT_REGISTRY.register(WriteFileTool(), toolset="core")
    _AGENT_REGISTRY.register(ListFilesTool(), toolset="core")
    logger.debug("Agent core tools registered: bash_exec, read_file, write_file, list_files")


def register_search_tools() -> None:
    """Register search (DuckDuckGo) and web_fetch tools."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.tools.search import SearchTool
    from navig.tools.web_fetch import WebFetchTool

    _AGENT_REGISTRY.register(SearchTool(), toolset="search")
    _AGENT_REGISTRY.register(WebFetchTool(), toolset="search")
    logger.debug("Agent search tools registered: search, web_fetch")


def register_memory_tools() -> None:
    """Register memory_read, memory_write, memory_delete, kb_lookup tools.

    These tools wrap navig's existing KeyFact / KB store via
    ``navig.agent.tools.memory_tools`` (MVP1 F-07).
    """
    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools.memory_tools import (
            KBLookupTool,
            MemoryDeleteTool,
            MemoryReadTool,
            MemoryWriteTool,
        )

        _AGENT_REGISTRY.register(MemoryReadTool(), toolset="memory")
        _AGENT_REGISTRY.register(MemoryWriteTool(), toolset="memory")
        _AGENT_REGISTRY.register(MemoryDeleteTool(), toolset="memory")
        _AGENT_REGISTRY.register(KBLookupTool(), toolset="memory")
        logger.debug("Agent memory tools registered")
    except ImportError as exc:
        logger.debug("Memory tools not available (skip): %s", exc)


def register_wiki_tools() -> None:
    """Register wiki_search, wiki_read, wiki_write tools.

    Wraps ``navig.wiki_rag`` if available.
    """
    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools.wiki_tools import WikiReadTool, WikiSearchTool, WikiWriteTool

        _AGENT_REGISTRY.register(WikiSearchTool(), toolset="wiki")
        _AGENT_REGISTRY.register(WikiReadTool(), toolset="wiki")
        _AGENT_REGISTRY.register(WikiWriteTool(), toolset="wiki")
        logger.debug("Agent wiki tools registered")
    except ImportError as exc:
        logger.debug("Wiki tools not available (skip): %s", exc)


def register_devops_tools() -> None:
    """Register all DevOps (remote-host) tools: navig_run, navig_file_*, navig_db_*, etc.

    These tools wrap navig's SSH/config layer for structured agent access
    to remote hosts, databases, Docker containers, and web servers (MVP3 F-16).
    """
    try:
        from navig.agent.tools.devops_tools import (
            register_devops_tools as _do_register,
        )

        _do_register()
        logger.debug("Agent devops tools registered")
    except ImportError as exc:
        logger.debug("DevOps tools not available (skip): %s", exc)


def register_plan_context_tools() -> None:
    """Register the get_plan_context tool."""
    try:
        from navig.agent.tools.plan_tools import register_plan_context_tool

        register_plan_context_tool()
        logger.debug("Agent plan context tools registered")
    except ImportError as exc:
        logger.debug("Plan context tool not available (skip): %s", exc)


def register_background_task_tools() -> None:
    """Register background_task_start, _status, _output, _kill tools (FB-04)."""
    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools.background_task_tools import (
            BackgroundTaskKillTool,
            BackgroundTaskOutputTool,
            BackgroundTaskStartTool,
            BackgroundTaskStatusTool,
        )

        _AGENT_REGISTRY.register(BackgroundTaskStartTool(), toolset="background_task")
        _AGENT_REGISTRY.register(BackgroundTaskStatusTool(), toolset="background_task")
        _AGENT_REGISTRY.register(BackgroundTaskOutputTool(), toolset="background_task")
        _AGENT_REGISTRY.register(BackgroundTaskKillTool(), toolset="background_task")
        logger.debug("Agent background task tools registered")
    except ImportError as exc:
        logger.debug("Background task tools not available (skip): %s", exc)


def register_worktree_tools() -> None:
    """Register worktree_create, _list, _merge, _remove tools (FB-05)."""
    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools.worktree_tools import (
            WorktreeCreateTool,
            WorktreeListTool,
            WorktreeMergeTool,
            WorktreeRemoveTool,
        )

        _AGENT_REGISTRY.register(WorktreeCreateTool(), toolset="worktree")
        _AGENT_REGISTRY.register(WorktreeListTool(), toolset="worktree")
        _AGENT_REGISTRY.register(WorktreeMergeTool(), toolset="worktree")
        _AGENT_REGISTRY.register(WorktreeRemoveTool(), toolset="worktree")
        logger.debug("Agent worktree tools registered")
    except ImportError as exc:
        logger.debug("Worktree tools not available (skip): %s", exc)


def register_coordinator_tools() -> None:
    """Register coordinator_run and coordinator_status tools (FB-01)."""
    try:
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools.coordinator_tools import (
            CoordinatorRunTool,
            CoordinatorStatusTool,
        )

        _AGENT_REGISTRY.register(CoordinatorRunTool(), toolset="coordinator")
        _AGENT_REGISTRY.register(CoordinatorStatusTool(), toolset="coordinator")
        logger.debug("Agent coordinator tools registered")
    except ImportError as exc:
        logger.debug("Coordinator tools not available (skip): %s", exc)


def register_all_tools() -> None:
    """Register all available built-in agent tools.

    Each group is registered independently; failures in optional groups
    are logged at DEBUG and skipped without breaking the others.
    """
    register_core_tools()
    register_search_tools()

    for name, fn in [
        ("memory", register_memory_tools),
        ("wiki", register_wiki_tools),
        ("devops", register_devops_tools),
        ("plan_context", register_plan_context_tools),
        ("background_task", register_background_task_tools),
        ("worktree", register_worktree_tools),
        ("coordinator", register_coordinator_tools),
    ]:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Optional tool group %r failed to register: %s", name, exc)

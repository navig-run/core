"""
navig.hooks — Structured lifecycle hook system for the NAVIG AI agent layer.

Ported and adapted from Claude Code's TypeScript hooks subsystem
(``hookEvents.ts``, ``hooksConfigManager.ts``, ``postSamplingHooks.ts``).

Six event types model the full tool-call lifecycle:

    PRE_TOOL_USE           Before a tool is invoked.  Hook exit-code 2 injects
                           stderr into the model context and blocks the call.
    POST_TOOL_USE          After a tool returns successfully.
    POST_TOOL_USE_FAILURE  After a tool raises an exception.
    PERMISSION_DENIED      When safety_guard or permission rules deny a call.
    NOTIFICATION           Side-channel notification (no blocking semantics).
    SESSION_START          When a new conversation session begins.

Hook scripts are configured in ``.navig/hooks.yaml`` (project) or
``~/.navig/hooks.yaml`` (global).  See ``navig/hooks/registry.py`` for
schema details.

Public API::

    from navig.hooks import fire_hook, HookEvent, HookContext

    ctx = HookContext(
        event=HookEvent.PRE_TOOL_USE,
        tool_name="bash",
        tool_input={"command": "ls -la"},
        session_id="telegram:user:123",
    )
    result = fire_hook(ctx)
    if result.block:
        # Inject result.message into model context
        ...
"""

from __future__ import annotations

from .events import HookContext, HookEvent, HookResult
from .executor import HookExecutor
from .registry import HookRegistry

__all__ = [
    "HookContext",
    "HookEvent",
    "HookResult",
    "HookRegistry",
    "HookExecutor",
    "fire_hook",
]

_executor: HookExecutor | None = None


def fire_hook(ctx: HookContext) -> HookResult:
    """Fire all registered hooks for *ctx* and return a merged ``HookResult``.

    This is the primary public entry point.  It is synchronous and
    never raises — failures are swallowed and logged.
    """
    global _executor
    if _executor is None:
        _executor = HookExecutor(HookRegistry())
    return _executor.run(ctx)

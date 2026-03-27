"""
Tool Bridge — adapts Registry-A (BaseTool) instances for Registry-B (ToolRouter).

Registry A (navig.tools.registry) is async-native and uses:
    BaseTool.run(args, on_status) -> ToolResult (registry.ToolResult)

Registry B (navig.tools.router) is sync/async flexible and uses:
    ToolHandler(**params) -> Any
    ToolMeta(name, domain, safety, ...)

This bridge converts BaseTool objects into (ToolMeta, handler) pairs that
ToolRegistry (router) can store, without modifying either registry's code.

Usage::

    from navig.tools.bridge import bridge_all
    from navig.tools.registry import ToolRegistry as BaseRegistry
    from navig.tools.router import get_tool_registry

    base_reg = BaseRegistry()
    base_reg.register(MyTool())

    n = bridge_all(base_reg, get_tool_registry())
    print(f"Bridged {n} tools")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.tools.registry import BaseTool
    from navig.tools.registry import ToolRegistry as BaseRegistry
    from navig.tools.router import ToolRegistry as RouterRegistry

logger = logging.getLogger("navig.tools.bridge")


# =============================================================================
# Adapters
# =============================================================================


def _make_handler(base_tool: BaseTool):
    """
    Produce an async handler function wrapping *base_tool*.run().

    The wrapper:
    - Accepts **kwargs matching the router's call convention.
    - Calls base_tool.run(args=kwargs) with no on_status callback.
    - Returns the output on success, raises RuntimeError on failure.
    """

    async def _handler(**kwargs: Any) -> Any:
        from navig.tools.registry import ToolResult as BaseResult

        result: BaseResult = await base_tool.run(kwargs)
        if result.success:
            return result.output
        raise RuntimeError(result.error or f"Tool '{base_tool.name}' returned failure")

    _handler.__name__ = f"bridged_{base_tool.name}"
    return _handler


def adapt_base_tool(base_tool: BaseTool) -> tuple:
    """
    Convert a BaseTool into a (ToolMeta, async_handler) tuple.

    The produced ToolMeta uses sensible defaults:
    - domain: GENERAL
    - safety: SAFE
    - description: derived from the tool's class docstring
    - tags: ["bridged"]

    If *base_tool* exposes any of these attributes directly, they are used:
    ``domain``, ``safety``, ``description``, ``tags``, ``parameters_schema``.
    """
    from navig.tools.router import SafetyLevel, ToolDomain, ToolMeta

    name = base_tool.name
    description = (
        getattr(base_tool, "description", None)
        or ((base_tool.__class__.__doc__ or "").strip().splitlines()[0])
    )
    domain_raw = getattr(base_tool, "domain", ToolDomain.GENERAL)
    if isinstance(domain_raw, str):
        try:
            domain_raw = ToolDomain(domain_raw)
        except ValueError:
            domain_raw = ToolDomain.GENERAL

    safety_raw = getattr(base_tool, "safety", SafetyLevel.SAFE)
    if isinstance(safety_raw, str):
        try:
            safety_raw = SafetyLevel(safety_raw)
        except ValueError:
            safety_raw = SafetyLevel.SAFE

    tags = list(getattr(base_tool, "tags", [])) + ["bridged"]
    parameters_schema = getattr(base_tool, "parameters_schema", {})

    meta = ToolMeta(
        name=name,
        domain=domain_raw,
        description=description,
        safety=safety_raw,
        parameters_schema=parameters_schema,
        tags=tags,
    )
    handler = _make_handler(base_tool)
    return meta, handler


def register_base_tool(
    router_registry: RouterRegistry,
    base_tool: BaseTool,
    overwrite: bool = False,
) -> bool:
    """
    Bridge *base_tool* into *router_registry*.

    Returns True if registration happened, False if the tool was already
    present and *overwrite* is False.
    """
    if not overwrite and router_registry.get_tool(base_tool.name) is not None:
        logger.debug(
            "bridge: skipping %s — already registered (overwrite=False)",
            base_tool.name,
        )
        return False

    meta, handler = adapt_base_tool(base_tool)
    router_registry.register(meta, handler=handler)
    logger.debug(
        "bridge: registered %s (domain=%s, safety=%s)",
        meta.name,
        meta.domain.value,
        meta.safety.value,
    )
    return True


def bridge_all(
    base_registry: BaseRegistry,
    router_registry: RouterRegistry,
    overwrite: bool = False,
) -> int:
    """
    Bridge every tool in *base_registry* into *router_registry*.

    Args:
        base_registry:   Source Registry-A (navig.tools.registry.ToolRegistry).
        router_registry: Target Registry-B (navig.tools.router.ToolRegistry).
        overwrite:       When False, skip tools already present in router_registry.

    Returns:
        Number of tools that were actually registered (skipped tools not counted).
    """
    count = 0
    for name in base_registry.names():
        tool = base_registry.get(name)
        if tool is None:
            continue
        if register_base_tool(router_registry, tool, overwrite=overwrite):
            count += 1
    if count:
        logger.info("bridge_all: bridged %d tools from BaseRegistry → RouterRegistry", count)
    return count


# =============================================================================
# Graceful degradation helpers
# =============================================================================


def try_get_handler(
    router_registry: RouterRegistry,
    name: str,
) -> Any | None:
    """
    Return the handler for *name* from *router_registry*, or ``None``.

    Never raises.  Logs a warning when the tool is missing so callers can
    decide how to degrade rather than crashing the pipeline.

    Example::

        handler = try_get_handler(registry, "bash_exec")
        if handler is None:
            return fallback_result
        result = await handler(**params)
    """
    try:
        handler = router_registry.get_handler(name)
        if handler is None:
            logger.warning("bridge: tool '%s' not found in registry — degrading gracefully", name)
        return handler
    except Exception:
        logger.warning(
            "bridge: error resolving tool '%s' — degrading gracefully",
            name,
            exc_info=True,
        )
        return None

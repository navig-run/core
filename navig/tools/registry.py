"""
ToolRegistry — Registration and safe execution of NAVIG tools.

Each tool emits granular status events consumed by the StatusRenderer pipeline.
Tool failures are isolated: they return ToolResult(success=False) and never
propagate exceptions to the caller.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

# Type alias: on_status(step, detail, progress) callback
StatusCallback = Callable[[str, str, int], Coroutine]


@dataclass
class ToolResult:
    """Result returned by every BaseTool.run() call."""

    name: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    status_events: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line human-readable summary of the result."""
        if self.success:
            if isinstance(self.output, dict):
                return " · ".join(f"{k}: {v}" for k, v in self.output.items() if v is not None)
            return str(self.output)[:200]
        return f"⚠️ {self.error or 'unknown error'}"


class BaseTool(ABC):
    """Abstract base for all NAVIG pipeline tools."""

    name: str = "base_tool" # id maps directly to NavigToolMeta.id
    description: str = ""
    parameters: dict[str, Any] = {} # Defines NavigToolParameter keys
    owner_only: bool = False

    def get_meta(self) -> dict[str, Any]:
        """
        Exports the tool definition aligning strictly 
        with the navig-shared/core/src/types/agent.ts NavigToolMeta interface.
        """
        return {
            "id": self.name,
            "name": self.__class__.__name__,
            "description": self.description,
            "parameters": self.parameters,
            "ownerOnly": self.owner_only
        }

    @abstractmethod
    async def run(
        self,
        args: Dict[str, Any],
        on_status: Optional[StatusCallback] = None,
    ) -> ToolResult:
        """Execute the tool with the given args.

        Must NEVER raise — return ToolResult(success=False, error=...) instead.
        on_status(step, detail, progress) is called at each sub-step to feed
        the live StatusRenderer pipeline.
        """

    async def _emit(
        self,
        on_status: Optional[StatusCallback],
        step: str,
        detail: str = "",
        progress: int = 0,
    ) -> None:
        """Fire an on_status event if a callback is provided."""
        if on_status is not None:
            try:
                await on_status(step, detail, progress)
            except Exception as e:
                logger.debug("on_status callback error in tool %s: %s", self.name, e)


class ToolRegistry:
    """Registry of all available tools.  Thread-safe singleton pattern."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool by its ``name`` attribute."""
        self._tools[tool.name] = tool
        logger.debug("Tool registered: %s", tool.name)

    def get(self, name: str) -> Optional[BaseTool]:
        """Look up a tool by name.  Returns None if not registered."""
        return self._tools.get(name)

    def names(self) -> List[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def get_meta_map(self) -> Dict[str, Dict[str, Any]]:
        """Return a mapping of all tools for UI integration."""
        return {tool.name: tool.get_meta() for tool in self._tools.values()}

    async def run_tool(
        self,
        name: str,
        args: Dict[str, Any],
        on_status: Optional[StatusCallback] = None,
    ) -> ToolResult:
        """Run a tool by name, isolating all exceptions.

        Returns ToolResult(success=False, error=...) if:
        - tool not found
        - tool raises any exception
        - tool times out (60s hard cap)
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                name=name,
                success=False,
                error=f"tool '{name}' not registered",
            )

        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                tool.run(args, on_status=on_status),
                timeout=60.0,
            )
            result.elapsed_ms = (time.monotonic() - t0) * 1000
            return result
        except asyncio.TimeoutError:
            return ToolResult(
                name=name,
                success=False,
                error="timed out after 60s",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            logger.exception("Unhandled exception in tool '%s'", name)
            return ToolResult(
                name=name,
                success=False,
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

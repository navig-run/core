"""
Tool Router & Registry - Central dispatch for LLM-requested tool calls.

Mirrors the ChannelRegistry pattern (navig.gateway.channels.registry)
and extends the lazy-loading pattern from navig.tools.__init__.

Architecture:
    ToolRegistry  - Stores ToolMeta for every known tool + lazy handler loading.
    ToolRouter    - Accepts a ToolCallAction, resolves the handler, applies
                    safety policy, executes, and returns a ToolResult.

Usage:
    from navig.tools.router import get_tool_router

    router = get_tool_router()
    result = router.execute(action)
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

from navig.tools.schemas import (
    ToolCallAction,
    ToolResult,
    ToolResultStatus,
)

logger = logging.getLogger("navig.tools.router")


# =============================================================================
# Enums
# =============================================================================

class ToolDomain(str, Enum):
    """Domain categories for tool packs."""
    WEB = "web"
    IMAGE = "image"
    CODE = "code"
    SYSTEM = "system"
    DATA = "data"
    GENERAL = "general"


class ToolStatus(str, Enum):
    """Availability status of a tool."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    ERROR = "error"


class SafetyLevel(str, Enum):
    """Risk classification for a tool."""
    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"


# =============================================================================
# ToolMeta - metadata for a single tool
# =============================================================================

# Handler type: sync callable (**params) -> Any
ToolHandler = Callable[..., Any]


@dataclass
class ToolMeta:
    """
    Metadata descriptor for a registered tool.

    Mirrors ChannelMeta from navig.gateway.channels.registry.
    """
    name: str
    domain: ToolDomain
    description: str = ""
    safety: SafetyLevel = SafetyLevel.SAFE
    status: ToolStatus = ToolStatus.AVAILABLE
    status_message: str = ""

    # Parameter schema (for LLM prompt injection)
    parameters_schema: Dict[str, Any] = field(default_factory=dict)

    # Lazy loading
    module_path: Optional[str] = None
    handler_name: Optional[str] = None

    # Pre-loaded handler (alternative to lazy loading)
    handler: Optional[ToolHandler] = None

    # Tags for filtering
    tags: List[str] = field(default_factory=list)

    # Config requirements (env vars or config keys)
    required_config: List[str] = field(default_factory=list)

    def is_available(self) -> bool:
        """Check if tool is available for execution."""
        return self.status == ToolStatus.AVAILABLE

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for display / LLM prompt schema injection."""
        return {
            "name": self.name,
            "domain": self.domain.value,
            "description": self.description,
            "safety": self.safety.value,
            "status": self.status.value,
            "parameters": self.parameters_schema,
            "tags": self.tags,
        }


# =============================================================================
# Tool Aliases (flexible lookup)
# =============================================================================

TOOL_ALIASES: Dict[str, str] = {
    "search": "web_search",
    "google": "web_search",
    "fetch": "web_fetch",
    "browse": "web_fetch",
    "image": "image_generate",
    "img": "image_generate",
    "dalle": "image_generate",
    "sandbox": "code_sandbox",
    "exec": "code_sandbox",
    "run_code": "code_sandbox",
    "docs": "docs_search",
}


# =============================================================================
# ToolRegistry
# =============================================================================

class ToolRegistry:
    """
    Registry of all known tools, their metadata, and lazy-loaded handlers.

    Follows the singleton pattern from ChannelRegistry.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolMeta] = {}
        self._handlers: Dict[str, ToolHandler] = {}
        self._initialized: bool = False

    def initialize(self) -> None:
        """Initialize registry by loading all tool pack registrations."""
        if self._initialized:
            return
        self._load_builtin_packs()
        self._initialized = True

    def _load_builtin_packs(self) -> None:
        """Load tool metadata from builtin packs modules."""
        pack_modules = [
            "navig.tools.packs.web_pack",
            "navig.tools.packs.image_pack",
            "navig.tools.packs.code_pack",
            "navig.tools.packs.system_pack",
            "navig.tools.packs.data_pack",
            "navig.tools.packs.api_pack",
        ]
        for mod_path in pack_modules:
            try:
                mod = importlib.import_module(mod_path)
                register_fn = getattr(mod, "register_tools", None)
                if callable(register_fn):
                    register_fn(self)
                    logger.debug("Loaded tool pack: %s", mod_path)
            except ImportError as e:
                logger.debug("Tool pack %s not available: %s", mod_path, e)
            except Exception as e:
                logger.warning("Tool pack %s failed to load: %s", mod_path, e)

    # -- Registration ---------------------------------------------------------

    def register(
        self,
        meta: ToolMeta,
        handler: Optional[ToolHandler] = None,
    ) -> None:
        """
        Register a tool.

        Args:
            meta: Tool metadata descriptor.
            handler: Optional pre-created handler callable.
        """
        self._tools[meta.name] = meta
        if handler:
            self._handlers[meta.name] = handler
        elif meta.handler:
            self._handlers[meta.name] = meta.handler

    # -- Lookup ---------------------------------------------------------------

    def normalize_tool_name(self, raw: str) -> Optional[str]:
        """Normalize a tool name or alias to its canonical name."""
        key = raw.strip().lower().replace("-", "_")
        if key in TOOL_ALIASES:
            key = TOOL_ALIASES[key]
        if key in self._tools:
            return key
        return None

    def get_tool(self, name: str) -> Optional[ToolMeta]:
        """Get tool metadata by name or alias."""
        if not self._initialized:
            self.initialize()
        canonical = self.normalize_tool_name(name)
        if canonical is None:
            return None
        return self._tools.get(canonical)

    def get_handler(self, name: str) -> Optional[ToolHandler]:
        """
        Get or lazy-load the handler for a tool.

        Uses the same lazy-import pattern as navig.tools.__init__.
        """
        if not self._initialized:
            self.initialize()

        canonical = self.normalize_tool_name(name)
        if canonical is None:
            return None

        # Check cache
        if canonical in self._handlers:
            return self._handlers[canonical]

        # Lazy load
        meta = self._tools.get(canonical)
        if meta is None:
            return None

        if meta.module_path and meta.handler_name:
            try:
                mod = importlib.import_module(meta.module_path)
                handler = getattr(mod, meta.handler_name)
                self._handlers[canonical] = handler
                return handler
            except (ImportError, AttributeError) as e:
                meta.status = ToolStatus.ERROR
                meta.status_message = str(e)
                logger.warning("Failed to load handler for %s: %s", canonical, e)
                return None

        return None

    # -- Listing --------------------------------------------------------------

    def list_tools(
        self,
        available_only: bool = False,
        domain: Optional[ToolDomain] = None,
    ) -> List[ToolMeta]:
        """List registered tools with optional filters."""
        if not self._initialized:
            self.initialize()

        tools = []
        for meta in self._tools.values():
            if available_only and not meta.is_available():
                continue
            if domain and meta.domain != domain:
                continue
            tools.append(meta)

        return sorted(tools, key=lambda t: (t.domain.value, t.name))

    def list_domains(self) -> List[str]:
        """List all domains that have registered tools."""
        if not self._initialized:
            self.initialize()
        return sorted({t.domain.value for t in self._tools.values()})

    def get_tools_for_llm_prompt(self, available_only: bool = True) -> List[Dict[str, Any]]:
        """
        Return a JSON-serializable list of tool descriptors
        suitable for injection into the LLM system prompt.
        """
        return [t.to_dict() for t in self.list_tools(available_only=available_only)]

    def get_status_summary(self) -> Dict[str, Any]:
        """Summary of tool registry state."""
        if not self._initialized:
            self.initialize()
        tools = list(self._tools.values())
        return {
            "total": len(tools),
            "available": sum(1 for t in tools if t.is_available()),
            "domains": self.list_domains(),
            "tools": [t.to_dict() for t in tools],
        }


# =============================================================================
# ToolRouter - executes tool calls with safety checks
# =============================================================================

class ToolRouter:
    """
    Accepts a ToolCallAction, resolves the handler from the registry,
    applies safety policy, executes, and returns a ToolResult.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        safety_policy: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.registry = registry or get_tool_registry()
        self._policy = safety_policy or {}
        # Set of tool names blocked by policy
        self._blocked: Set[str] = set(self._policy.get("blocked_tools", []))
        self._require_confirmation: Set[str] = set(
            self._policy.get("require_confirmation", [])
        )
        self._max_calls_per_turn: int = self._policy.get("max_calls_per_turn", 10)
        # Safety mode: permissive | standard | strict
        self._safety_mode: str = self._policy.get("safety_mode", "standard")

    def execute(self, action: ToolCallAction) -> ToolResult:
        """
        Execute a single tool call.

        Pipeline:
          1. Normalize tool name
          2. Check availability + policy
          3. Safety classification
          4. Invoke handler
          5. Wrap result
        """
        t0 = time.monotonic()
        tool_name = action.tool

        # 1. Normalize
        canonical = self.registry.normalize_tool_name(tool_name)
        if canonical is None:
            return ToolResult(
                tool=tool_name,
                status=ToolResultStatus.NOT_FOUND,
                error=f"Unknown tool: {tool_name}",
            )

        # 2. Availability
        meta = self.registry.get_tool(canonical)
        if meta is None or not meta.is_available():
            msg = meta.status_message if meta else "not registered"
            return ToolResult(
                tool=canonical,
                status=ToolResultStatus.ERROR,
                error=f"Tool unavailable: {msg}",
            )

        # 3. Policy check
        if canonical in self._blocked:
            return ToolResult(
                tool=canonical,
                status=ToolResultStatus.DENIED,
                error=f"Tool '{canonical}' is blocked by safety policy",
            )

        if canonical in self._require_confirmation:
            # Signal that this tool needs human confirmation before execution.
            # Callers can route this through ApprovalManager.
            return ToolResult(
                tool=canonical,
                status=ToolResultStatus.NEEDS_CONFIRMATION,
                error=f"Tool '{canonical}' requires human confirmation",
                metadata={"action": action.parameters},
            )

        # 4. Safety guard integration (respects safety_mode)
        if self._safety_mode == "strict":
            # Strict: block DANGEROUS outright, check MODERATE for destructive
            if meta.safety == SafetyLevel.DANGEROUS:
                return ToolResult(
                    tool=canonical,
                    status=ToolResultStatus.DENIED,
                    error=f"Tool '{canonical}' is DANGEROUS (strict safety mode)",
                    metadata={"safety_mode": "strict"},
                )
            if meta.safety == SafetyLevel.MODERATE:
                from navig.safety_guard import classify_action_risk
                params_str = str(action.parameters)
                risk = classify_action_risk(params_str)
                if risk in ("destructive", "risky"):
                    return ToolResult(
                        tool=canonical,
                        status=ToolResultStatus.DENIED,
                        error=f"Tool '{canonical}' flagged as {risk} (strict safety mode)",
                        metadata={"risk": risk, "safety_mode": "strict"},
                    )
        elif self._safety_mode == "standard":
            # Standard: check DANGEROUS for destructive, log MODERATE risky
            if meta.safety == SafetyLevel.DANGEROUS:
                from navig.safety_guard import classify_action_risk
                params_str = str(action.parameters)
                risk = classify_action_risk(params_str)
                if risk == "destructive":
                    return ToolResult(
                        tool=canonical,
                        status=ToolResultStatus.DENIED,
                        error="Destructive action detected - requires human confirmation",
                        metadata={"risk": risk, "safety_mode": "standard"},
                    )
            if meta.safety == SafetyLevel.MODERATE:
                from navig.safety_guard import classify_action_risk
                params_str = str(action.parameters)
                risk = classify_action_risk(params_str)
                if risk == "destructive":
                    return ToolResult(
                        tool=canonical,
                        status=ToolResultStatus.DENIED,
                        error="Destructive action detected in MODERATE tool",
                        metadata={"risk": risk, "safety_mode": "standard"},
                    )
        else:
            # Permissive: only check DANGEROUS for destructive patterns
            if meta.safety == SafetyLevel.DANGEROUS:
                from navig.safety_guard import classify_action_risk
                params_str = str(action.parameters)
                risk = classify_action_risk(params_str)
                if risk == "destructive":
                    return ToolResult(
                        tool=canonical,
                        status=ToolResultStatus.DENIED,
                        error="Destructive action detected - requires human confirmation",
                        metadata={"risk": risk, "safety_mode": "permissive"},
                    )

        # 5. Get handler
        handler = self.registry.get_handler(canonical)
        if handler is None:
            return ToolResult(
                tool=canonical,
                status=ToolResultStatus.ERROR,
                error=f"No handler loaded for tool: {canonical}",
            )

        # 6. Execute
        try:
            output = handler(**action.parameters)
            latency = int((time.monotonic() - t0) * 1000)
            return ToolResult(
                tool=canonical,
                status=ToolResultStatus.SUCCESS,
                output=output,
                latency_ms=latency,
            )
        except TypeError as e:
            latency = int((time.monotonic() - t0) * 1000)
            return ToolResult(
                tool=canonical,
                status=ToolResultStatus.ERROR,
                error=f"Invalid parameters: {e}",
                latency_ms=latency,
            )
        except Exception as e:
            latency = int((time.monotonic() - t0) * 1000)
            logger.error("Tool %s execution failed: %s", canonical, e, exc_info=True)
            return ToolResult(
                tool=canonical,
                status=ToolResultStatus.ERROR,
                error=f"{type(e).__name__}: {e}",
                latency_ms=latency,
            )

    def execute_multi(self, actions: List[ToolCallAction]) -> List[ToolResult]:
        """Execute multiple tool calls sequentially, respecting max_calls_per_turn."""
        results = []
        for i, action in enumerate(actions):
            if i >= self._max_calls_per_turn:
                results.append(ToolResult(
                    tool=action.tool,
                    status=ToolResultStatus.DENIED,
                    error=f"Max calls per turn ({self._max_calls_per_turn}) exceeded",
                ))
                break
            results.append(self.execute(action))
        return results


# =============================================================================
# Global Singletons
# =============================================================================

_registry: Optional[ToolRegistry] = None
_router: Optional[ToolRouter] = None


def get_tool_registry() -> ToolRegistry:
    """Get the global ToolRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _registry.initialize()
    return _registry


def get_tool_router(safety_policy: Optional[Dict[str, Any]] = None) -> ToolRouter:
    """Get the global ToolRouter singleton."""
    global _router
    if _router is None:
        _router = ToolRouter(safety_policy=safety_policy)
    return _router


def reset_globals() -> None:
    """Reset singletons (for testing)."""
    global _registry, _router
    _registry = None
    _router = None
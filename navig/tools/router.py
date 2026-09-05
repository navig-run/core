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

import asyncio
import importlib
import inspect
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from navig.tools.schemas import ToolCallAction, ToolResult, ToolResultStatus

logger = logging.getLogger("navig.tools.router")


# =============================================================================
# Enums
# =============================================================================


class ToolDomain(str, Enum):
    """Domain categories for tool packs."""

    WEB = "web"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
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
    parameters_schema: dict[str, Any] = field(default_factory=dict)

    # Lazy loading
    module_path: str | None = None
    handler_name: str | None = None

    # Pre-loaded handler (alternative to lazy loading)
    handler: ToolHandler | None = None

    # Tags for filtering
    tags: list[str] = field(default_factory=list)

    # Config requirements (env vars or config keys)
    required_config: list[str] = field(default_factory=list)

    # Optional output schema (JSON Schema) for this tool's return value
    output_schema: dict[str, Any] | None = None

    def is_available(self) -> bool:
        """Check if tool is available for execution."""
        return self.status == ToolStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        """Serialize for display / LLM prompt schema injection."""
        d: dict[str, Any] = {
            "name": self.name,
            "domain": self.domain.value,
            "description": self.description,
            "safety": self.safety.value,
            "status": self.status.value,
            "parameters": self.parameters_schema,
            "tags": self.tags,
        }
        if self.output_schema is not None:
            d["output_schema"] = self.output_schema
        return d

    def to_openapi_schema(self) -> dict[str, Any]:
        """Return an OpenAPI 3.0 operation object for this tool."""
        schema: dict[str, Any] = {
            "operationId": self.name,
            "summary": self.description,
            "tags": [self.domain.value],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": self.parameters_schema or {"type": "object"},
                    }
                },
            },
        }
        if self.output_schema is not None:
            schema["responses"] = {
                "200": {
                    "description": "Successful result",
                    "content": {
                        "application/json": {
                            "schema": self.output_schema,
                        }
                    },
                }
            }
        return schema


# =============================================================================
# Tool Aliases (flexible lookup)
# =============================================================================

TOOL_ALIASES: dict[str, str] = {
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


def canonical_tool_key(raw: str) -> str:
    """The canonical FORM of a tool name — casing, hyphens and aliases, no registry.

    The single place that answers "are these two strings the same tool?", so a gate
    and whatever populates it cannot disagree. They did: `ToolRouter` stored
    `blocked_tools` / `require_confirmation` exactly as written in config, while
    `execute()` tested the *canonical* name from the registry. A policy of

        blocked_tools: ["web-fetch"]      # or the documented alias "fetch"

    therefore matched nothing and the tool RAN — verified against the real router,
    which got as far as a DNS lookup for a tool the operator had blocked.

    Deliberately does NOT consult the registry: the policy is built before tools are
    registered, so requiring registration would drop every entry and re-open the same
    hole from the other side.
    """
    key = raw.strip().lower().replace("-", "_")
    return TOOL_ALIASES.get(key, key)



# =============================================================================
# ToolRegistry
# =============================================================================


class ToolRegistry:
    """
    Registry of all known tools, their metadata, and lazy-loaded handlers.

    Follows the singleton pattern from ChannelRegistry.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolMeta] = {}
        self._handlers: dict[str, ToolHandler] = {}
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
            "navig.tools.domains.web_pack",
            "navig.tools.domains.image_pack",
            "navig.tools.domains.video_pack",
            "navig.tools.domains.audio_pack",
            "navig.tools.domains.code_pack",
            "navig.tools.domains.exec_pack",
            "navig.tools.domains.system_pack",
            "navig.tools.domains.data_pack",
            "navig.tools.domains.api_pack",
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
        handler: ToolHandler | None = None,
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

    def normalize_tool_name(self, raw: str) -> str | None:
        """Normalize a tool name or alias to its canonical name.

        ``None`` when the result names no registered tool. Callers that need the
        canonical FORM of a name for a tool that may not be registered yet — the
        safety policy, loaded before the registry — want :func:`canonical_tool_key`.
        """
        key = canonical_tool_key(raw)
        if key in self._tools:
            return key
        return None

    def get_tool(self, name: str) -> ToolMeta | None:
        """Get tool metadata by name or alias."""
        if not self._initialized:
            self.initialize()
        canonical = self.normalize_tool_name(name)
        if canonical is None:
            return None
        return self._tools.get(canonical)

    def get_handler(self, name: str) -> ToolHandler | None:
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
        domain: ToolDomain | None = None,
    ) -> list[ToolMeta]:
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

    def list_domains(self) -> list[str]:
        """List all domains that have registered tools."""
        if not self._initialized:
            self.initialize()
        return sorted({t.domain.value for t in self._tools.values()})

    def get_tools_for_llm_prompt(self, available_only: bool = True) -> list[dict[str, Any]]:
        """
        Return a JSON-serializable list of tool descriptors
        suitable for injection into the LLM system prompt.
        """
        return [t.to_dict() for t in self.list_tools(available_only=available_only)]

    def get_status_summary(self) -> dict[str, Any]:
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

    def names(self) -> list[str]:
        """Return a list of all registered tool names."""
        if not self._initialized:
            self.initialize()
        return list(self._tools.keys())

    def to_markdown_summary(
        self,
        domain: ToolDomain | None = None,
    ) -> str:
        """Return a Markdown table summarising registered tools."""
        tools = self.list_tools(domain=domain)
        if not tools:
            return "No tools registered."
        lines = [
            "| Tool | Domain | Safety | Description |",
            "|------|--------|--------|-------------|",
        ]
        for t in tools:
            lines.append(
                f"| {t.name} | {t.domain.value} | {t.safety.value} | {t.description[:60]} |"
            )
        return "\n".join(lines)

    def to_openapi_schema(self) -> dict[str, Any]:
        """Return an OpenAPI 3.0 document for all registered tools."""
        paths: dict[str, Any] = {}
        for meta in self.list_tools():
            paths[f"/tools/{meta.name}"] = {
                "post": meta.to_openapi_schema(),
            }
        return {
            "openapi": "3.0.0",
            "info": {"title": "NAVIG Tools", "version": "1.0.0"},
            "paths": paths,
        }


# =============================================================================
# ToolRouter - executes tool calls with safety checks
# =============================================================================


class _AsyncHandlerNeedsEventLoop(RuntimeError):
    """A coroutine handler was invoked from a thread that is already running a
    loop, so the synchronous router cannot drive it to completion."""


def _drive_awaitable_to_completion(canonical: str, awaitable: Any) -> Any:
    """Run an async handler's awaitable to completion for a synchronous caller.

    A handler that returns a coroutine has **not executed yet**. Handing that
    object back as the tool's `output` is a phantom success: the router reports
    SUCCESS, the coroutine is garbage-collected un-awaited, and the model is
    told the work happened. The only honest outcomes are "ran it" or "could not
    run it here" — never "SUCCESS" over an object.

    Blocking is only possible when this thread has no event loop of its own to
    starve. If one is already running, we close the coroutine (so it does not
    leak as a never-awaited warning) and raise, which the caller turns into an
    ERROR result naming `async_execute` as the fix.

    Note for handler authors: the blocking path runs on a **fresh** loop that is
    closed again on return, so a handler must not depend on loop-bound state
    surviving between calls (a module-level aiohttp session, say). Anything that
    does should be reached through `async_execute`, which uses the caller's loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result(awaitable))

    close = getattr(awaitable, "close", None)
    if callable(close):
        close()
    raise _AsyncHandlerNeedsEventLoop(
        f"Tool '{canonical}' has an async handler and execute() was called from a "
        "running event loop, which it cannot block. Use async_execute() instead."
    )


async def _await_result(awaitable: Any) -> Any:
    return await awaitable


class ToolRouter:
    """
    Accepts a ToolCallAction, resolves the handler from the registry,
    applies safety policy, executes, and returns a ToolResult.
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        safety_policy: dict[str, Any] | None = None,
    ) -> None:
        self.registry = registry or get_tool_registry()
        self._policy = safety_policy or {}
        # Tool names blocked / gated by policy, stored in CANONICAL form.
        #
        # These came straight from config as written, while `execute()` tests the
        # canonical name — so `blocked_tools: ["web-fetch"]`, or the documented alias
        # `["fetch"]`, matched nothing and the tool executed. The operator's safety
        # policy silently did not apply. Both sides now go through
        # `canonical_tool_key`, so a gate and its setter cannot disagree.
        # Filter AFTER canonicalising: a whitespace-only entry is truthy going in and
        # empty coming out, so an `if n` guard on the raw value lets "" into a set that
        # a policy dump then shows as a real entry.
        self._blocked: set[str] = {
            key
            for key in (canonical_tool_key(n) for n in self._policy.get("blocked_tools", []))
            if key
        }
        self._require_confirmation: set[str] = {
            key
            for key in (
                canonical_tool_key(n) for n in self._policy.get("require_confirmation", [])
            )
            if key
        }
        self._max_calls_per_turn: int = self._policy.get("max_calls_per_turn", 10)
        # Safety mode: permissive | standard | strict
        self._safety_mode: str = self._policy.get("safety_mode", "standard")

    def _prepare(self, action: ToolCallAction) -> ToolResult | tuple[str, ToolHandler]:
        """
        Steps 1–5 of the pipeline, shared by `execute` and `async_execute`.

        Returns the canonical name + handler when the call may proceed, or the
        ToolResult that ends it (unknown tool, unavailable, blocked, needs
        confirmation, denied by safety mode, handler failed to load).

        The two entry points MUST share this: they used to be one function, and
        the async path was a thin wrapper that awaited the sync path's output
        *after* its try/except had already returned — so an exception raised
        inside an async handler escaped `async_execute` raw instead of becoming
        `ToolResult(status=ERROR)`, and the sync path reported SUCCESS for a
        coroutine that had never run. Splitting the guards out lets each path
        own its own invoke + error handling without duplicating a policy check.
        """
        tool_name = action.tool

        # 1. Normalize
        canonical = self.registry.normalize_tool_name(tool_name)
        if canonical is None:
            from navig.tools.hooks import ToolEvent, get_hook_registry

            get_hook_registry().fire(ToolEvent.NOT_FOUND, tool=tool_name)
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

        return canonical, handler

    # -- Result wrapping (shared by both entry points) -------------------------

    @staticmethod
    def _success(canonical: str, output: Any, t0: float) -> ToolResult:
        from navig.tools.hooks import ToolEvent, get_hook_registry

        get_hook_registry().fire(ToolEvent.AFTER_EXECUTE, tool=canonical, status="success")
        return ToolResult(
            tool=canonical,
            status=ToolResultStatus.SUCCESS,
            output=output,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    @staticmethod
    def _failure(canonical: str, error: str, t0: float) -> ToolResult:
        return ToolResult(
            tool=canonical,
            status=ToolResultStatus.ERROR,
            error=error,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    def execute(self, action: ToolCallAction) -> ToolResult:
        """
        Execute a single tool call synchronously.

        Pipeline:
          1. Normalize tool name
          2. Check availability + policy
          3. Safety classification
          4. Invoke handler
          5. Wrap result

        An **async** handler is driven to completion here rather than handed
        back un-awaited: a coroutine object is work that has not happened yet,
        and returning one as `output` reported SUCCESS for a tool that never
        ran (`bash_exec`, the shell tool, is async — so the model was told its
        command succeeded while nothing executed). When this is called from a
        thread that already has a running event loop it cannot block, and says
        so instead: use `async_execute` there.
        """
        t0 = time.monotonic()
        prepared = self._prepare(action)
        if isinstance(prepared, ToolResult):
            return prepared
        canonical, handler = prepared

        from navig.tools.hooks import ToolEvent, get_hook_registry

        get_hook_registry().fire(ToolEvent.BEFORE_EXECUTE, tool=canonical)
        try:
            output = handler(**action.parameters)
            if inspect.isawaitable(output):
                output = _drive_awaitable_to_completion(canonical, output)
            return self._success(canonical, output, t0)
        except _AsyncHandlerNeedsEventLoop as e:
            return self._failure(canonical, str(e), t0)
        except TypeError as e:
            return self._failure(canonical, f"Invalid parameters: {e}", t0)
        except Exception as e:
            logger.exception("Tool %s execution failed: %s", canonical, e)
            return self._failure(canonical, f"{type(e).__name__}: {e}", t0)

    async def async_execute(self, action: ToolCallAction) -> ToolResult:
        """
        Execute a single tool call, awaiting async handlers.

        This is not a wrapper around `execute()`. It runs the same guards and
        then awaits **inside** its own try/except — an exception raised while
        the handler is running is the ordinary way a tool fails, and it has to
        come back as `ToolResult(status=ERROR)` like every other failure rather
        than propagating out of the router into the agent loop.
        """
        t0 = time.monotonic()
        prepared = self._prepare(action)
        if isinstance(prepared, ToolResult):
            return prepared
        canonical, handler = prepared

        from navig.tools.hooks import ToolEvent, get_hook_registry

        get_hook_registry().fire(ToolEvent.BEFORE_EXECUTE, tool=canonical)
        try:
            output = handler(**action.parameters)
            if inspect.isawaitable(output):
                output = await output
            return self._success(canonical, output, t0)
        except TypeError as e:
            return self._failure(canonical, f"Invalid parameters: {e}", t0)
        except Exception as e:
            logger.exception("Tool %s execution failed: %s", canonical, e)
            return self._failure(canonical, f"{type(e).__name__}: {e}", t0)

    def execute_multi(self, actions: list[ToolCallAction]) -> list[ToolResult]:
        """Execute multiple tool calls sequentially, respecting max_calls_per_turn."""
        results = []
        for i, action in enumerate(actions):
            if i >= self._max_calls_per_turn:
                results.append(
                    ToolResult(
                        tool=action.tool,
                        status=ToolResultStatus.DENIED,
                        error=f"Max calls per turn ({self._max_calls_per_turn}) exceeded",
                    )
                )
                break
            results.append(self.execute(action))
        return results


# =============================================================================
# Global Singletons
# =============================================================================

_registry: ToolRegistry | None = None
_router: ToolRouter | None = None
_registry_lock = threading.Lock()
_router_lock = threading.Lock()


def get_tool_registry() -> ToolRegistry:
    """Get the global ToolRegistry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                registry = ToolRegistry()
                registry.initialize()
                _registry = registry
    return _registry


def load_safety_policy() -> dict[str, Any]:
    """The operator's `tools:` safety policy, read from config.

    The single place that answers "what did the operator configure?", for the same reason
    `canonical_tool_key` is the single place that answers "are these the same tool?" — the
    policy previously had two readers and one of them was on a dormant path.

    Degrades to `{}` on any failure. The router sits on the dispatch path, so a config read
    that raises must not turn every tool call into a crash; an empty policy is what the
    router already did before this was wired, so the failure mode is unchanged rather than
    newly permissive.

    ⚠ Reads through `ConfigManager`, NOT `navig.core.config_loader.load_config`. That
    function takes a required `path` — it loads *a file*, it is not "the global config" —
    so the previous reader's `load_config()` raised `TypeError` on every single call and
    its bare `except` turned that into `{}`. The operator's policy therefore had no working
    reader anywhere in the tree, which is why the missing wiring above went unnoticed: even
    the one caller that asked for the policy received an empty one.
    """
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        return {
            "blocked_tools": list(cm.get("tools.blocked_tools", []) or []),
            "require_confirmation": list(cm.get("tools.require_confirmation", []) or []),
            "max_calls_per_turn": cm.get("tools.max_calls_per_turn", 10),
            "safety_mode": cm.get("tools.safety_mode", "standard"),
        }
    except Exception as exc:  # noqa: BLE001 — a config read must not break dispatch
        logger.debug("Could not load tools safety policy: %s", exc)
        return {}


def get_tool_router(safety_policy: dict[str, Any] | None = None) -> ToolRouter:
    """Get the global ToolRouter singleton.

    With no *safety_policy*, the operator's configured one is loaded — it is not left empty.
    It used to be: this is a first-caller-wins singleton, and the live caller
    (`agent/conv/executor.py`) passes nothing, so `blocked_tools` / `require_confirmation`
    were applied on **no** path a user could reach. `llm/generate.py` was the only caller
    that built a policy, and it is dormant.

    Passing *safety_policy* explicitly still wins, but only when this call is the one that
    constructs the singleton — that is inherent to a process-wide singleton. What is not
    inherent is doing it silently, which is how the original bug stayed invisible: a caller
    handed over a real policy and had every reason to believe it applied.
    """
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                policy = load_safety_policy() if safety_policy is None else safety_policy
                _router = ToolRouter(safety_policy=policy)
                return _router
    if safety_policy is not None and safety_policy != _router._policy:
        logger.warning(
            "get_tool_router() was given a safety policy but the router already exists — "
            "the policy was NOT applied. The live policy is whatever built the singleton "
            "first (normally the operator's config). Call reset_globals() first if you "
            "meant to replace it."
        )
    return _router


def reset_globals() -> None:
    """Reset singletons (for testing)."""
    global _registry, _router
    with _registry_lock:
        _registry = None
    with _router_lock:
        _router = None

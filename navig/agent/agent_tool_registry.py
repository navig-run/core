"""
navig.agent.agent_tool_registry — OpenAI function-calling tool registry.

This is a **separate** registry from ``navig.tools.registry.ToolRegistry`` (which
handles the pipeline / StatusRenderer tool system).  This registry:

1. Wraps existing :class:`~navig.tools.registry.BaseTool` instances.
2. Generates ``tools=[{"type":"function","function":{...}}]`` JSON schemas
   consumable by any OpenAI-compatible LLM API.
3. Dispatches ``tool_calls`` responses from the LLM back to ``BaseTool.run()``.
4. Supports ``check_fn`` availability gating — unavailable tools are invisible
   to the LLM (excluded from the schema list).
5. Supports ``deregister()`` for plugin runtime removal.

Usage::

    from navig.agent.agent_tool_registry import _AGENT_REGISTRY
    from navig.agent.tools import register_core_tools

    register_core_tools()
    schemas = _AGENT_REGISTRY.get_openai_schemas()  # pass to LLM tools= param
    result_str = _AGENT_REGISTRY.dispatch("bash_exec", {"command": "echo hi"})
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from navig.agent.tool_caps import cap_result
from navig.tools.registry import BaseTool, ToolResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentToolEntry:
    """Immutable descriptor for a tool registered in the agent registry.

    Attributes:
        name:      Tool identifier (matches ``BaseTool.name``).
        schema:    OpenAI function schema dict (the ``"function"`` sub-object).
        tool_ref:  Reference to the underlying :class:`BaseTool` instance.
        toolset:   Toolset group this tool belongs to (e.g. ``"core"``).
        check_fn:  Optional availability gate.  If provided and returns
                   ``False``, the tool is excluded from schema exports and
                   dispatch calls are rejected.
        vault_keys: Credential keys to inject from vault before dispatch.
                   These key names are **stripped** from the LLM-facing schema.
    """

    name: str
    schema: dict[str, Any]
    tool_ref: BaseTool
    toolset: str = "core"
    check_fn: Callable[[], bool] | None = None
    vault_keys: list[str] = field(default_factory=list)


# Helper type alias
CheckFn = Callable[[], bool]


# ─────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────


class AgentToolRegistry:
    """Registry for OpenAI function-calling agent tools.

    Thread-safe for reads; writes should on the main thread (at startup).
    """

    def __init__(self) -> None:
        self._entries: dict[str, AgentToolEntry] = {}

    # ── Registration ────────────────────────────────────────

    def register(
        self,
        tool: BaseTool,
        toolset: str = "core",
        check_fn: CheckFn | None = None,
        vault_keys: list[str] | None = None,
    ) -> None:
        """Register a :class:`BaseTool` with an auto-generated OpenAI schema.

        Args:
            tool:       The :class:`BaseTool` instance to register.
            toolset:    Toolset group name (e.g. ``"core"``, ``"devops"``).
            check_fn:   Optional availability predicate.
            vault_keys: Credential key names to inject from vault at
                        dispatch time.  These are stripped from the exported
                        schema so the LLM never sees them.
        """
        schema = _build_openai_schema(tool, vault_keys or [])
        entry = AgentToolEntry(
            name=tool.name,
            schema=schema,
            tool_ref=tool,
            toolset=toolset,
            check_fn=check_fn,
            vault_keys=vault_keys or [],
        )
        self._entries[tool.name] = entry
        logger.debug("AgentToolRegistry: registered %r (toolset=%s)", tool.name, toolset)

    def register_entry(self, entry: AgentToolEntry) -> None:
        """Register a pre-built :class:`AgentToolEntry` directly."""
        self._entries[entry.name] = entry
        logger.debug("AgentToolRegistry: registered entry %r", entry.name)

    def deregister(self, name: str) -> None:
        """Remove a tool from the registry at runtime (e.g. plugin unload).

        Args:
            name: Tool name to remove.  No-op if not registered.
        """
        removed = self._entries.pop(name, None)
        if removed:
            logger.debug("AgentToolRegistry: deregistered %r", name)

    # ── Querying ─────────────────────────────────────────────

    def get_entry(self, name: str) -> AgentToolEntry | None:
        """Look up a tool entry by name.  Returns ``None`` if not found."""
        return self._entries.get(name)

    def available_names(
        self, toolsets: list[str] | None = None
    ) -> list[str]:
        """Return sorted list of available (check_fn-passing) tool names.

        Args:
            toolsets: If given, only include tools belonging to these toolsets.

        Returns:
            Sorted list of tool names that pass their ``check_fn``.
        """
        results: list[str] = []
        for name, entry in self._entries.items():
            if toolsets is not None and entry.toolset not in toolsets:
                continue
            if _is_available(entry):
                results.append(name)
        return sorted(results)

    def get_openai_schemas(
        self,
        toolsets: list[str] | None = None,
        tool_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return OpenAI ``tools=[]`` list for the given toolsets/names.

        Args:
            toolsets:   If given, include only tools in these toolsets.
                        ``None`` means include all toolsets.
            tool_names: If given, include only tools with these names.
                        Takes precedence over *toolsets* when both are set.

        Returns:
            List of ``{"type": "function", "function": {...}}`` dicts suitable
            for passing directly to any OpenAI-compatible LLM ``tools=`` param.
        """
        schemas: list[dict[str, Any]] = []
        for name, entry in self._entries.items():
            # Toolset filter
            if toolsets is not None and entry.toolset not in toolsets:
                continue
            # Name filter
            if tool_names is not None and name not in tool_names:
                continue
            # Availability gate
            if not _is_available(entry):
                continue
            schemas.append({"type": "function", "function": entry.schema})
        return schemas

    # ── Dispatch ─────────────────────────────────────────────

    def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        vault_injector: Callable[[list[str]], dict[str, str]] | None = None,
    ) -> str:
        """Execute a tool by name and return its output as a string.

        Args:
            name:           Tool name to call.
            args:           Arguments from the LLM ``tool_call``.
            vault_injector: Optional callable that resolves vault keys to
                            credential values.  Signature:
                            ``vault_injector(keys) -> {key: value}``.

        Returns:
            String output (``ToolResult.output`` on success, or error message).
            Truncated to :data:`_MAX_OUTPUT_CHARS` as a backstop.

        Raises:
            KeyError: If *name* is not registered.
            RuntimeError: If *check_fn* returns ``False`` (tool unavailable).
        """
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"Tool {name!r} not registered in AgentToolRegistry")

        if not _is_available(entry):
            raise RuntimeError(f"Tool {name!r} is currently unavailable (check_fn returned False)")

        # Inject vault credentials (not from LLM args)
        merged_args = dict(args)
        if entry.vault_keys and vault_injector is not None:
            try:
                secrets = vault_injector(entry.vault_keys)
                merged_args.update(secrets)
            except Exception as e:
                logger.warning("Vault injection failed for tool %r: %s", name, e)

        # Execute asynchronously (sync bridge)
        result: ToolResult = _run_tool_sync(entry.tool_ref, merged_args)

        # Convert result to string
        output = _result_to_str(result)

        # Context-aware truncation with disk spillover
        output = cap_result(output, tool_name=name)

        return output

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return name in self._entries


# ─────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────

#: The default agent tool registry instance used throughout navig.
_AGENT_REGISTRY = AgentToolRegistry()


# ─────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────


def _is_available(entry: AgentToolEntry) -> bool:
    """Return True if the entry's check_fn passes (or no check_fn)."""
    if entry.check_fn is None:
        return True
    try:
        return bool(entry.check_fn())
    except Exception as e:
        logger.debug("check_fn error for tool %r: %s", entry.name, e)
        return False


def _run_tool_sync(tool: BaseTool, args: dict[str, Any]) -> ToolResult:
    """Run an async BaseTool synchronously, creating an event loop if needed."""
    async def _run() -> ToolResult:
        return await tool.run(args, on_status=None)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an async context — use a thread-based approach
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(asyncio.run, _run())
            return future.result(timeout=120)
    else:
        return asyncio.run(_run())


def _result_to_str(result: ToolResult) -> str:
    """Serialise a ToolResult to a string consumable by the LLM."""
    if result.success:
        if isinstance(result.output, str):
            return result.output
        if isinstance(result.output, (dict, list)):
            try:
                return json.dumps(result.output, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                return str(result.output)
        return str(result.output) if result.output is not None else ""
    # Failure path
    error = result.error or "Tool execution failed (unknown error)"
    return f"[ERROR] {error}"


def _build_openai_schema(tool: BaseTool, vault_keys: list[str]) -> dict[str, Any]:
    """Convert a BaseTool into an OpenAI function schema.

    Handles two parameter formats:

    1. **List format** (new): ``parameters = [{"name":..., "type":..., ...}]``
    2. **Dict format** (legacy): ``parameters = {"key": "description", ...}``
    3. **JSON Schema object** (already conformant): ``parameters = {"type":"object", ...}``

    Credential fields listed in *vault_keys* are stripped from the schema so
    the LLM never sees or provides them.

    Args:
        tool:       :class:`BaseTool` to introspect.
        vault_keys: Parameter names to exclude from the exported schema.

    Returns:
        OpenAI function schema dict with keys ``name``, ``description``,
        ``parameters``.
    """
    raw = tool.parameters

    if isinstance(raw, list):
        # List-of-param-descriptors format (used by BashExecTool, SearchTool)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in raw:
            pname = param.get("name", "")
            if not pname or pname in vault_keys:
                continue
            ptype = param.get("type", "string")
            pdesc = param.get("description", "")
            prop: dict[str, Any] = {"type": ptype}
            if pdesc:
                prop["description"] = pdesc
            # Handle enum if present
            if "enum" in param:
                prop["enum"] = param["enum"]
            properties[pname] = prop
            if param.get("required", False):
                required.append(pname)

        parameters_schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            parameters_schema["required"] = required

    elif isinstance(raw, dict) and raw.get("type") == "object":
        # Already a JSON Schema object — use as-is but strip vault_keys
        parameters_schema = dict(raw)
        if vault_keys and "properties" in parameters_schema:
            props = dict(parameters_schema["properties"])
            for vk in vault_keys:
                props.pop(vk, None)
            parameters_schema["properties"] = props
            if "required" in parameters_schema:
                parameters_schema["required"] = [
                    r for r in parameters_schema["required"] if r not in vault_keys
                ]

    elif isinstance(raw, dict):
        # Legacy dict: {"key": "description"} or {"key": {"desc": "..."}}
        properties = {}
        for pname, pdesc in raw.items():
            if pname in vault_keys:
                continue
            if isinstance(pdesc, str):
                properties[pname] = {"type": "string", "description": pdesc}
            elif isinstance(pdesc, dict):
                properties[pname] = pdesc
            else:
                properties[pname] = {"type": "string"}
        parameters_schema = {"type": "object", "properties": properties}

    else:
        # Fallback: no parameters
        parameters_schema = {"type": "object", "properties": {}}

    return {
        "name": tool.name,
        "description": tool.description or f"Execute the {tool.name} tool",
        "parameters": parameters_schema,
    }

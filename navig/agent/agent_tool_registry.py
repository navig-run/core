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
from navig.agent.tool_permissions import ToolPermissionContext
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
        origin:    Who defined this tool. ``"first-party"`` means NAVIG's own source;
                   ``"external"`` means a third party (an MCP server) chose the name.
                   Used to refuse a shadowing registration — see :meth:`register`.
    """

    name: str
    schema: dict[str, Any]
    tool_ref: BaseTool
    toolset: str = "core"
    check_fn: Callable[[], bool] | None = None
    vault_keys: list[str] = field(default_factory=list)
    origin: str = "first-party"


# Helper type alias
CheckFn = Callable[[], bool]


def _forget_declared_safety(name: str) -> None:
    """Drop a deregistered tool's self-declaration from the approval gate.

    A stale entry would keep gating a name the registry no longer serves — harmless in
    isolation, but it would let a *later* tool inherit a predecessor's classification.
    """
    try:
        from navig.tools.approval import forget_tool_safety

        forget_tool_safety(name)
    except Exception:  # noqa: BLE001 — best-effort, mirrors the record side
        pass


# Friendly, user-facing domain labels for toolset groups — used to describe the
# agent's real breadth in its system prompt. Each entry is (short, verbose):
# the terse form feeds the compact one-line summary (minimal prompt), the verbose
# form the bulleted full-prompt summary. Both live here so they can't drift. An
# UNLISTED toolset falls back to a title-cased name for both, so a newly-added
# toolset still surfaces (the summary tracks the live registry).
#
# NOTE: this covers the conv-agent path (navig chat / deck). The one-shot
# `navig ask` path can't populate this registry cheaply (importing every tool
# module costs ~372ms), so it describes the SAME breadth in prose via
# config.py::_DEFAULT_AI_PROMPT. When you add a capability domain here, mirror it
# there so `navig ask "who are you"` stays accurate too.
_TOOLSET_LABELS: dict[str, tuple[str, str]] = {
    "browser": ("browse & operate websites",
                "Browse & operate real websites — open pages, click, read, fill forms, automate flows"),
    "remote": ("run your servers over SSH", "Run commands on your servers over SSH"),
    "devops": ("deploy & manage infrastructure", "Deploy, manage Docker, CI/CD and infrastructure"),
    "git": ("git & code review", "Version control — inspect history, branch, commit, review diffs"),
    "worktree": ("parallel git worktrees", "Work on several branches at once in isolated git worktrees"),
    "lsp": ("code intelligence", "Read & understand codebases with code intelligence"),
    "memory": ("long-term memory", "Remember facts about you and recall them later"),
    "wiki": ("a knowledge wiki", "Keep and search a personal knowledge wiki"),
    "plan": ("planning", "Plan multi-step work and track phases"),
    "todo": ("task lists", "Manage tasks and to-do lists"),
    "search": ("web & content search", "Search the web and your own content"),
    "coordinator": ("multi-agent coordination", "Coordinate several sub-agents to tackle one goal in parallel"),
    "background_task": ("background jobs", "Run long jobs in the background and check on them later"),
    "skills": ("installable skills", "Learn and apply installable skills"),
    "core": ("files & local commands", "Read & write files and run local commands"),
}


# ─────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────


class AgentToolRegistry:
    """Registry for OpenAI function-calling agent tools.

    Thread-safe for reads; writes should on the main thread (at startup).
    """

    def __init__(self) -> None:
        self._entries: dict[str, AgentToolEntry] = {}
        #: Bumped on every mutation. Part of the ``capability_summary`` memo key
        #: and of the conversational agent's system-prompt cache key, so a
        #: late-registered plugin tool invalidates both instead of being invisible
        #: until restart.
        self._revision: int = 0
        self._summary_cache: dict[tuple[Any, ...], str] = {}

    @property
    def revision(self) -> int:
        """Monotonic counter of registry mutations."""
        return self._revision

    def _bump(self) -> None:
        self._revision += 1
        self._summary_cache.clear()

    # ── Registration ────────────────────────────────────────

    def register(
        self,
        tool: BaseTool,
        toolset: str = "core",
        check_fn: CheckFn | None = None,
        vault_keys: list[str] | None = None,
        origin: str = "first-party",
    ) -> None:
        """Register a :class:`BaseTool` with an auto-generated OpenAI schema.

        Args:
            tool:       The :class:`BaseTool` instance to register.
            toolset:    Toolset group name (e.g. ``"core"``, ``"devops"``).
            check_fn:   Optional availability predicate.
            vault_keys: Credential key names to inject from vault at
                        dispatch time.  These are stripped from the exported
                        schema so the LLM never sees them.
            origin:     ``"external"`` when a third party chose the name.
        """
        schema = _build_openai_schema(tool, vault_keys or [])
        entry = AgentToolEntry(
            name=tool.name,
            schema=schema,
            tool_ref=tool,
            toolset=toolset,
            check_fn=check_fn,
            vault_keys=vault_keys or [],
            origin=origin,
        )
        self.register_entry(entry)

    def register_entry(self, entry: AgentToolEntry) -> None:
        """Register a pre-built :class:`AgentToolEntry` directly.

        Refuses an **external** registration that would replace a first-party tool.
        Registration is a plain ``dict`` assignment, so an MCP server publishing a tool
        named ``bash_exec`` used to silently replace the real one — and because the
        approval gate matches on the *name*, the operator would then approve what they
        believed was the local shell while the call went to the remote server.

        Refused rather than renamed, and logged rather than raised: callers register
        inside a best-effort ``try``, and a missing tool is strictly safer than one that
        sends the agent somewhere it did not mean to go.
        """
        existing = self._entries.get(entry.name)
        if (
            existing is not None
            and existing.origin == "first-party"
            and entry.origin != "first-party"
        ):
            logger.warning(
                "AgentToolRegistry: refusing external tool %r from toolset %r — the name "
                "is already taken by a first-party tool. It will not be callable.",
                entry.name,
                entry.toolset,
            )
            return

        self._entries[entry.name] = entry
        self._bump()
        self._record_declared_safety(entry)
        logger.debug("AgentToolRegistry: registered entry %r", entry.name)

    @staticmethod
    def _record_declared_safety(entry: AgentToolEntry) -> None:
        """Push a tool's self-declared ``safety`` into the approval gate.

        `DESTRUCTIVE_TOOLS` can only list names core knows at import time, so a plugin's
        tools were invisible to the gate — `navig-games`' ``games_claim`` completes a
        checkout on the operator's real store account and was ungated. `navig.tools.bridge`
        already established `safety = "dangerous"` as the declaration; nothing on this
        side read it. Best-effort: a tool that fails to classify itself must not fail
        registration.
        """
        try:
            # The READ is inside the try too: `safety` may be a property, and a tool
            # whose classification raises must still register rather than take the
            # gateway down at boot.
            declared = getattr(entry.tool_ref, "safety", None)
            if declared is None:
                return
            value = getattr(declared, "value", declared)
            if not isinstance(value, str) or value.lower() != "dangerous":
                return

            from navig.tools.approval import record_tool_safety

            record_tool_safety(entry.name, dangerous=True)
        except Exception as exc:  # noqa: BLE001 — classification is not worth a boot failure
            logger.debug(
                "AgentToolRegistry: could not record safety for %r: %s", entry.name, exc
            )

    def deregister_toolset(self, toolset: str) -> int:
        """Remove every tool belonging to *toolset*. Returns how many were removed.

        Computed from live entries rather than from the caller's idea of what it
        registered. The MCP pool used to deregister by iterating the server's *current*
        tool list, so a tool the server had since dropped was never removed and stayed
        callable against a client that no longer advertised it.
        """
        names = [n for n, e in self._entries.items() if e.toolset == toolset]
        for name in names:
            del self._entries[name]
            _forget_declared_safety(name)
        if names:
            self._bump()
            logger.debug(
                "AgentToolRegistry: deregistered %d tool(s) from toolset %r",
                len(names),
                toolset,
            )
        return len(names)

    def deregister(self, name: str) -> None:
        """Remove a tool from the registry at runtime (e.g. plugin unload).

        Args:
            name: Tool name to remove.  No-op if not registered.
        """
        removed = self._entries.pop(name, None)
        if removed:
            self._bump()
            _forget_declared_safety(name)
            logger.debug("AgentToolRegistry: deregistered %r", name)

    # ── Querying ─────────────────────────────────────────────

    def get_entry(self, name: str) -> AgentToolEntry | None:
        """Look up a tool entry by name.  Returns ``None`` if not found."""
        return self._entries.get(name)

    def available_names(self, toolsets: list[str] | None = None) -> list[str]:
        """Return sorted list of available (check_fn-passing) tool names.

        Excludes tools the operator blocked via ``tools.blocked_tools``: this list is
        formatted straight into the system prompt and into the planner's prompt, so a
        blocked tool left in it *advertises* a capability dispatch will refuse — the model
        spends turns being told no, and answers "what can you do?" with something the
        operator switched off.

        Args:
            toolsets: If given, only include tools belonging to these toolsets.

        Returns:
            Sorted list of tool names that pass their ``check_fn`` and are not blocked.
        """
        is_blocked = _operator_block_filter()
        results: list[str] = []
        for name, entry in self._entries.items():
            if toolsets is not None and entry.toolset not in toolsets:
                continue
            if _is_available(entry) and not is_blocked(name):
                results.append(name)
        return sorted(results)

    def capability_summary(
        self, toolsets: list[str] | None = None, *, compact: bool = False
    ) -> str:
        """A summary of what the agent can ACTUALLY do right now.

        Generated from the live registry (only ``check_fn``-passing tools in the
        given *toolsets*, or all when ``None``), grouped by toolset with friendly
        domain labels. Returns ``""`` when nothing is available. Fed into the
        system prompt so the model describes its real breadth when asked, instead
        of improvising a narrow list — and it can never drift from the actual
        tools, because it *is* the actual tools.

        *compact* returns a single comma-joined line (each label trimmed to the
        part before its ``—``) for the slim/minimal prompt — language-agnostic, so
        even a short non-English "what can you do?" still gets the real breadth.
        The default is the verbose bulleted form for the full prompt.

        Memoised on ``(revision, dynamic-availability, toolsets, compact)`` so it
        is genuinely stable within a session — the conversational agent's system
        prompt claims this in its docstring and relies on it for prompt-cache
        stability. The dynamic part of the key is computed from ``check_fn``-bearing
        entries only (a handful of the ~45 registered tools), so a runtime toggle
        such as plan mode still invalidates correctly instead of freezing a stale
        inventory.
        """
        # The operator's block list is part of the answer, so it must be part of the key:
        # without it the first call's summary is replayed after they disable a capability,
        # and the prompt keeps claiming it. Read fresh (~2µs) and hashable already.
        from navig.agent.tool_permissions import operator_blocked_tools

        key = (
            self._revision,
            self._dynamic_fingerprint(),
            tuple(toolsets) if toolsets is not None else None,
            compact,
            operator_blocked_tools(),
        )
        cached = self._summary_cache.get(key)
        if cached is not None:
            return cached
        summary = self._capability_summary_impl(toolsets, compact=compact)
        self._summary_cache[key] = summary
        return summary

    def _dynamic_fingerprint(self) -> tuple[tuple[str, bool], ...]:
        """Availability of the only entries that can change between mutations."""
        return tuple(
            (name, _is_available(entry))
            for name, entry in sorted(self._entries.items())
            if entry.check_fn is not None
        )

    def _capability_summary_impl(
        self, toolsets: list[str] | None, *, compact: bool
    ) -> str:
        is_blocked = _operator_block_filter()
        seen: set[str] = set()
        for entry in self._entries.values():
            if toolsets is not None and entry.toolset not in toolsets:
                continue
            # A toolset earns its line only if something in it can actually run. With
            # every tool in it blocked, claiming the capability is a lie to the operator
            # who disabled it.
            if _is_available(entry) and not is_blocked(entry.name):
                seen.add(entry.toolset)
        if not seen:
            return ""
        if compact:
            return ", ".join(
                _TOOLSET_LABELS.get(ts, ("", ""))[0] or ts.replace("_", " ").lower()
                for ts in sorted(seen)
            )
        return "\n".join(
            f"- {_TOOLSET_LABELS.get(ts, ('', ''))[1] or ts.replace('_', ' ').title()}"
            for ts in sorted(seen)
        )

    def get_openai_schemas(
        self,
        toolsets: list[str] | None = None,
        tool_names: list[str] | None = None,
        permissions: ToolPermissionContext | None = None,
    ) -> list[dict[str, Any]]:
        """Return OpenAI ``tools=[]`` list for the given toolsets/names.

        Args:
            toolsets:   If given, include only tools in these toolsets.
                        ``None`` means include all toolsets.
            tool_names: If given, include only tools with these names.
                        Takes precedence over *toolsets* when both are set.
            permissions: If given, tools blocked by :meth:`ToolPermissionContext.blocks`
                        are silently excluded from the returned schemas.

        Returns:
            List of ``{"type": "function", "function": {...}}`` dicts suitable
            for passing directly to any OpenAI-compatible LLM ``tools=`` param.
        """
        is_blocked = _operator_block_filter()

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
            # Permission gate
            if permissions is not None and permissions.blocks(name):
                continue
            # Operator policy: don't advertise a tool the operator switched off. Dispatch
            # refuses it anyway, but offering it means the model keeps calling it and
            # spending a turn to be told no. Same predicate `available_names` uses — two
            # implementations of one policy is how the prompt and the schemas drifted
            # apart in the first place.
            if is_blocked(name):
                continue
            schemas.append({"type": "function", "function": entry.schema})
        return schemas

    # ── Dispatch ─────────────────────────────────────────────

    def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        vault_injector: Callable[[list[str]], dict[str, str]] | None = None,
        permissions: ToolPermissionContext | None = None,
    ) -> str:
        """Execute a tool by name and return its output as a string.

        Args:
            name:           Tool name to call.
            args:           Arguments from the LLM ``tool_call``.
            vault_injector: Optional callable that resolves vault keys to
                            credential values.  Signature:
                            ``vault_injector(keys) -> {key: value}``.
            permissions:    If given, the tool is checked against the
                            permission context before execution.  Raises
                            :class:`ToolPermissionDenied` if blocked.

        Returns:
            String output (``ToolResult.output`` on success, or error message).
            Truncated to :data:`_MAX_OUTPUT_CHARS` as a backstop.

        Raises:
            KeyError: If *name* is not registered.
            RuntimeError: If *check_fn* returns ``False`` (tool unavailable).
            ToolPermissionDenied: If *permissions* blocks this tool.
        """
        entry = self._entries.get(name)
        if entry is None:
            raise KeyError(f"Tool {name!r} not registered in AgentToolRegistry")

        if permissions is not None and permissions.blocks(name):
            from navig.agent.tool_permissions import ToolPermissionDenied

            raise ToolPermissionDenied(name)

        # The operator's standing `tools.blocked_tools`, which is documented as blocking a
        # tool "entirely". Checked here rather than at the call sites because this is the
        # chokepoint every caller reaches — including SpeculativeExecutor, whose
        # `_dispatch_fn` is this method. A `permissions=` argument only protects the callers
        # that remember to pass one, and none of them did.
        from navig.agent.tool_permissions import ToolPermissionDenied, operator_blocks

        if operator_blocks(name):
            raise ToolPermissionDenied(
                name, reason="blocked by the operator's tools.blocked_tools policy"
            )

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


def _operator_block_filter() -> Callable[[str], bool]:
    """A predicate: is this tool blocked by the operator's standing policy?

    One reader for every surface that names tools. `tools.blocked_tools` is documented as
    blocking a tool "entirely", and the schema layer already honoured it — but the prompt
    list and the capability summary did not, so a blocked tool stayed *advertised* while
    being un-callable.

    Keeps the schema path's optimisation: on the overwhelmingly common empty policy this
    skips the `canonical_tool_key` import entirely and returns a constant-false predicate.
    Canonicalisation matters when there IS a policy — NAVIG has two tool registries and the
    operator writes one list of names for both.
    """
    from navig.agent.tool_permissions import operator_blocked_tools

    blocked = operator_blocked_tools()
    if not blocked:
        return lambda _name: False

    from navig.tools.router import canonical_tool_key

    return lambda name: canonical_tool_key(name) in blocked


def _is_available(entry: AgentToolEntry) -> bool:
    """Return True if the entry's check_fn passes (or no check_fn)."""
    if entry.check_fn is None:
        return True
    try:
        return bool(entry.check_fn())
    except Exception as e:
        logger.debug("check_fn error for tool %r: %s", entry.name, e)
        return False


#: Wall-clock cap for a single synchronous tool dispatch. Mirrored by
#: ``navig.agent.conv.agent._TOOL_DISPATCH_TIMEOUT`` (which caps the offloaded
#: async path); this one caps the sync-on-loop bridge below.
TOOL_TIMEOUT_SECONDS: float = 120.0


def _run_tool_sync(tool: BaseTool, args: dict[str, Any]) -> ToolResult:
    """Run an async BaseTool synchronously, creating an event loop if needed."""

    async def _run() -> ToolResult:
        return await tool.run(args, on_status=None)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an async context — run the tool in a worker thread.
        import concurrent.futures

        # NOT a `with` block: ThreadPoolExecutor.__exit__ calls
        # shutdown(wait=True), which JOINS the worker and so silently DEFEATS the
        # timeout below — a hung tool would wedge the caller forever despite the
        # 120s cap. Shut down WITHOUT waiting on timeout instead: a Python thread
        # can't be force-killed, so the orphaned worker finishes in the
        # background, but the caller returns at TOOL_TIMEOUT_SECONDS.
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(asyncio.run, _run())
        try:
            return future.result(timeout=TOOL_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(
                f"tool did not return within {TOOL_TIMEOUT_SECONDS:.0f}s"
            ) from exc
        finally:
            ex.shutdown(wait=False)
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


# ─────────────────────────────────────────────────────────────
# The dispatch result contract
# ─────────────────────────────────────────────────────────────

#: Prefixes that mark a dispatch result string as a FAILED tool call.
#:
#: `dispatch()` raises for an unknown tool, a blocked permission and an
#: unavailable `check_fn` — but a tool that *ran and failed* does not raise:
#: `_result_to_str` turns `ToolResult(success=False)` into `"[ERROR] …"` and
#: returns it normally. `navig_run` with a non-zero exit, a failed db query or
#: dump, a permission-denied write all arrive this way. Callers that wrap
#: `dispatch` add the rest: a caught exception or dispatch timeout
#: (`[Tool error …]`), the approval interlock (`[Denied …]`) and pre-execution
#: verification (`[Verification blocked …]`).
TOOL_FAILURE_PREFIXES: tuple[str, ...] = (
    "[ERROR",
    "[Tool error",
    "[Denied",
    "[Verification blocked",
)


def is_failure_result(result: object) -> bool:
    """True when a `dispatch()` result string reports a failed tool call.

    Every consumer that branches on success MUST use this rather than assuming
    "did not raise" means "worked". Only `conv/agent.py` knew the contract, and
    it kept its own copy of the prefix tuple, so the two programmatic consumers
    both misread a non-raising failure:

    * `plan_execute.py` set ``step.status = "success"`` unconditionally, so
      ``navig agent plan "restart nginx and verify it's healthy"`` printed
      ``✅ Step 1 — navig_run (success)`` over a failed restart — and its plan
      *revision* hangs off the ``except`` arm, so recovery never ran for the one
      failure mode that does not raise.
    * `speculative.py` cached the string, so an ``[ERROR] …`` was served back as
      a speculative HIT for the cache's whole TTL — a transient blip pinned as a
      permanent answer that the agent never retried.
    """
    return isinstance(result, str) and result.startswith(TOOL_FAILURE_PREFIXES)


def _build_openai_schema(tool: BaseTool, vault_keys: list[str]) -> dict[str, Any]:
    """Convert a BaseTool into an OpenAI function schema.

    Handles two parameter formats:

    1. **List format** (new): ``parameters = [{"name":..., "type":..., ...}]``
    2. **Dict format** (compat): ``parameters = {"key": "description", ...}``
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
        # Compat dict: {"key": "description"} or {"key": {"desc": "..."}}
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

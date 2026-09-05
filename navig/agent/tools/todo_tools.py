"""
navig.agent.tools.todo_tools — Agent tools for the todo tracker.

Provides three tools for the LLM to manage a persistent todo list:

* ``todo_create``  — create (or replace) the todo list
* ``todo_update``  — update a single item's status
* ``todo_show``    — display the todo list with progress

These tools are registered in the ``"todo"`` toolset.

FA-03 implementation.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Shared state — set by register_todo_tools()
# ─────────────────────────────────────────────────────────────

#: Explicit override — when set, every call uses this list regardless of session.
#: Kept for embedders that own the list themselves (and for tests). Production never
#: sets it; the per-conversation store below is the live path.
_todo_list_ref: Any = None  # Will be TodoList
_persistence_ref: Any = None  # Will be TodoPersistence or None

#: One list per conversation, most-recently-used last.
#:
#: A single process-wide list is not merely untidy here — ``todo_create`` REPLACES the
#: list, so one chat would silently destroy another's plan. That is why these tools could
#: not simply be registered: the daemon serves every chat from one process, and the
#: session key only reaches a tool that declares ``needs_session``.
_LISTS: "OrderedDict[str, Any]" = OrderedDict()

#: Chats accumulate for as long as the daemon runs, so the map is capped (LRU). A todo
#: list is at most MAX_ITEMS short strings, so this is about not leaking, not about size.
MAX_TRACKED_SESSIONS: int = 64

#: Used when a caller has no session identity — the legacy agent path, or a direct call.
#: Sharing one list there matches how `browser_tool` already degrades on that path.
DEFAULT_SESSION: str = "_default"


def set_todo_list(todo_list: Any, persistence: Any = None) -> None:
    """Pin one list for every call, bypassing per-conversation resolution.

    An override for embedders that own the list; production leaves it unset.
    """
    global _todo_list_ref, _persistence_ref
    _todo_list_ref = todo_list
    _persistence_ref = persistence


def get_todo_list(session_id: str | None = None) -> Any:
    """The todo list for *session_id*, created on first use.

    Creating on demand rather than raising is what makes the tools usable at all: nothing
    in the tree calls :func:`set_todo_list`, so the old "not initialised" error was the
    only thing these tools could ever return.
    """
    if _todo_list_ref is not None:
        return _todo_list_ref

    from navig.agent.todo_tracker import TodoList

    key = session_id or DEFAULT_SESSION
    existing = _LISTS.get(key)
    if existing is not None:
        _LISTS.move_to_end(key)
        return existing

    created = TodoList(session_id=key)
    _LISTS[key] = created
    while len(_LISTS) > MAX_TRACKED_SESSIONS:
        _LISTS.popitem(last=False)  # drop the least-recently-used chat
    return created


def reset_todo_lists() -> None:
    """Forget every per-conversation list AND any pinned override.

    Both, deliberately. Clearing only `_LISTS` leaves `_todo_list_ref` pinned, and the
    override wins for every session key — so a caller that "reset" would still see one
    shared list and per-conversation isolation would be silently off. That is the same
    half-reset trap as `reset_approval_gate()` clearing its singleton but not its policy.
    """
    global _todo_list_ref, _persistence_ref
    _LISTS.clear()
    _todo_list_ref = None
    _persistence_ref = None


def _json_dumps_items(items: list[Any]) -> str:
    """Re-encode a model-supplied list so the normal string parser handles it.

    Cheaper than a second parsing path: one parser, one set of rules, no drift between
    "the model sent a JSON string" and "the model sent an actual array".
    """
    import json as _json

    try:
        return _json.dumps(items)
    except (TypeError, ValueError):
        return ",".join(str(i) for i in items)


def _session_of(args: dict[str, Any]) -> str:
    """The conversation this call belongs to.

    ``_session_id`` is injected by the agent for tools declaring ``needs_session`` and is
    deliberately absent from the tool schema, so the model can neither see nor spoof it.
    """
    return str(args.get("_session_id") or DEFAULT_SESSION)


def _replace_list(session_id: str, new_list: Any) -> None:
    """Install *new_list* as the list for *session_id* (or the override, if pinned)."""
    global _todo_list_ref
    if _todo_list_ref is not None:
        _todo_list_ref = new_list
        return
    _LISTS[session_id] = new_list
    _LISTS.move_to_end(session_id)
    while len(_LISTS) > MAX_TRACKED_SESSIONS:
        _LISTS.popitem(last=False)


def _auto_save(todo_list: Any = None) -> None:
    """Persist after mutation if a persistence backend is configured.

    Takes the list explicitly: once lists are per-conversation, reading the module global
    here would persist whichever list happened to be pinned rather than the one that just
    changed. Falls back to the override for older callers that pass nothing.
    """
    target = todo_list if todo_list is not None else _todo_list_ref
    if _persistence_ref is not None and target is not None:
        try:
            _persistence_ref.save(target)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Auto-save failed: %s", exc)


# ─────────────────────────────────────────────────────────────
# todo_create
# ─────────────────────────────────────────────────────────────


class TodoCreateTool(BaseTool):
    """Create or replace the todo list with a set of items."""

    name = "todo_create"
    needs_session = True
    description = (
        "Create a new todo list.  Provide a JSON array of item objects with 'title' "
        "(string, max 50 chars).  Replaces any existing list.  Max 15 items."
    )
    owner_only = False
    parameters = [
        {
            "name": "items",
            "type": "string",
            "description": (
                "JSON array of objects: [{\"title\": \"Read existing code\"}, ...]. "
                "Or a comma-separated list of titles."
            ),
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        import json as _json

        from navig.agent.todo_tracker import MAX_ITEMS, TodoList

        # The schema says "string", but the description asks for a JSON array — so a model
        # will sometimes send a real list or a list of dicts. `BaseTool.run` must NEVER
        # raise, and `.strip()` on a list is an AttributeError, so normalise first.
        supplied = args.get("items", "")
        if isinstance(supplied, list):
            supplied = _json_dumps_items(supplied)
        raw = str(supplied).strip()
        if not raw:
            return ToolResult(
                name=self.name,
                success=False,
                output="items is required.",
                error="missing items",
            )

        # Parse items — accept JSON array or comma-separated titles
        titles: list[str] = []
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                for entry in parsed:
                    if isinstance(entry, dict):
                        titles.append(entry.get("title", "").strip())
                    elif isinstance(entry, str):
                        titles.append(entry.strip())
            else:
                return ToolResult(
                    name=self.name,
                    success=False,
                    output="items must be a JSON array.",
                    error="invalid format",
                )
        except _json.JSONDecodeError:
            # Fall back to comma-separated
            titles = [t.strip() for t in raw.split(",") if t.strip()]

        if not titles:
            return ToolResult(
                name=self.name,
                success=False,
                output="No valid titles provided.",
                error="empty items",
            )

        if len(titles) > MAX_ITEMS:
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Too many items ({len(titles)}). Maximum is {MAX_ITEMS}.",
                error="too many items",
            )

        # Create fresh list, replacing old one
        session_id = _session_of(args)
        new_list = TodoList(session_id=session_id)
        errors: list[str] = []
        for title in titles:
            try:
                new_list.add(title)
            except ValueError as exc:
                errors.append(str(exc))

        if errors and not new_list.items:
            return ToolResult(
                name=self.name,
                success=False,
                output="All items failed: " + "; ".join(errors),
                error="validation failed",
            )

        _replace_list(session_id, new_list)
        _auto_save(new_list)

        result_lines = [f"Created todo list with {len(new_list.items)} items."]
        if errors:
            result_lines.append(f"Skipped {len(errors)} invalid items.")
        result_lines.append("")
        result_lines.append(new_list.format_display())

        return ToolResult(
            name=self.name,
            success=True,
            output="\n".join(result_lines),
        )


# ─────────────────────────────────────────────────────────────
# todo_update
# ─────────────────────────────────────────────────────────────


class TodoUpdateTool(BaseTool):
    """Update the status of a single todo item."""

    name = "todo_update"
    needs_session = True
    description = (
        "Update the status of a todo item.  Provide the item id and the new status: "
        "'not-started', 'in-progress', or 'completed'."
    )
    owner_only = False
    parameters = [
        {
            "name": "id",
            "type": "integer",
            "description": "The numeric id of the todo item to update.",
            "required": True,
        },
        {
            "name": "status",
            "type": "string",
            "description": "New status: not-started, in-progress, or completed",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.todo_tracker import TodoStatus

        todo_list = get_todo_list(_session_of(args))

        item_id = args.get("id")
        if item_id is None:
            return ToolResult(
                name=self.name,
                success=False,
                output="id is required.",
                error="missing id",
            )
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Invalid id: {item_id!r}",
                error="invalid id",
            )

        status_raw = args.get("status", "").strip().lower()
        try:
            new_status = TodoStatus(status_raw)
        except ValueError:
            valid = ", ".join(s.value for s in TodoStatus)
            return ToolResult(
                name=self.name,
                success=False,
                output=f"Invalid status: {status_raw!r}. Valid: {valid}",
                error="invalid status",
            )

        try:
            nudge = todo_list.update(item_id, new_status)
        except KeyError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                output=str(exc),
                error="not found",
            )
        except ValueError as exc:
            return ToolResult(
                name=self.name,
                success=False,
                output=str(exc),
                error="constraint violation",
            )

        _auto_save(todo_list)

        lines = [
            f"Item {item_id} → {new_status.value}",
            todo_list.get_progress(),
        ]
        if nudge:
            lines.append("")
            lines.append(f"⚠️ {nudge}")

        return ToolResult(name=self.name, success=True, output="\n".join(lines))


# ─────────────────────────────────────────────────────────────
# todo_show
# ─────────────────────────────────────────────────────────────


class TodoShowTool(BaseTool):
    """Display the current todo list with progress."""

    name = "todo_show"
    needs_session = True
    description = (
        "Show the current todo list with all items, their status, "
        "and overall progress."
    )
    owner_only = False
    parameters: list[dict[str, Any]] = []

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        todo_list = get_todo_list(_session_of(args))
        return ToolResult(
            name=self.name,
            success=True,
            output=todo_list.format_display(),
        )


# ─────────────────────────────────────────────────────────────
# Registration helper
# ─────────────────────────────────────────────────────────────


def register_todo_tools(
    todo_list: Any = None,
    persistence: Any = None,
) -> None:
    """Register todo tools in the agent registry, in the ``"todo"`` toolset.

    ``todo_list`` is optional and normally omitted: each tool resolves the list for the
    conversation it is serving. It used to be **required**, which is why these tools were
    never registered — nothing in the tree constructs a ``TodoList``, so there was no
    argument to pass, and passing one would have pinned a single list across every chat.

    Pass one only to override that (an embedder owning the list itself).
    """
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    if todo_list is not None:
        set_todo_list(todo_list, persistence)

    _AGENT_REGISTRY.register(TodoCreateTool(), toolset="todo")
    _AGENT_REGISTRY.register(TodoUpdateTool(), toolset="todo")
    _AGENT_REGISTRY.register(TodoShowTool(), toolset="todo")

    logger.debug("Todo tools registered: todo_create, todo_update, todo_show")

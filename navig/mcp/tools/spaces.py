"""Connected Spaces tools — the relations reader, provider-neutral (Phase D).

One engine, no second interpretation: every tool resolves through the same core
library the CLI and the deck endpoints use (``navig.spaces.relations`` /
``navig.spaces.linked_context``). What ``navig space related`` shows, an MCP
client sees; what ``navig space context`` counts, an MCP client counts.

Read-only by construction: this bundle never writes a manifest, a registry row
or a file — ``navig_space_read`` applies the linked-context guard (linked-or-own,
include/exclude globs, the deny list, resolved-path containment, size cap) and
refuses anything else with a reason.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

#: A single read is capped — an MCP tool result lands in a model context whole.
_MAX_READ_BYTES = 256 * 1024


def register(server: Any) -> None:
    """Register the space tools (declarations, safety classes, handlers)."""
    server.tools.update(
        {
            "navig_space_current": {
                "name": "navig_space_current",
                "description": (
                    "The active (or given) Space: id, path, phase title and relation counts. "
                    "Provenance anchor for every other space tool."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "space": {
                            "type": "string",
                            "description": "Space id, name or path (default: the active space)",
                        },
                    },
                    "required": [],
                },
            },
            "navig_space_related": {
                "name": "navig_space_related",
                "description": (
                    "A Space's logical graph — parent, links, children, inbound links, "
                    "availability (unavailable targets carry a reason, never an error) and "
                    "a parent_cycle flag. Same payload as `navig space related --json`."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "space": {
                            "type": "string",
                            "description": "Space id, name or path (default: the active space)",
                        },
                    },
                    "required": [],
                },
            },
            "navig_space_context": {
                "name": "navig_space_context",
                "description": (
                    "What an agent may read from this Space's relations: provenance-carrying "
                    "file items (space, rel, path, mtime), unavailable edges, character budget "
                    "and dedupe already applied. Same engine as `navig space context --json`."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "space": {
                            "type": "string",
                            "description": "Space id, name or path (default: the active space)",
                        },
                        "query": {
                            "type": "string",
                            "description": "Focus note — echoed back so runs stay comparable",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max items to return (default 50, cap 200)",
                            "default": 50,
                        },
                    },
                    "required": [],
                },
            },
            "navig_space_read": {
                "name": "navig_space_read",
                "description": (
                    "Read ONE file from a Space through the linked-context guard: the target "
                    "must be the active Space or reachable through its parent/link edges, the "
                    "path must match that edge's include/exclude globs, must not hit the deny "
                    "list (vault/env/credentials/sessions/state/databases/logs) and must "
                    "resolve inside the Space's own folder (junctions cannot escape). "
                    "Denied reads answer with a reason, never content."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "space": {
                            "type": "string",
                            "description": "The Space to read from (id, name or path)",
                        },
                        "path": {
                            "type": "string",
                            "description": "Path INSIDE that Space (relative preferred; containment is enforced either way)",
                        },
                    },
                    "required": ["space", "path"],
                },
            },
        }
    )

    if not hasattr(server, "_tool_safety"):
        server._tool_safety = {}
    server._tool_safety.update(
        {
            # Registry/manifest reads — nothing leaves the machine's own metadata.
            "navig_space_current": "safe",
            "navig_space_related": "safe",
            "navig_space_context": "safe",
            # File content enters the conversation: the guard is code, so the gate
            # is the second opinion (explicit entry — a missing one is a build failure).
            "navig_space_read": "moderate",
        }
    )

    server._tool_handlers.update(
        {
            "navig_space_current": _tool_space_current,
            "navig_space_related": _tool_space_related,
            "navig_space_context": _tool_space_context,
            "navig_space_read": _tool_space_read,
        }
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _err(message: str) -> dict[str, Any]:
    return {"error": message, "isError": True}


def _space_dir(space: str | None) -> Path:
    """Resolve a space reference to its folder — the CLI's own resolution rules."""
    from navig.commands.space import (
        _resolve_space_target,  # noqa: PLC0415
        get_active_space,  # noqa: PLC0415
    )

    ref = space or get_active_space()
    path = _resolve_space_target(ref)
    if path is None or not path.is_dir():
        raise ValueError(f"no such space: {ref!r} (id, name or path)")
    return path


def _own_id(space_dir: Path) -> str:
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    return load_space_manifest(space_dir).resolved_id or space_dir.name


def _phase_title(space_dir: Path) -> str | None:
    try:
        text = (space_dir / ".navig" / "plans" / "CURRENT_PHASE.md").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    for line in text.splitlines()[:12]:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:80] or None
    return None


def _matches(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


# ── Handlers ──────────────────────────────────────────────────────────────────


def _tool_space_current(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Identity + shape of one Space: where it is, what phase it's in, edges."""
    try:
        from navig.spaces.relations import load_relations  # noqa: PLC0415

        space_dir = _space_dir(args.get("space"))
        own = _own_id(space_dir)
        rel = load_relations(space_dir)
        return {
            "id": own,
            "path": str(space_dir),
            "phase": _phase_title(space_dir),
            "relations": {"parent": rel.parent, "links": [l.space for l in rel.links]},
        }
    except Exception as exc:  # noqa: BLE001 — the MCP surface never raises through
        return _err(str(exc))


def _tool_space_related(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """The shared graph payload — identical to `navig space related --json`."""
    try:
        from navig.spaces.relations import related_graph  # noqa: PLC0415

        space_dir = _space_dir(args.get("space"))
        _, payload = related_graph(space_dir, space_id=_own_id(space_dir))
        return payload
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def _tool_space_context(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """The linked-context snapshot with provenance — dedupe/budgets applied."""
    try:
        from navig.spaces.linked_context import gather_linked  # noqa: PLC0415

        space_dir = _space_dir(args.get("space"))
        own = _own_id(space_dir)
        ctx = gather_linked(space_dir, space_id=own)
        try:
            limit = max(0, min(int(args.get("limit") or 50), 200))
        except (TypeError, ValueError):
            limit = 50
        return {
            "space": own,
            "focus": args.get("query") or None,
            "parent_cycle": ctx.parent_cycle,
            "unavailable": [{"id": i, "reason": r} for i, r in ctx.unavailable],
            "chars_total": ctx.chars_total,
            "items": [
                {**item.provenance, "source": item.source, "size": item.size}
                for item in ctx.items[:limit]
            ],
            "truncated": len(ctx.items) > limit,
        }
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


def _guard_read(target: Path, rel_path: str) -> dict[str, Any] | None:
    """Authorization for ONE read against the ACTIVE space — returns an error
    payload when refused, None when allowed (the caller then reads)."""
    from navig.spaces.linked_context import DEFAULT_INCLUDE, contained, is_denied  # noqa: PLC0415
    from navig.spaces.relations import load_relations, registered_spaces, resolve  # noqa: PLC0415

    active_dir = _space_dir(None)
    active_id = _own_id(active_dir)
    target_id = _own_id(target)

    include, exclude = DEFAULT_INCLUDE, ()
    if target_id != active_id:
        rel = load_relations(active_dir)
        reg = registered_spaces()
        resolved = resolve(rel, self_id=active_id, registered=reg)
        edge = None
        if resolved.parent is not None and resolved.parent.target_id == target_id:
            edge = resolved.parent
        else:
            edge = next((r for r in resolved.links if r.target_id == target_id), None)
        if edge is None or not edge.available:
            return _err(
                f"'{target_id}' is not linked from the active space "
                f"({active_id}) — authorize it first: navig space link {active_id} {target_id}"
            )
        include = edge.link.include or DEFAULT_INCLUDE
        exclude = edge.link.exclude

    norm = PurePosixPath(rel_path.replace("\\", "/")).as_posix().lstrip("/")
    if not norm or norm == ".":
        return _err("path is required (relative to the space folder)")
    if is_denied(norm):
        return _err(f"denied by the space deny-list: {norm}")
    if not _matches(norm, include):
        return _err(f"outside this edge's include globs: {norm}")
    if exclude and _matches(norm, exclude):
        return _err(f"excluded by this edge: {norm}")

    full = (target / norm).resolve()
    if not contained(full, target):
        return _err(f"path escapes the space folder (junction/symlink?): {rel_path}")
    if not full.is_file():
        return _err(f"no such file in {target_id}: {norm}")
    return None


def _tool_space_read(server: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Guarded single-file read — see the tool description for the contract."""
    try:
        target = _space_dir(args.get("space"))
        rel_path = str(args.get("path") or "").strip()
        if not rel_path:
            return _err("'path' is required")

        refused = _guard_read(target, rel_path)
        if refused is not None:
            return refused

        full = (target / rel_path.replace("\\", "/")).resolve()
        size = full.stat().st_size
        if size > _MAX_READ_BYTES:
            return _err(f"file too large to read whole ({size} bytes > {_MAX_READ_BYTES}) — open it in the workspace instead")
        norm = full.relative_to(target.resolve()).as_posix()
        content = full.read_text(encoding="utf-8", errors="replace")
        return {
            "space": _own_id(target),
            "rel": norm,
            "path": str(full),
            "size": size,
            "mtime": full.stat().st_mtime,
            "content": content,
        }
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

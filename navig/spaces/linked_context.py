"""Linked context — what a Space may *read* from its relations. Read-only by construction.

Assembly order for a Space's context (Phase C):

1. the Space's own ``NAVIG.md`` (handled by the caller — it is not "linked"),
2. ``PlanContext.gather(active)`` (the caller again),
3. this module's :func:`gather_linked` — the parent's authorized parts, then each
   linked Space's ``include``-matched files, on demand.

Invariants:

* **No write API exists here.** The module cannot create, modify or delete any
  file — cross-Space writes are out of scope for v1 and the type system shows it.
* **Containment.** Every candidate is ``Path.resolve()``-ed and must stay
  ``is_relative_to`` the owning Space's resolved root — a junction or symlink
  inside a Space that points elsewhere (another Space, a system path) is rejected
  even though it was *found* under the root.
* **Default deny.** Regardless of ``include`` globs: vaults, env files,
  credential-shaped names, sessions/state stores, databases, raw logs and
  finance/medical/contacts folders never leave their Space.
* **Budgets.** Per-source and total character caps, per-Space item caps; within a
  Space the freshest files (mtime desc) win the budget, duplicates (same content
  hash) are dropped across Spaces.
* **Provenance.** Every item carries ``{space, path, rel, mtime, size}`` so a
  reader can say exactly where a fact came from.

Known limitation (documented, not solved): Claude Code itself has unrestricted
filesystem access — the enforcement point is the *tools* that consult this module
(``navig space context``, the MCP reader), not the OS.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from navig.spaces.relations import Relations, load_relations, registered_spaces, resolve

logger = logging.getLogger(__name__)

#: Directory-segment names that never leave their Space (matched on any path segment).
DENY_SEGMENTS = frozenset(
    {
        "vault",
        "sessions",
        "state",
        "finance",
        "medical",
        "contacts",
    }
)

#: Basename patterns that never leave their Space (``*`` crosses ``/``, fnmatch rules).
DENY_BASENAMES = (
    ".env*",
    "*credentials*",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.log",
    "secrets.*",
)

#: When a link carries no ``include`` globs, this default keeps the edge useful
#: (knowledge surfaces) without swinging the whole folder open. Deny still applies.
DEFAULT_INCLUDE = (
    "*.md",
    ".navig/wiki/**",
    ".navig/plans/**",
    "README*",
)


@dataclass(frozen=True)
class ContextBudget:
    """Caps for one assembly run — generous for humans, bounded for an agent prompt."""

    max_chars_per_source: int = 8_000
    max_total_chars: int = 60_000
    max_items_per_space: int = 50


@dataclass(frozen=True)
class ContextItem:
    """One authorized file view, with full provenance."""

    space: str
    path: Path
    rel: str
    mtime: float
    size: int
    source: str = "link"  # "parent" | "link" — which edge granted access
    excerpt: str | None = None
    content_sha1: str = ""

    @property
    def provenance(self) -> dict[str, object]:
        return {"space": self.space, "path": str(self.path), "rel": self.rel, "mtime": self.mtime}


@dataclass
class LinkedContext:
    """The assembled cross-Space view for one Space."""

    space: str
    items: list[ContextItem] = field(default_factory=list)
    unavailable: list[tuple[str, str]] = field(default_factory=list)  # (id, reason)
    parent_cycle: bool = False
    chars_total: int = 0


# ---------------------------------------------------------------------------
# Path policy
# ---------------------------------------------------------------------------


def is_denied(rel: str) -> bool:
    """True when a Space-relative path must never be shared — evaluated on every reader."""
    parts = PurePosixPath(rel).parts
    if any(p.lower() in DENY_SEGMENTS for p in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    return any(fnmatch.fnmatch(name.lower(), pat) for pat in DENY_BASENAMES)


def _matches(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def contained(path: Path, root: Path) -> bool:
    """Resolved containment — junctions/symlinks that escape ``root`` return False."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Candidate collection (one Space)
# ---------------------------------------------------------------------------


def _content_sha1(path: Path, limit: int) -> str | None:
    """Short content hash for cross-Space dedupe; None when unreadable or oversized."""
    try:
        if path.stat().st_size > limit:
            digest = hashlib.sha1()
            with path.open("rb") as fh:
                digest.update(fh.read(limit))
            return digest.hexdigest()[:16] + "-partial"
        return hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def collect_candidates(
    space_id: str,
    root: Path,
    *,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    source: str = "link",
    budget: ContextBudget | None = None,
) -> list[ContextItem]:
    """Walk ``root`` applying include → exclude → deny → containment, freshest first.

    Does not read file *content* into items (excerpts are the reader's job); only
    metadata plus a short hash — the budget is applied by :func:`gather_linked`.
    """
    budget = budget or ContextBudget()
    include = include or DEFAULT_INCLUDE
    if not root.is_dir():
        return []
    out: list[ContextItem] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Prune denied segments eagerly (vault/, .navig/state/ …) — never descend.
        dirnames[:] = [
            d
            for d in dirnames
            if d.lower() not in DENY_SEGMENTS
            and not any(fnmatch.fnmatch(d.lower(), pat) for pat in DENY_BASENAMES)
        ]
        for name in filenames:
            p = here / name
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                continue
            if is_denied(rel):
                continue
            if not _matches(rel, include):
                continue
            if exclude and _matches(rel, exclude):
                continue
            if not contained(p, root):  # a junction escape — found here, lives elsewhere
                logger.debug("linked-context: rejected escaping path %s", p)
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            digest = _content_sha1(p, budget.max_chars_per_source) or ""
            out.append(
                ContextItem(
                    space=space_id, path=p, rel=rel, mtime=st.st_mtime, size=st.st_size, source=source, content_sha1=digest
                )
            )
    out.sort(key=lambda item: item.mtime, reverse=True)  # freshness wins the budget
    return out[: budget.max_items_per_space]


# ---------------------------------------------------------------------------
# Assembly (parent + links of one Space)
# ---------------------------------------------------------------------------


def gather_linked(
    space_dir: Path,
    *,
    space_id: str | None = None,
    relations: Relations | None = None,
    budget: ContextBudget | None = None,
    include_unavailable: bool = True,
) -> LinkedContext:
    """Assemble the cross-Space view for ``space_dir``: parent first, then links.

    Unavailable targets (unknown id, missing path) contribute a reason instead of
    raising; a parent cycle is flagged, and the cycle itself never repeats.
    """
    budget = budget or ContextBudget()
    sid = space_id or _own_id(space_dir)
    rel = relations if relations is not None else load_relations(space_dir)
    reg = registered_spaces()
    resolved = resolve(rel, self_id=sid, registered=reg)
    ctx = LinkedContext(space=sid, parent_cycle=resolved.parent_cycle)

    seen_hashes: dict[str, str] = {}
    total = 0

    def _absorb(items: list[ContextItem]) -> None:
        nonlocal total
        for item in items:
            if total >= budget.max_total_chars:
                return
            if item.content_sha1 and item.content_sha1 in seen_hashes:
                continue  # same bytes already in context from another Space
            if item.content_sha1:
                seen_hashes[item.content_sha1] = item.space
            # Charge the file's text size against budgets (the excerpt read is the
            # consumer's; metadata here bounds the worst case honestly).
            charge = min(item.size, budget.max_chars_per_source)
            if total + charge > budget.max_total_chars:
                return
            ctx.items.append(item)
            total += charge

    ordered: list[tuple[str, Path, tuple[str, ...], tuple[str, ...], str]] = []
    if resolved.parent is not None and resolved.parent.available and resolved.parent.target_path:
        ordered.append(
            (resolved.parent.target_id, resolved.parent.target_path, (), (), "parent")  # parent: default include
        )
    else:
        if resolved.parent is not None and not resolved.parent.available and include_unavailable:
            ctx.unavailable.append((resolved.parent.target_id, resolved.parent.reason or "unavailable"))
    for r in resolved.links:
        if r.available and r.target_path:
            ordered.append((r.target_id, r.target_path, r.link.include, r.link.exclude, "link"))
        elif include_unavailable:
            ctx.unavailable.append((r.target_id, r.reason or "unavailable"))

    for tid, path, include, exclude, source in ordered:
        _absorb(collect_candidates(tid, path, include=include, exclude=exclude, source=source, budget=budget))

    ctx.chars_total = total
    return ctx


def _own_id(space_dir: Path) -> str:
    from navig.spaces.space_manifest import (
        load_space_manifest,  # noqa: PLC0415 — keep import cost off the hot path
    )

    return load_space_manifest(space_dir).resolved_id or space_dir.name

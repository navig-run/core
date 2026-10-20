"""Cross-Space relations — logical parent/links, metadata only.

Every Space keeps its own files, plans and identity: relations live in that Space's
own manifest (``<space>/.navig/space.json``) and describe *logical* edges — never a
physical move, never a new folder. Two fields (both additive):

* ``relations.parent`` — a stable registry id ("this Space hangs under that one").
* ``relations.links``  — ``[{space, access, include, exclude, profile}]``: explicitly
  authorized read edges into other Spaces (the globs are applied by the reader,
  :mod:`navig.spaces.linked_context`).

Plus a top-level ``collections`` list — UI grouping only, no behaviour.

Design rules (Phase C of the Connected Spaces plan):

* **Resolve by stable id.** Paths move; ids survive re-registration. A link whose id
  is unknown or whose path is gone resolves to ``available=False`` — a deleted Space
  never breaks the Space that links to it, and reads never raise.
* **Write-time validation, read-time tolerance.** ``link``/``set_parent`` refuse
  obvious mistakes (self-links, unregistered targets, parent cycles) with
  ``ValueError``; ``load_relations`` treats a malformed block as "no relations"
  (a failed read must never become a destructive write, and a broken manifest must
  not take down the session that merely *reads* it).
* **Read-only by construction.** This module writes manifests only — it has no API
  that touches another Space's files. Cross-Space writes are out of scope.
* **v1 access is always ``read``.** The field exists so a later policy can widen it;
  anything else at write time is a ``ValueError`` today.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.spaces.space_manifest import load_space_manifest, set_manifest_field

logger = logging.getLogger(__name__)

#: The only cross-Space access v1 grants. Future modes widen this tuple, never a
#: silent per-link escape: an unknown access value is refused at write time.
ALLOWED_ACCESS = ("read",)

#: Parent-chain walk cap — a chain longer than this on a healthy install is either a
#: cycle or corrupt data; both are handled by the ``seen`` set, this is belt-and-braces.
_MAX_CHAIN = 32


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpaceLink:
    """One authorized read edge from this Space into another."""

    space: str
    access: str = "read"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"space": self.space, "access": self.access}
        if self.include:
            d["include"] = list(self.include)
        if self.exclude:
            d["exclude"] = list(self.exclude)
        if self.profile:
            d["profile"] = self.profile
        return d


@dataclass(frozen=True)
class Relations:
    """The ``relations`` block of one manifest, normalized."""

    parent: str | None = None
    links: tuple[SpaceLink, ...] = ()

    @property
    def empty(self) -> bool:
        return self.parent is None and not self.links

    def to_dict(self) -> dict[str, Any] | None:
        """The manifest value to write — ``None`` deletes the field when empty."""
        if self.empty:
            return None
        d: dict[str, Any] = {}
        if self.parent:
            d["parent"] = self.parent
        if self.links:
            d["links"] = [lnk.to_dict() for lnk in self.links]
        return d


@dataclass(frozen=True)
class ResolvedLink:
    """A link with its target resolved against the registry — never an exception."""

    link: SpaceLink
    target_id: str
    target_path: Path | None = None
    available: bool = False
    reason: str | None = None  # "unknown-id" | "missing-path" | None


@dataclass(frozen=True)
class ResolvedRelations:
    """What a reader (CLI, gateway, linked_context) sees for one Space."""

    parent: ResolvedLink | None = None
    links: tuple[ResolvedLink, ...] = ()
    parent_cycle: bool = False
    unresolved: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Manifest read / write
# ---------------------------------------------------------------------------


def _link_from_dict(d: Any) -> SpaceLink | None:
    """Tolerant link parser — a malformed entry reads as absent, never raises."""
    if not isinstance(d, dict):
        return None
    space = d.get("space")
    if not isinstance(space, str) or not space.strip():
        return None
    access = d.get("access", "read")
    if access not in ALLOWED_ACCESS:
        return None
    include = d.get("include") or []
    exclude = d.get("exclude") or []
    if not isinstance(include, list) or not isinstance(exclude, list):
        return None
    profile = d.get("profile")
    return SpaceLink(
        space=space.strip(),
        access=access,
        include=tuple(str(g) for g in include),
        exclude=tuple(str(g) for g in exclude),
        profile=profile if isinstance(profile, str) and profile else None,
    )


def load_relations(space_dir: Path) -> Relations:
    """Read ``relations`` from a Space's manifest — malformed → empty, never a raise."""
    raw = load_space_manifest(space_dir).get("relations")
    if not isinstance(raw, dict):
        return Relations()
    parent = raw.get("parent")
    parent_id = parent.strip() if isinstance(parent, str) and parent.strip() else None
    raw_links = raw.get("links")
    links: list[SpaceLink] = []
    seen: set[str] = set()
    if isinstance(raw_links, list):
        for item in raw_links:
            lnk = _link_from_dict(item)
            if lnk is not None and lnk.space not in seen:
                seen.add(lnk.space)
                links.append(lnk)
    return Relations(parent=parent_id, links=tuple(links))


def save_relations(space_dir: Path, rel: Relations, *, id_hint: str | None = None) -> Path | None:
    """Persist ``relations`` through the one safe manifest writer.

    Empty relations delete the key (additive schema stays clean). Returns the
    written manifest path, or ``None`` when nothing was written because the block
    was already absent.
    """
    value = rel.to_dict()
    current = load_space_manifest(space_dir).get("relations")
    if value is None and current is None:
        return None
    return set_manifest_field(space_dir, "relations", value, id_hint=id_hint)


def load_collections(space_dir: Path) -> tuple[str, ...]:
    """UI grouping names — tolerant read, like every other relations reader."""
    raw = load_space_manifest(space_dir).get("collections")
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return tuple(out)


def save_collections(space_dir: Path, names: tuple[str, ...], *, id_hint: str | None = None) -> Path | None:
    """Persist grouping names; an empty list deletes the key."""
    clean = tuple(dict.fromkeys(n.strip() for n in names if isinstance(n, str) and n.strip()))
    current = load_space_manifest(space_dir).get("collections")
    if not clean and current is None:
        return None
    return set_manifest_field(space_dir, "collections", list(clean) if clean else None, id_hint=id_hint)


# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------


def registered_spaces() -> dict[str, Path]:
    """Live registry rows as ``id -> path``.

    First row wins on a duplicate id (matching discovery, which keeps the first and
    warns): relations must resolve the same way the rest of the system sees the id.
    Forgotten paths are not rows, so they are naturally absent.
    """
    from navig.spaces import (
        registry as _registry,  # noqa: PLC0415 — keep import cost off the hot path
    )

    out: dict[str, Path] = {}
    for row in _registry.load_registry().get("spaces", []):
        sid = row.get("id")
        path = row.get("path")
        if isinstance(sid, str) and isinstance(path, str) and sid and sid not in out:
            out[sid] = Path(path)
    return out


def resolve(
    rel: Relations,
    *,
    self_id: str | None = None,
    registered: dict[str, Path] | None = None,
) -> ResolvedRelations:
    """Attach target paths to a Relations block — missing targets go unavailable.

    ``parent_cycle`` is True when walking up from ``parent`` (transitively) reaches
    ``self_id`` again: corrupt data that was written by hand or by an older build is
    *reported*, never repaired silently and never raised into a read path.
    """
    reg = registered if registered is not None else registered_spaces()

    def _one(lnk: SpaceLink) -> ResolvedLink:
        path = reg.get(lnk.space)
        if path is None:
            return ResolvedLink(link=lnk, target_id=lnk.space, available=False, reason="unknown-id")
        if not path.is_dir():
            return ResolvedLink(link=lnk, target_id=lnk.space, target_path=path, available=False, reason="missing-path")
        return ResolvedLink(link=lnk, target_id=lnk.space, target_path=path, available=True)

    parent_res = _one(SpaceLink(space=rel.parent)) if rel.parent else None
    resolved_links = tuple(_one(lnk) for lnk in rel.links)
    unresolved = tuple(
        r.target_id for r in ((parent_res,) + resolved_links) if r is not None and not r.available
    )

    cycle = False
    if self_id and rel.parent:
        cycle = _chain_reaches(reg, start_id=rel.parent, want_id=self_id)

    return ResolvedRelations(
        parent=parent_res,
        links=resolved_links,
        parent_cycle=cycle,
        unresolved=unresolved,
    )


def _chain_reaches(reg: dict[str, Path], *, start_id: str, want_id: str, max_depth: int = _MAX_CHAIN) -> bool:
    """Walk parent ids from ``start_id``; True if ``want_id`` appears (cycle evidence).

    A pre-existing upstream cycle (not involving ``want_id``) stops on ``seen`` —
    this walk never spins, and read-time ``parent_cycle`` covers the self-reachable
    case that matters to the reading Space.
    """
    seen: set[str] = set()
    cur: str | None = start_id
    for _ in range(max_depth):
        if cur is None or cur in seen:
            return False
        if cur == want_id:
            return True
        seen.add(cur)
        path = reg.get(cur)
        if path is None:
            return False
        cur = load_relations(path).parent
    return False


# ---------------------------------------------------------------------------
# Write-time operations (the CLI / gateway mutate through these)
# ---------------------------------------------------------------------------


def _require_registered(reg: dict[str, Path], target_id: str, what: str) -> Path:
    path = reg.get(target_id)
    if path is None:
        known = ", ".join(sorted(reg)[:8]) or "none"
        raise ValueError(f"{what} '{target_id}' is not a registered space (known ids: {known}…)")
    return path


def _self_id(space_dir: Path) -> str | None:
    return load_space_manifest(space_dir).resolved_id


def link(
    space_dir: Path,
    target_id: str,
    *,
    access: str = "read",
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    profile: str | None = None,
    id_hint: str | None = None,
) -> Relations:
    """Add a read edge to ``target_id``. Refuses self-links, duplicates, bad access
    and unregistered targets — every mistake that is obvious at write time."""
    if access not in ALLOWED_ACCESS:
        raise ValueError(f"access must be one of {', '.join(ALLOWED_ACCESS)} — got '{access}'")
    reg = registered_spaces()
    _require_registered(reg, target_id, "link target")
    current = load_relations(space_dir)
    own = _self_id(space_dir) or id_hint
    if own and target_id == own:
        raise ValueError(f"space '{own}' cannot link to itself")
    if any(lnk.space == target_id for lnk in current.links):
        return current  # idempotent: linking twice is a no-op, not an error
    updated = Relations(
        parent=current.parent,
        links=current.links
        + (SpaceLink(space=target_id, access=access, include=include, exclude=exclude, profile=profile),),
    )
    save_relations(space_dir, updated, id_hint=id_hint or own)
    return updated


def unlink(space_dir: Path, target_id: str) -> Relations:
    """Remove every edge to ``target_id``; absent → unchanged (idempotent)."""
    current = load_relations(space_dir)
    updated = Relations(parent=current.parent, links=tuple(l for l in current.links if l.space != target_id))
    if updated.links != current.links:
        save_relations(space_dir, updated)
    return updated


def related_graph(
    space_dir: Path, *, space_id: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parent, links, children and inbound links from ONE registry scan.

    The shared graph reader behind ``navig space related`` and
    ``GET /api/deck/spaces/{id}/relations`` — one engine, so the CLI table and the
    deck payload can never disagree. Returns ``(rows, payload)``: rows are
    display-ready dicts (``relation``/``space``/``mark``/``detail``), payload is
    the machine shape (ids + availability + flags). Unavailable targets carry a
    reason instead of raising; ``parent_cycle`` is flagged, never followed.
    """
    sid = space_id or _self_id(space_dir) or space_dir.name
    reg = registered_spaces()
    rel = load_relations(space_dir)
    resolved = resolve(rel, self_id=sid, registered=reg)

    def _status(r: ResolvedLink | None) -> tuple[str, str]:
        if r is None:
            return "—", ""
        if r.available:
            return "●", str(r.target_path) if r.target_path else ""
        return "○", r.reason or "unavailable"

    rows: list[dict[str, Any]] = []
    if resolved.parent is not None:
        mark, detail = _status(resolved.parent)
        rows.append(
            {"relation": "parent", "space": resolved.parent.target_id, "mark": mark, "detail": detail}
        )
    for r in resolved.links:
        mark, detail = _status(r)
        spec = detail if mark == "○" else ""
        if r.link.include:
            spec = "include: " + ", ".join(r.link.include)
        if r.link.exclude:
            spec = (spec + "  " if spec else "") + "exclude: " + ", ".join(r.link.exclude)
        if r.link.profile:
            spec = (spec + "  " if spec else "") + f"profile: {r.link.profile}"
        rows.append({"relation": "link", "space": r.target_id, "mark": mark, "detail": spec})

    children: list[str] = []
    incoming: list[str] = []
    for other_id, path in sorted(reg.items()):
        if path == space_dir or not path.is_dir():
            continue
        other = load_relations(path)
        if other.parent == sid:
            children.append(other_id)
            rows.append({"relation": "child", "space": other_id, "mark": "●", "detail": str(path)})
        if any(lnk.space == sid for lnk in other.links):
            incoming.append(other_id)
            rows.append({"relation": "linked by", "space": other_id, "mark": "●", "detail": str(path)})

    payload: dict[str, Any] = {
        "id": sid,
        "parent": (
            {
                "id": resolved.parent.target_id,
                "available": resolved.parent.available,
                "reason": resolved.parent.reason,
            }
            if resolved.parent
            else None
        ),
        "links": [
            {
                "id": r.target_id,
                "available": r.available,
                "reason": r.reason,
                "include": list(r.link.include),
                "exclude": list(r.link.exclude),
                "profile": r.link.profile,
            }
            for r in resolved.links
        ],
        "children": children,
        "incoming_links": incoming,
        "parent_cycle": resolved.parent_cycle,
        "unresolved": list(resolved.unresolved),
    }
    return rows, payload


def set_parent(space_dir: Path, target_id: str | None, *, id_hint: str | None = None) -> Relations:
    """Set (or with ``target_id=None`` clear) the logical parent.

    Refuses self-parenting and any parent that would create a cycle through this
    Space — the walk starts at the *proposed* parent, so ``a -> b`` is rejected
    when ``b``'s chain already reaches ``a``.
    """
    current = load_relations(space_dir)
    if target_id is None:
        if current.parent is None:
            return current
        updated = Relations(parent=None, links=current.links)
        save_relations(space_dir, updated, id_hint=id_hint)
        return updated

    reg = registered_spaces()
    _require_registered(reg, target_id, "parent")
    own = _self_id(space_dir) or id_hint
    if own and target_id == own:
        raise ValueError(f"space '{own}' cannot be its own parent")
    if own and _chain_reaches(reg, start_id=target_id, want_id=own):
        raise ValueError(f"parent '{target_id}' would create a cycle: its chain already reaches '{own}'")
    updated = Relations(parent=target_id, links=current.links)
    save_relations(space_dir, updated, id_hint=id_hint or own)
    return updated

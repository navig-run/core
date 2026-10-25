"""Environment navigation aliases — the areas/ + projects/ link reconciler.

Declarative source of truth: the Environment manifest's ``navigation`` block maps
**alias names** to **registered ids** (Area ids / Project-Manager ids — the same
identity space), never to raw canonical paths:

    "navigation": {"areas": {"android": "android"}, "projects": {"navig": "navig"}}

The filesystem is derived from that declaration: ``<env>/areas/<alias>`` and
``<env>/projects/<alias>`` become **junctions** (Windows, no elevation — chosen by
the existing :mod:`navig.spaces.media_links` primitives, which also give us
reparse-aware broken-link detection) or POSIX directory symlinks. Nothing is moved
or copied: application repositories stay on their canonical directories, Areas
keep their canonical folders, and aliases are navigation only — discovery is
top-level-only, so these nested links can never register as a Space, Environment,
Area, Project or workspace.

Safety contract (mission Phase 3 §3/§6):

* **Idempotent plan/reconcile** — ``plan()`` is the read-only diagnosis the doctor
  shows; a second ``reconcile(fix=True)`` changes nothing.
* **Never overwrite content** — a real directory or file at an alias path is a
  *collision*: reported, refused, untouched.
* **Only links are repaired** — broken or wrong-target *reparse points* under the
  managed ``areas/``/``projects/`` dirs may be re-pointed (they are this surface's
  own wiring); user-created files there are never deleted.
* **Cycle protection** — a target equal to, containing, or contained by the
  Environment root is refused (self-links and loops cannot be created).
* **Rollback of exactly what we created** — creations append to
  ``.navig/navigation-links.rollback.json``; :func:`rollback` removes only those
  recorded links (still links), never anything else.
* **Tombstoned Spaces are never revived** — resolution goes through the live
  registry rows only (forgotten paths are not rows).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from navig.spaces.media_links import is_link, make_link, read_link, remove_link

#: Managed subdirectories of an Environment root (the ONLY places this module writes).
LINK_DIRS: tuple[str, ...] = ("areas", "projects")

ROLLBACK_FILE = ".navig/navigation-links.rollback.json"


class NavigationError(RuntimeError):
    """Reconciliation refused (collision, cycle, unresolved id) — never partial by surprise."""


# ── Declaration ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NavigationSpec:
    """The manifest's intended aliases: kind → {alias: registered id}."""

    areas: dict[str, str] = field(default_factory=dict)
    projects: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.areas and not self.projects


def load_navigation(space_dir: Path) -> NavigationSpec:
    """Read ``navigation`` from the manifest — malformed → empty (tolerant, like every reader)."""
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    raw = load_space_manifest(space_dir).get("navigation")
    if not isinstance(raw, dict):
        return NavigationSpec()

    def _pair(key: str) -> dict[str, str]:
        block = raw.get(key)
        if not isinstance(block, dict):
            return {}
        return {
            str(a).strip(): str(i).strip()
            for a, i in block.items()
            if isinstance(a, str) and isinstance(i, str) and a.strip() and i.strip()
        }

    return NavigationSpec(areas=_pair("areas"), projects=_pair("projects"))


def resolve_alias(alias: str, registered_id: str) -> tuple[Path | None, str | None]:
    """alias + registered id → (canonical target path, error reason).

    Resolution uses the LIVE registry only — forgotten/tombstoned paths are absent
    from it, so an alias to one answers ``reason='unregistered-id'`` and never
    revives it.
    """
    from navig.spaces.relations import registered_spaces  # noqa: PLC0415

    path = registered_spaces().get(registered_id)
    if path is None:
        return None, "unregistered-id"
    if not path.is_dir():
        return None, "target-missing"
    return path, None


# ── Read-only diagnosis ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class LinkState:
    alias: str
    kind: str  # "areas" | "projects"
    path: Path  # the alias location (…/areas/<alias>)
    target: Path | None  # canonical target, when resolvable
    state: str  # linked | missing | broken | wrong-target | collision | unresolved | unmanaged
    detail: str = ""
    link_target: Path | None = None  # where a present link currently points

    @property
    def healthy(self) -> bool:
        return self.state == "linked"


def _same_path(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return False
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def plan(space_dir: Path, spec: NavigationSpec | None = None) -> list[LinkState]:
    """Desired-vs-actual for every declared alias — READ-ONLY (the doctor's default)."""
    spec = spec if spec is not None else load_navigation(space_dir)
    out: list[LinkState] = []
    for kind in LINK_DIRS:
        aliases = spec.areas if kind == "areas" else spec.projects
        base = space_dir / kind
        for alias, reg_id in aliases.items():
            path = base / alias
            target, err = resolve_alias(alias, reg_id)
            if err is not None:
                out.append(LinkState(alias, kind, path, None, "unresolved", f"{reg_id}: {err}"))
                continue
            # cycle / self rules before anything else
            try:
                env_r, tgt_r = space_dir.resolve(), target.resolve()
                if env_r == tgt_r or env_r.is_relative_to(tgt_r) or tgt_r.is_relative_to(env_r):
                    out.append(LinkState(alias, kind, path, target, "unresolved",
                                        "cycle: target is (inside) the Environment root"))
                    continue
            except OSError:
                pass
            if not path.exists() and not is_link(path):
                out.append(LinkState(alias, kind, path, target, "missing", "alias not created yet"))
                continue
            if is_link(path):
                current = read_link(path)
                if current is None or not Path(current).is_dir():
                    out.append(LinkState(alias, kind, path, target, "broken",
                                        f"link target gone: {current}"))
                    continue
                if not _same_path(current, target):
                    out.append(LinkState(alias, kind, path, target, "wrong-target",
                                        f"points at {current}", link_target=current))
                    continue
                out.append(LinkState(alias, kind, path, target, "linked", str(target),
                                     link_target=current))
                continue
            # a real dir or file occupies the alias — collision, never overwrite
            kind_txt = "directory" if path.is_dir() else "file"
            out.append(LinkState(alias, kind, path, target, "collision",
                                 f"existing {kind_txt} at alias path (never overwritten)"))
    # anything else sitting in the managed dirs (user content — reported, never touched)
    for kind in LINK_DIRS:
        base = space_dir / kind
        if not base.is_dir():
            continue
        declared = set(spec.areas if kind == "areas" else spec.projects)
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for child in entries:
            if child.name not in declared:
                out.append(LinkState(child.name, kind, child, None, "unmanaged",
                                     "user content in a managed dir — left alone"))
    return out


# ── Reconcile (fix) + rollback ────────────────────────────────────────────────


def _rollback_path(space_dir: Path) -> Path:
    return space_dir / ROLLBACK_FILE


def _load_rollback(space_dir: Path) -> list[dict]:
    try:
        raw = json.loads(_rollback_path(space_dir).read_text(encoding="utf-8"))
        return [x for x in raw if isinstance(x, dict) and "path" in x] if isinstance(raw, list) else []
    except (OSError, ValueError):
        return []


def _save_rollback(space_dir: Path, records: list[dict]) -> None:
    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    p = _rollback_path(space_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(records, indent=2) + "\n")


@dataclass
class ReconcileReport:
    states: list[LinkState] = field(default_factory=list)
    created: list[Path] = field(default_factory=list)
    repaired: list[Path] = field(default_factory=list)
    refused: list[LinkState] = field(default_factory=list)
    unchanged: int = 0
    changed: bool = False

    def to_dict(self) -> dict:
        return {
            "created": [str(p) for p in self.created],
            "repaired": [str(p) for p in self.repaired],
            "refused": [{"alias": s.alias, "state": s.state, "detail": s.detail}
                        for s in self.refused],
            "unchanged": self.unchanged,
            "changed": self.changed,
            "states": [{"alias": s.alias, "kind": s.kind, "state": s.state,
                        "detail": s.detail, "path": str(s.path),
                        "target": str(s.target) if s.target else None} for s in self.states],
        }


def reconcile(space_dir: Path, spec: NavigationSpec | None = None, *, fix: bool = False) -> ReconcileReport:
    """Bring the alias dirs in line with the declaration (or just report).

    ``fix=False`` → diagnosis only (identical to :func:`plan`, wrapped for callers
    that want one call shape). ``fix=True`` creates missing links, repairs
    broken/wrong-target LINKS under the managed dirs, refuses collisions and
    unresolved/cycle targets, and records every link it created for rollback.
    Nothing outside ``areas/``+``projects/`` and no real directory is ever written.
    """
    states = plan(space_dir, spec)
    rep = ReconcileReport(states=states)
    if not fix:
        rep.refused = [s for s in states if s.state in ("collision", "unresolved")]
        rep.unchanged = sum(1 for s in states if s.state in ("linked", "unmanaged"))
        return rep

    created_records = _load_rollback(space_dir)
    known = {(r.get("path"), r.get("target")) for r in created_records}

    for s in states:
        if s.state == "missing":
            if s.target is None:  # unreachable (plan already resolved), belt-and-braces
                rep.refused.append(s)
                continue
            s.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                make_link(s.path, s.target)  # refuses existing (FileExistsError) — never overwrites
            except (FileExistsError, FileNotFoundError, OSError) as exc:
                rep.refused.append(LinkState(s.alias, s.kind, s.path, s.target,
                                             "collision", str(exc)))
                continue
            rep.created.append(s.path)
            rep.changed = True
            rec = {"path": str(s.path), "target": str(s.target)}
            if (rec["path"], rec["target"]) not in known:
                created_records.append(rec)
        elif s.state in ("broken", "wrong-target") and is_link(s.path):
            # Managed reparse points only (we are inside LINK_DIRS by construction).
            try:
                remove_link(s.path)  # unlinks the point, never the target
                make_link(s.path, s.target)
            except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
                rep.refused.append(LinkState(s.alias, s.kind, s.path, s.target,
                                             "collision", str(exc)))
                continue
            rep.repaired.append(s.path)
            rep.changed = True
            rec = {"path": str(s.path), "target": str(s.target)}
            if (rec["path"], rec["target"]) not in known:
                created_records.append(rec)
        elif s.state in ("linked", "unmanaged"):
            rep.unchanged += 1
        else:  # collision | unresolved — never touched
            rep.refused.append(s)

    if rep.changed:
        _save_rollback(space_dir, created_records)
    return rep


def rollback(space_dir: Path) -> list[Path]:
    """Remove EXACTLY the links this module created (from the rollback record).

    Only paths still present *as links* are removed — a link the operator replaced
    with a real directory stays. Never touches targets.
    """
    removed: list[Path] = []
    remaining: list[dict] = []
    for rec in _load_rollback(space_dir):
        p = Path(rec.get("path", ""))
        if p.is_symlink() or is_link(p):
            try:
                remove_link(p)
                removed.append(p)
            except (OSError, ValueError):
                remaining.append(rec)  # still ours but removal failed — keep for retry
        # a path that no longer exists, or is now a real dir/file: drop the record
    _save_rollback(space_dir, remaining)
    return removed

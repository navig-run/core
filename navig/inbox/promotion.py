"""
navig.inbox.promotion — "upgrade" an inbox item between plan tiers.

Promotion is the user-facing "move this idea up the roadmap" action. It is
**never destructive**: it appends a summarized bullet to the active space's (or
project's) plan file and drops a searchable record into the wiki, then logs the
decision. Nothing is deleted.

Tiers (low → high)::

    archive  <  plan/deferred  <  plan/after-mvp  <  hub/tasks  <  plan/roadmap

``promote(ref, to_tier=...)`` writes to:
    plan/roadmap   → <plans>/ROADMAP.md      under "## Roadmap"
    plan/deferred  → <plans>/DEV_PLAN.md      under "## Deferred / Later"
    plan/after-mvp → <plans>/DEV_PLAN.md      under "## After MVP"

Plan-file resolution is **space-aware**: the active space's directory wins when it
exists, otherwise the project's ``.navig/plans/`` (ROADMAP also prefers a project
root ``ROADMAP.md``).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.inbox.promotion")

TIERS = ["archive", "plan/deferred", "plan/after-mvp", "hub/tasks", "plan/roadmap"]

# tier → (plan filename, section heading, wiki destination)
_TIER_TARGET: dict[str, tuple[str, str, str]] = {
    "plan/roadmap": ("ROADMAP.md", "## Roadmap", ".navig/wiki/hub/roadmap"),
    "plan/deferred": ("DEV_PLAN.md", "## Deferred / Later", ".navig/wiki/hub/roadmap/deferred"),
    "plan/after-mvp": ("DEV_PLAN.md", "## After MVP", ".navig/wiki/hub/roadmap/after-mvp"),
}

# Friendly aliases accepted on the CLI / API.
_TIER_ALIASES = {
    "roadmap": "plan/roadmap",
    "deferred": "plan/deferred",
    "later": "plan/deferred",
    "after-mvp": "plan/after-mvp",
    "aftermvp": "plan/after-mvp",
    "after_mvp": "plan/after-mvp",
}


def normalize_tier(to_tier: str) -> str:
    t = (to_tier or "").strip().lower()
    return _TIER_ALIASES.get(t, t)


def promote(
    ref: str | int | Path,
    *,
    to_tier: str,
    space: str | None = None,
    summary: str | None = None,
    project_root: Path | None = None,
    store: Any | None = None,
) -> dict:
    """Promote an inbox item (by id, ``path:``-ref, or Path) to *to_tier*.

    Returns a structured result; never raises for normal failures.
    """
    tier = normalize_tier(to_tier)
    if tier not in _TIER_TARGET:
        return {"ok": False, "error": f"unknown tier {to_tier!r} (use roadmap|deferred|after-mvp)"}

    root = Path(project_root) if project_root else _find_project_root()
    src_path, event = _resolve_ref(ref, store)

    if not summary:
        summary = _derive_summary(src_path)
    summary = (summary or "untitled item").strip().splitlines()[0][:200]

    fname, section, wiki_rel = _TIER_TARGET[tier]
    plan_file = _resolve_plan_file(fname, space, root)

    origin = f"  _(promoted from inbox: {src_path.name})_" if src_path else ""
    bullet = f"- [ ] {summary}{origin}"

    try:
        _append_bullet(plan_file, section, bullet)
        plan_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("promote: plan append failed (%s): %s", plan_file, exc)
        plan_ok = False

    wiki_record = _write_wiki_record(root / wiki_rel, summary, tier, src_path)

    # Persist a decision so promotion history is queryable (never overwrites).
    if store is not None and event is not None and getattr(event, "id", None):
        try:
            from navig.inbox.store import RoutingDecision

            store.insert_decision(
                RoutingDecision(
                    event_id=event.id,
                    category=tier,
                    confidence=1.0,
                    mode="copy",
                    destination=str(plan_file),
                    executed=plan_ok,
                    result_path=str(plan_file),
                    classifier="promote",
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("promote: decision log failed: %s", exc)

    return {
        "ok": plan_ok,
        "to_tier": tier,
        "section": section,
        "plan_file": str(plan_file),
        "wiki_record": str(wiki_record) if wiki_record else None,
        "summary": summary,
        "bullet": bullet,
        "source": str(src_path) if src_path else None,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _resolve_ref(ref: str | int | Path, store: Any | None) -> tuple[Path | None, Any | None]:
    if isinstance(ref, Path):
        return ref, None
    if isinstance(ref, int):
        return _event_to_path(ref, store)
    s = str(ref)
    if s.startswith("path:"):
        return Path(s[5:]), None
    if s.isdigit():
        return _event_to_path(int(s), store)
    return Path(s), None


def _event_to_path(eid: int, store: Any | None) -> tuple[Path | None, Any | None]:
    try:
        if store is None:
            from navig.inbox.store import InboxStore

            store = InboxStore()
        ev = store.get_event(eid)
        return (Path(ev.source_path), ev) if ev else (None, None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_event_to_path(%s) failed: %s", eid, exc)
        return None, None


def _derive_summary(src_path: Path | None) -> str:
    if src_path is None or not src_path.exists():
        return src_path.stem.replace("-", " ").replace("_", " ") if src_path else "untitled item"
    try:
        from navig.inbox.extract_hook import content_for_classify

        text = content_for_classify(src_path, full=True)
    except Exception:  # noqa: BLE001
        text = src_path.stem
    lines = text.splitlines()
    # Prefer the first H1 heading as the title.
    for line in lines:
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()[:200]
    # Else the first real prose line (skip frontmatter + blank + heading lines).
    in_frontmatter = False
    for line in lines:
        s = line.strip()
        if s == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter or not s or s.startswith("#"):
            continue
        return s[:200]
    return src_path.stem.replace("-", " ").replace("_", " ")


def _resolve_plan_file(fname: str, space: str | None, project_root: Path) -> Path:
    """Space-aware: active space dir if it exists, else project .navig/plans."""
    if space:
        try:
            from navig.spaces.resolver import resolve_space

            sc = resolve_space(space)
            if sc.path.exists():
                return sc.path / fname
        except Exception as exc:  # noqa: BLE001
            logger.debug("space resolve failed for %s: %s", space, exc)

    if fname == "ROADMAP.md":
        root_roadmap = project_root / "ROADMAP.md"
        if root_roadmap.exists():
            return root_roadmap
    return project_root / ".navig" / "plans" / fname


def _append_bullet(plan_file: Path, section: str, bullet: str) -> None:
    from navig.commands.plans import _insert_under_section

    plan_file.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if plan_file.exists():
        existing = plan_file.read_text(encoding="utf-8")
    else:
        existing = f"# {plan_file.stem.replace('_', ' ').title()}\n"
    updated = _insert_under_section(existing, section, bullet)

    fd, tmp = tempfile.mkstemp(dir=str(plan_file.parent), suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_text(updated, encoding="utf-8")
        os.replace(str(tmp_path), str(plan_file))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_wiki_record(dest_dir: Path, summary: str, tier: str, src_path: Path | None) -> Path | None:
    """Drop a small searchable markdown record so promotions show in wiki/context."""
    try:
        from navig.core.yaml_io import atomic_write_text

        dest_dir.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in summary.lower())[:48].strip("-")
        dest = dest_dir / f"{slug or 'item'}.md"
        if dest.exists():
            return dest  # idempotent — don't duplicate
        origin = f"\n\nOriginal: `{src_path.name}`" if src_path else ""
        atomic_write_text(
            dest,
            f"---\ntier: {tier}\npromoted: true\n---\n\n# {summary}\n{origin}\n",
        )
        return dest
    except Exception as exc:  # noqa: BLE001
        logger.debug("wiki record write failed: %s", exc)
        return None


def _find_project_root() -> Path:
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".navig").is_dir():
            return parent
    return cwd

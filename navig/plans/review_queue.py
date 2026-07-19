"""
navig.plans.review_queue — Manage ``.md.review`` items awaiting human decision.

Items land in ``.md.review`` state when the reconciliation pipeline cannot
make a confident decision (duplicates, conflicts, pipeline exceptions).

Human operators use this module to:

* List items in review.
* Inspect individual items.
* Commit an item (triggers re-reconciliation; if it fails again → stays review).
* Archive an item (renames ``.md.review`` → ``.md.archive``).

Slice B3 (forge consolidation — the archived navig-inbox extension's Review
Queue + Sandbox) adds the full human decision loop over ``.navig/inbox``:

* :meth:`ReviewQueue.approve_item` — route the item's content into a confined
  ``.navig/`` target (wiki / docs / plans) and mark the source ``.md.done``.
* :meth:`ReviewQueue.reject_item` — park the item as ``.md.archive``.
* :meth:`ReviewQueue.requeue_item` — undo: rename any state back to active ``.md``.
* :meth:`ReviewQueue.latest_decisions` — audit trail (who went where) read from
  the same ``staging/reconciliation_queue.json`` JSONL the processor appends to.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from navig.plans.frontmatter import parse_frontmatter_with_body as _parse_frontmatter
from navig.plans.inbox_reader import SuffixState

logger = logging.getLogger(__name__)

# Named route destinations (the archived extension's wiki / docs / plans
# buttons), resolved relative to ``.navig/``. Raw relative dirs are also
# accepted by :meth:`ReviewQueue.resolve_target_dir` — always confined.
NAMED_TARGETS: dict[str, str] = {
    "wiki": "wiki/knowledge",
    "docs": "wiki/technical",
    "plans": "plans/tasks/active",
}

_STATE_SUFFIX: dict[SuffixState, str] = {
    "active": "",
    "done": ".done",
    "archive": ".archive",
    "review": ".review",
}


@dataclass(frozen=True)
class ReviewItem:
    """A single item in the review queue."""

    path: Path
    """Absolute path to the ``.md.review`` file."""

    name: str
    """Canonical base name (e.g. ``my_task.md``)."""

    title: str
    """Title from frontmatter, or derived from filename."""

    frontmatter: dict[str, str]
    """Parsed frontmatter key/values."""

    body: str
    """Content after frontmatter."""

    reason: str
    """Why the item was routed to review (from frontmatter or empty)."""


def _canonical_name(filename: str) -> str:
    """Strip lifecycle suffixes from a filename."""
    lower = filename.lower()
    for suffix in (".done", ".archive", ".review"):
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return filename


class ReviewQueue:
    """Manage items in ``.md.review`` state across plans directories.

    Scans:
    - ``.navig/inbox/`` — review items from inbox processing
    - ``.navig/plans/tasks/review/`` — review items from task routing

    Parameters
    ----------
    root:
        Project root directory containing ``.navig/``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._navig_dir = self._root / ".navig"

    def _review_dirs(self) -> list[Path]:
        """Return directories to scan for ``.md.review`` files."""
        candidates = [
            self._navig_dir / "inbox",
            self._navig_dir / "plans" / "tasks" / "review",
        ]
        return [d for d in candidates if d.is_dir()]

    def list_items(self) -> list[ReviewItem]:
        """Return all items currently in review state.

        Returns
        -------
        list[ReviewItem]
            Sorted by filename for deterministic order.
        """
        items: list[ReviewItem] = []
        for directory in self._review_dirs():
            for entry in sorted(directory.iterdir()):
                if not entry.is_file():
                    continue
                if not entry.name.lower().endswith(".md.review"):
                    continue
                item = self._read_review_item(entry)
                if item is not None:
                    items.append(item)
        return items

    def get_item_detail(self, filename: str) -> ReviewItem | None:
        """Get a single review item by filename.

        Searches all review directories for the named file.
        """
        for directory in self._review_dirs():
            path = directory / filename
            if path.is_file():
                return self._read_review_item(path)
        return None

    def commit_item(self, filename: str) -> bool:
        """Re-reconcile a review item. On failure it stays ``.md.review``.

        The item is renamed from ``.md.review`` → ``.md`` (active) and
        returned to the inbox for re-processing.  If the rename fails,
        the item stays in review.

        Parameters
        ----------
        filename:
            Name of the ``.md.review`` file.

        Returns
        -------
        bool
            ``True`` if the item was successfully committed back to active.
        """
        for directory in self._review_dirs():
            source = directory / filename
            if not source.is_file():
                continue

            base = _canonical_name(filename)
            target = self._navig_dir / "inbox" / base

            try:
                # Move back to inbox as active .md
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                logger.debug("Committed review item %s → %s", source, target)
                return True
            except OSError:
                logger.exception("Failed to commit review item %s", filename)
                return False

        logger.debug("Review item %s not found in any review directory", filename)
        return False

    def archive_item(self, filename: str) -> bool:
        """Archive a review item: rename ``.md.review`` → ``.md.archive``.

        Parameters
        ----------
        filename:
            Name of the ``.md.review`` file.

        Returns
        -------
        bool
            ``True`` if successfully archived.
        """
        for directory in self._review_dirs():
            source = directory / filename
            if not source.is_file():
                continue

            archive_name = filename.replace(".md.review", ".md.archive")
            target = source.parent / archive_name

            try:
                source.rename(target)
                logger.debug("Archived review item %s → %s", source, target)
                return True
            except OSError:
                logger.exception("Failed to archive review item %s", filename)
                return False

        logger.debug("Review item %s not found", filename)
        return False

    # ── Slice B3 — the full decision loop over .navig/inbox ──────────────────

    @property
    def inbox_dir(self) -> Path:
        """Absolute path to the ``.navig/inbox`` directory."""
        return self._navig_dir / "inbox"

    def resolve_item(self, filename: str) -> Path | None:
        """Resolve a bare inbox filename — confined to ``.navig/inbox``.

        Returns ``None`` for anything that is not a plain existing filename
        (path separators, traversal, hidden files, missing file).
        """
        name = (filename or "").strip()
        if (
            not name
            or name.startswith(".")
            or "/" in name
            or "\\" in name
            or Path(name).name != name
        ):
            return None
        path = self.inbox_dir / name
        return path if path.is_file() else None

    def set_state(self, filename: str, state: SuffixState) -> Path | None:
        """Rename an inbox item into a lifecycle state; return the new path.

        Collisions (e.g. an older ``x.md.done`` already present) are resolved
        by uniquifying the canonical stem — nothing is ever overwritten.
        """
        if state not in _STATE_SUFFIX:
            return None
        source = self.resolve_item(filename)
        if source is None:
            return None
        base = _canonical_name(source.name)
        target = source.parent / (base + _STATE_SUFFIX[state])
        if target == source:
            return source
        if target.exists():
            stem, dot, ext = base.partition(".")
            i = 1
            while target.exists():
                target = source.parent / (f"{stem}_{i}{dot}{ext}" + _STATE_SUFFIX[state])
                i += 1
        try:
            source.rename(target)
        except OSError:
            logger.exception("State transition failed for %s → %s", filename, state)
            return None
        return target

    def resolve_target_dir(self, target: str) -> Path | None:
        """A named target (wiki/docs/plans) or a ``.navig``-relative dir — confined.

        Returns the absolute destination directory, or ``None`` when the value
        is empty, absolute, or escapes ``.navig/`` (traversal).
        """
        rel = NAMED_TARGETS.get((target or "").strip().lower(), (target or "").strip())
        rel = rel.replace("\\", "/").rstrip("/")
        if not rel or rel.startswith("/") or ":" in rel:
            return None
        p = Path(rel)
        if p.is_absolute() or p.drive:
            return None
        base = self._navig_dir.resolve()
        try:
            resolved = (base / p).resolve()
        except OSError:
            return None
        if not resolved.is_relative_to(base) or resolved == base:
            return None
        return resolved

    def approve_item(self, filename: str, target: str | None = None) -> dict[str, object] | None:
        """Approve: route the item's content to a target and mark it ``.md.done``.

        The content (frontmatter + body, byte-preserved) is COPIED into
        ``.navig/<target>/<canonical name>`` (unique-renamed on collision);
        the inbox source is renamed to ``.md.done`` so history survives.

        Parameters
        ----------
        filename:
            Bare inbox filename (any lifecycle state).
        target:
            A named destination (``wiki`` / ``docs`` / ``plans``) or a
            ``.navig``-relative directory. ``None`` = the keyword router's
            proposal for this item.

        Returns
        -------
        dict | None
            ``{name, state, routed_to, target_dir}`` on success; ``None`` when
            the item does not exist.

        Raises
        ------
        ValueError
            When *target* is invalid (absolute, traversal, empty resolution).
        """
        source = self.resolve_item(filename)
        if source is None:
            return None
        from navig.plans.inbox_reader import InboxReader

        item = InboxReader(self._root).read_item(source.name)
        if item is None:
            return None

        if target is None or not str(target).strip():
            from navig.plans.inbox_processor import Router

            target = Router().route(item)
        dest_dir = self.resolve_target_dir(str(target))
        if dest_dir is None:
            raise ValueError(
                f"invalid target {target!r} — use wiki/docs/plans or a path inside .navig/"
            )

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = self._unique_path(dest_dir, _canonical_name(source.name))
        dest.write_text(item.content, encoding="utf-8")

        new_path = self.set_state(source.name, "done")
        if new_path is None:  # routed copy exists; report but do not lose it
            logger.error("Approved %s but could not mark it .done", filename)

        base = self._navig_dir.resolve()
        routed_rel = dest.relative_to(base).as_posix()
        target_rel = dest_dir.relative_to(base).as_posix()
        name = _canonical_name(source.name)
        self._append_audit(name, "approved", routed_rel, f"Approved → {routed_rel}")
        return {
            "name": name,
            "state": "approved",
            "routed_to": routed_rel,
            "target_dir": target_rel,
        }

    def reject_item(self, filename: str) -> dict[str, object] | None:
        """Reject: park the item as ``.md.archive`` (never deleted)."""
        source = self.resolve_item(filename)
        if source is None:
            return None
        name = _canonical_name(source.name)
        if self.set_state(source.name, "archive") is None:
            return None
        self._append_audit(name, "rejected", "archive", "Rejected — parked as .md.archive")
        return {"name": name, "state": "rejected"}

    def requeue_item(self, filename: str) -> dict[str, object] | None:
        """Undo: return an item (any state) to the active ``.md`` queue."""
        source = self.resolve_item(filename)
        if source is None:
            return None
        name = _canonical_name(source.name)
        if self.set_state(source.name, "active") is None:
            return None
        self._append_audit(name, "requeued", "inbox", "Requeued — back to pending")
        return {"name": name, "state": "pending"}

    def latest_decisions(self) -> dict[str, dict[str, object]]:
        """Latest audit-trail line per canonical item name.

        Reads the processor's ``staging/reconciliation_queue.json`` JSON Lines
        file; malformed lines are skipped. Later lines win.
        """
        queue = self._navig_dir / "staging" / "reconciliation_queue.json"
        out: dict[str, dict[str, object]] = {}
        if not queue.is_file():
            return out
        try:
            lines = queue.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return out
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            item = data.get("item") if isinstance(data, dict) else None
            if isinstance(item, str) and item:
                out[item] = data
        return out

    def _append_audit(self, name: str, decision: str, target: str, reason: str) -> None:
        """Append a decision line to the shared reconciliation queue (JSONL)."""
        staging = self._navig_dir / "staging"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {
                    "item": name,
                    "decision": decision,
                    "target": target,
                    "reason": reason,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            with open(staging / "reconciliation_queue.json", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            logger.debug("Failed to append review audit line for %s", name)

    @staticmethod
    def _unique_path(dest_dir: Path, filename: str) -> Path:
        """A non-colliding path in *dest_dir* for *filename*."""
        target = dest_dir / filename
        if not target.exists():
            return target
        stem, suffix = target.stem, target.suffix
        i = 1
        while True:
            cand = dest_dir / f"{stem}_{i}{suffix}"
            if not cand.exists():
                return cand
            i += 1

    def _read_review_item(self, path: Path) -> ReviewItem | None:
        """Parse a single ``.md.review`` file into a ReviewItem."""
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None

        frontmatter, body = _parse_frontmatter(content)

        title = frontmatter.get("title", "")
        if not title:
            title = (
                _canonical_name(path.name)
                .replace(".md", "")
                .replace("_", " ")
                .replace("-", " ")
            )

        return ReviewItem(
            path=path.resolve(),
            name=_canonical_name(path.name),
            title=title,
            frontmatter=frontmatter,
            body=body,
            reason=frontmatter.get("review_reason", ""),
        )

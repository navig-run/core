"""A project's documents — what the hub may list, read and edit.

Real projects keep their docs in many shapes (measured over C:\\projects\\apps and
\\store): canonical ``.navig/plans/{VISION,ROADMAP,CURRENT_PHASE,DEV_PLAN,SPEC}.md``,
but also ``TASKS.md`` / ``BLOCKERS.md`` / ``DECISIONS.md``, numbered
``00-MASTER-ROADMAP.md``, lowercase ``mvp_plan.md``, ``tasks/T-0NN-*.md``,
``briefs/``, ``.navig/vision.md`` and ``ai_system_prompt.txt`` — and global spaces
keep a flat ``plans/`` at their root. The old plans routes saw only
``.navig/plans/``, so most of that was invisible.

The reachable set is:

* every ``.md``/``.txt`` under ``.navig/`` **except** the deny-listed directories —
  ``vault``, ``credentials`` and friends hold secrets, and ``cache``/``history``/
  ``sessions`` hold machine output nobody edits;
* ``plans/**`` at the project root (the flat space layout);
* the root agent files in :data:`ROOT_ALLOWLIST`.

:func:`resolve_doc` is the one gate every read and write goes through — it is the
same containment rule as the listing, so a path that is not listed cannot be
read either, whatever the client sends.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from navig.projects import textio

logger = logging.getLogger(__name__)

__all__ = [
    "DENY_DIRS",
    "DocError",
    "ROOT_ALLOWLIST",
    "index_docs",
    "read_doc",
    "resolve_doc",
    "save_doc",
]

#: Directory names never listed, read or written — at any depth under `.navig/`.
DENY_DIRS: frozenset[str] = frozenset({
    "vault", "credentials", "secrets", "keys", "cache", "history", "backups", "backup",
    "sessions", "state", "store", "logs", "tmp", "node_modules", ".git", ".rejected", ".staging",
    "worktrees", "runtime",
})
#: Root-level files the hub may show (agent instructions + the README).
ROOT_ALLOWLIST: tuple[str, ...] = ("README.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md", "NAVIG.md")
_EXTS = (".md", ".txt")
_MAX_DOCS = 600
_MAX_INBOX = 60
_NUMBERED = re.compile(r"^(\d{1,3})[-_ ]")
_MAX_DEPTH = 6
_MAX_READ_BYTES = 2 * 1024 * 1024

#: Canonical plan order (Forge planningDocReader priority), matched on the stem,
#: case-insensitively — `vision.md` and `VISION.md` are the same document.
CANONICAL_ORDER: tuple[str, ...] = (
    "VISION", "ROADMAP", "CURRENT_PHASE", "TASKS", "DEV_PLAN", "SPEC", "MASTER_PLAN",
    "BLOCKERS", "DECISIONS", "CHANGELOG",
)


class DocError(Exception):
    """A user-reportable doc failure carrying an HTTP-ish status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _canonical_rank(stem: str) -> int | None:
    up = stem.upper().replace("-", "_")
    return CANONICAL_ORDER.index(up) if up in CANONICAL_ORDER else None


def _group(rel: str) -> str:
    parts = rel.split("/")
    lower = [p.lower() for p in parts]
    name = lower[-1]
    if len(parts) == 1:
        return "agent"
    if name == "ideas.md":
        return "ideas"  # the ideas backlog (navig/projects/ideas.py), not a plan
    # Folder conventions seen in real projects (Schema is the reference): plans/archive is
    # history, plans/ideas the idea bank, refs/ reference material, proposed/ AI output
    # awaiting review. `archive` is checked FIRST so an archived plan is never "a plan".
    for marker, group in (("archive", "archive"), ("briefs", "briefs"), ("tasks", "tasks"),
                          ("wiki", "wiki"), ("inbox", "inbox"), ("ideas", "ideas"),
                          ("refs", "refs"), ("proposed", "proposed")):
        if marker in lower[:-1]:
            return group
    if "plans" in lower[:-1]:
        return "plans"
    if name.endswith(".txt") or name in ("vision.md", "context.md", "genesis.md", "profile.md"):
        return "identity"
    return "other"


def _allowed_rel(rel: str) -> bool:
    """The containment rule, shared by the index and by every read/write."""
    parts = rel.split("/")
    if not parts or any(p in ("", ".", "..") for p in parts):
        return False
    if not parts[-1].lower().endswith(_EXTS):
        return False
    if len(parts) == 1:
        # The agent files, plus the legacy flat layout that keeps plans at the root
        # (`plan_files.PLAN_DIRS` still reads it, so the hub must too).
        return parts[0] in ROOT_ALLOWLIST or (
            parts[0].lower().endswith(".md") and _canonical_rank(Path(parts[0]).stem) is not None
        )
    if parts[0] not in (".navig", "plans"):
        return False
    if len(parts) - 1 > _MAX_DEPTH:
        return False
    return not any(p.lower() in DENY_DIRS for p in parts[1:-1])


def resolve_doc(root: Path, rel: str) -> Path:
    """*rel* (posix, project-relative) → an absolute path, or :class:`DocError`.

    Containment is checked twice: on the logical path (the deny-list and the
    allowed roots) and after ``resolve()`` (a symlink must not lead out).
    """
    from navig.gateway.deck.routes._utils import confine_under  # noqa: PLC0415

    norm = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not _allowed_rel(norm):
        raise DocError("that path is not a project document", 400)
    target = confine_under(root, norm)
    if target is None:
        raise DocError("path escapes the project", 400)
    return target


def _entry(root: Path, path: Path) -> dict[str, Any] | None:
    try:
        st = path.stat()
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    from navig.plans.frontmatter import first_h1, parse_frontmatter  # noqa: PLC0415

    rel = path.relative_to(root).as_posix()
    title = ""
    if path.suffix.lower() == ".md":
        try:
            title = first_h1(head) or str(parse_frontmatter(head).get("title", "") or "")
        except Exception:  # noqa: BLE001 — a malformed frontmatter only costs the title
            title = ""
    group = _group(rel)
    rank = _canonical_rank(path.stem) if path.suffix.lower() == ".md" else None
    if rank is not None and group in ("agent", "other", "identity"):
        group = "plans"  # `.navig/vision.md` and a legacy root `VISION.md` are plans too
    return {
        "path": rel,
        "name": path.name,
        "title": title or path.stem,
        "group": group,
        "canonical": rank,
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def index_docs(root: Path) -> list[dict[str, Any]]:
    """Every reachable doc in *root*, canonical plans first. Bounded and total."""
    out: list[dict[str, Any]] = []
    try:
        root_files = sorted(p for p in root.iterdir() if p.is_file())
    except OSError:
        root_files = []
    for p in root_files:
        if _allowed_rel(p.name):
            e = _entry(root, p)
            if e:
                out.append(e)
    inbox_seen = 0
    # A root `plans/` is often a LINK to `.navig/plans/` (Schema keeps one "so it is reachable
    # from the root"): walking both listed every plan twice, which doubled todo counts and the
    # context pack. Dedupe on the real path; `.navig` is walked first, so its spelling wins.
    seen_real: set[str] = set()
    root_real = os.path.normcase(os.path.realpath(root)) + os.sep
    for top in (".navig", "plans"):
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=True):
            depth = len(Path(dirpath).relative_to(base).parts)
            dirnames[:] = sorted(
                d for d in dirnames
                if d.lower() not in DENY_DIRS and not d.startswith(".") and depth < _MAX_DEPTH
            )
            for fn in sorted(filenames):
                if not fn.lower().endswith(_EXTS):
                    continue
                p = Path(dirpath) / fn
                rel = p.relative_to(root).as_posix()
                if not _allowed_rel(rel):
                    continue
                real = os.path.normcase(os.path.realpath(p))
                if real in seen_real or not real.startswith(root_real):
                    continue  # a duplicate via a link, or a link that leads OUT of the project
                seen_real.add(real)
                if "/inbox/" in f"/{rel.lower()}":
                    # An inbox can hold a whole Notion export (cybesis: hundreds of files);
                    # give it its own cap so it never crowds the plans out of the index.
                    if inbox_seen >= _MAX_INBOX:
                        continue
                    inbox_seen += 1
                e = _entry(root, p)
                if e:
                    out.append(e)
                if len(out) >= _MAX_DOCS:
                    break
            if len(out) >= _MAX_DOCS:
                logger.debug("doc index capped at %d for %s", _MAX_DOCS, root)
                break

    def _location(rel: str) -> int:
        # The same nearest-first order `plan_files.PLAN_DIRS` reads with, so the copy
        # listed first is the copy every other reader uses (grabnite keeps a ROADMAP.md
        # at the root AND in .navig/plans — the latter is the one that counts).
        folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
        return {".navig/plans": 0, "plans": 1, "": 2, ".navig": 3}.get(folder, 4)

    def _sort_key(d: dict[str, Any]) -> tuple:
        rank = d["canonical"]
        last = d["group"] in ("inbox", "archive")
        # Numbered plans (`00-MASTER-ROADMAP.md`, `07-backlog.md`) keep their phase order.
        num = _NUMBERED.match(d["name"])
        return (1 if last else 0, 0 if rank is not None else 1 if num else 2,
                rank if rank is not None else int(num.group(1)) if num else 0,
                _location(d["path"]), d["path"].count("/"), d["path"].lower())

    out.sort(key=_sort_key)
    return out


def read_doc(root: Path, rel: str) -> dict[str, Any]:
    """Read one doc. ``fingerprint`` is the token a later save must echo back."""
    target = resolve_doc(root, rel)
    if not target.is_file():
        raise DocError("document not found", 404)
    if target.stat().st_size > _MAX_READ_BYTES:
        raise DocError("document is too large to open here", 413)
    try:
        text = textio.read_exact(target)
        editable = True
    except textio.NotUtf8Error:
        text = target.read_text(encoding="utf-8", errors="replace")
        editable = False
    return {
        "path": target.relative_to(root.resolve()).as_posix(),
        "content": text,
        "fingerprint": textio.fingerprint(text),
        "mtime": target.stat().st_mtime,
        "eol": "crlf" if textio.dominant_eol(text) == "\r\n" else "lf",
        "editable": editable,
    }


def save_doc(root: Path, rel: str, content: str, *, expect: str | None = None) -> dict[str, Any]:
    """Save (or create) one doc atomically.

    *expect* is the fingerprint the editor loaded; a mismatch means someone else
    changed the file meanwhile, and the save is refused (409) rather than
    silently overwriting their edit. The file keeps its own line endings — an
    editor sends ``\\n``, a CRLF file stays CRLF.
    """
    if not isinstance(content, str):
        raise DocError("content must be a string")
    target = resolve_doc(root, rel)
    eol = "\n"
    if target.is_file():
        try:
            current = textio.read_exact(target)
        except textio.NotUtf8Error as exc:
            raise DocError(str(exc), 422) from exc
        if expect is not None and textio.fingerprint(current) != expect:
            raise DocError("the document changed since you opened it — reload and retry", 409)
        eol = textio.dominant_eol(current)
    elif expect:
        raise DocError("the document was deleted since you opened it", 409)
    final = textio.normalize_eol(content, eol)
    textio.write_exact(target, final)
    return {
        "path": target.relative_to(root.resolve()).as_posix(),
        "fingerprint": textio.fingerprint(final),
        "mtime": target.stat().st_mtime,
    }

"""Session links — which Claude Code session touched, planned or committed what.

Links live in the recall ``links`` table (``a`` = ``cc:<sessionId>``) and in the vault's
append-only ``.links.jsonl``, so ``navig recall reindex`` restores them. No transcript text is
stored here — only paths, short commit ids and relation names.

Target keys:

* ``file:<project>/<project-relative posix path>`` — a file in a project (worktree paths fold
  to the project, so ``.dev/worktrees/x/core/a.py`` and ``core/a.py`` are the same target);
* ``plan:claude/<name>.md`` — a Claude Code plan file (``~/.claude*/plans/<name>.md``);
* ``commit:<short sha>`` — a commit made by ``git commit`` inside the session.

Relations: ``touched`` (Edit/Write succeeded), ``planned`` (ExitPlanMode plan file),
``committed`` (commit output), ``linked`` (the operator linked it by hand). An unlink is a
tombstone ``-<rel>`` appended to the vault and replayed in order by ``apply_links``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

from navig.recall.projects import SHARED, ProjectResolver

SESSION_PREFIX = "cc:"
RELS = ("touched", "planned", "committed", "linked")
DERIVED_RELS = ("touched", "planned", "committed")  # re-derivable from the transcript
# Bump when extraction changes: sources ingested under an older version are re-read once for links.
LINKS_VERSION = 3  # 2: worktree files share their main-checkout key · 3: .navig_worktrees folds too

EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
_COMMIT_CMD = re.compile(r"\bgit\b[^\n|;&]*\bcommit\b")
_COMMIT_OUT = re.compile(r"^\[(?P<branch>[^\]\s]+)(?: \(root-commit\))? (?P<sha>[0-9a-f]{7,40})\]", re.M)
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")

ACTIVE_MINUTES = 30
RECENT_DAYS = 7


def is_session_id(value: str) -> bool:
    return bool(_UUID.match(value or ""))


def is_commit_id(value: str) -> bool:
    return bool(_SHA.match((value or "").strip()))


def is_commit_command(command: str) -> bool:
    return bool(_COMMIT_CMD.search(command or ""))


def commit_shas(command: str, output: str) -> list[str]:
    """Short commit ids printed by ``git commit`` (``[branch abc1234] message``)."""
    if not is_commit_command(command):
        return []
    return [m.group("sha").lower()[:12] for m in _COMMIT_OUT.finditer(output or "")]


def _claude_plan_name(path: str) -> str | None:
    p = PurePath(path.replace("\\", "/"))
    parts = [s.casefold() for s in p.parts]
    if len(parts) >= 3 and parts[-2] == "plans" and parts[-3].startswith(".claude") and p.suffix.lower() == ".md":
        return p.name
    return None


def _main_checkout_twin(path: str, resolver: ProjectResolver) -> tuple[str | None, str | None]:
    """A worktree file → the same file in the main checkout, so an edit made in a worktree
    and one made in the checkout share one key (nested spaces then resolve the same way).

    Returns ``(twin path, None)``, ``(None, "<project>/<rest>")`` for a Codex worktree whose
    project has no registered root, or ``(None, None)`` when *path* is not in a worktree."""
    norm = path.replace("\\", "/")
    low = norm.casefold()
    i = low.find("/.dev/worktrees/")
    if i >= 0:
        rest = norm[i + len("/.dev/worktrees/"):].split("/", 1)
        return (norm[:i] + "/" + rest[1], None) if len(rest) == 2 and rest[1] else (None, None)
    # The agent's own isolation worktrees (agent/worktree.py WORKTREE_DIR) fold the same way.
    n = low.find("/.navig_worktrees/")
    if n >= 0:
        rest = norm[n + len("/.navig_worktrees/"):].split("/", 1)
        return (norm[:n] + "/" + rest[1], None) if len(rest) == 2 and rest[1] else (None, None)
    j = low.find("/.codex/worktrees/")
    if j >= 0:
        parts = norm[j + len("/.codex/worktrees/"):].split("/", 2)
        if len(parts) == 3 and parts[2]:
            pid = resolver.by_name(parts[1])
            root = resolver.root_for(pid) if pid else None
            if root:
                return root.replace("\\", "/") + "/" + parts[2], None
            return None, f"{pid or parts[1].casefold()}/{parts[2]}"
    return None, None


def _collapse_dots(path: str) -> str:
    """Resolve ``.`` and ``..`` segments lexically (``core/../apps/x`` → ``apps/x``)."""
    parts: list[str] = []
    for seg in path.replace("\\", "/").split("/"):
        if seg == "..":
            if parts and parts[-1] not in ("", "..") and not parts[-1].endswith(":"):
                parts.pop()
        elif seg != "." or not parts:
            parts.append(seg)
    return "/".join(parts)


def target_key(path: str, resolver: ProjectResolver) -> str | None:
    """Stable target key for an absolute file path, or None when it belongs to no project."""
    if not path or not (os.path.isabs(path) or path.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path)):
        return None
    path = _collapse_dots(path)
    name = _claude_plan_name(path)
    if name:
        return f"plan:claude/{name}"
    twin, unrooted = _main_checkout_twin(path, resolver)
    if unrooted:
        return f"file:{unrooted.strip('/')}"
    path = twin or path
    ref = resolver.resolve(path)
    if ref.id == SHARED or not ref.root:
        return None
    root = ref.root.replace("\\", "/").rstrip("/")
    norm = path.replace("\\", "/")
    if not norm.casefold().startswith(root.casefold() + "/"):
        return None
    rel = norm[len(root) + 1:].strip("/")
    return f"file:{ref.id}/{rel}" if rel else None


def query_key(target: str, resolver: ProjectResolver) -> str | None:
    """Key for what the operator asked about: a path, a plan file, a commit or a raw key."""
    t = (target or "").strip()
    if t.startswith(("file:", "plan:", "commit:")):
        return t
    if _SHA.match(t):
        return f"commit:{t.lower()[:12]}"
    # A relative path is resolved by the caller (the CLI anchors it to where the operator typed it).
    return target_key(t, resolver)


def links_from_meta(source_id: str, meta: dict[str, Any], resolver: ProjectResolver) -> list[tuple[str, str, str]]:
    """Links for one parsed session (``touched`` / ``planned`` paths, ``commits``)."""
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rel, paths in (("touched", meta.get("touched") or []), ("planned", meta.get("planned") or [])):
        for p in paths:
            key = target_key(str(p), resolver)
            if key and (key, rel) not in seen:
                seen.add((key, rel))
                out.append((source_id, key, rel))
    for sha in meta.get("commits") or []:
        if ("commit:" + sha, "committed") not in seen:
            seen.add(("commit:" + sha, "committed"))
            out.append((source_id, f"commit:{sha}", "committed"))
    return out


def activity(last_ts: str, now: datetime | None = None) -> str:
    """``active`` (written in the last 30 min) · ``recent`` (7 days) · ``historical``.
    Activity-based: a long-idle session that is still open reads as recent/historical."""
    try:
        ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "historical"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (now or datetime.now(timezone.utc)) - ts
    if age.total_seconds() <= ACTIVE_MINUTES * 60:
        return "active"
    if age.days < RECENT_DAYS:
        return "recent"
    return "historical"


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    title: str
    project: str
    cwd: str
    branch: str
    first_ts: str
    last_ts: str
    state: str
    rels: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "title": self.title, "project": self.project, "cwd": self.cwd,
                "branch": self.branch, "first_ts": self.first_ts, "last_ts": self.last_ts, "state": self.state,
                "rels": list(self.rels), "open_uri": f"vscode://anthropic.claude-code/open?session={self.session_id}"}


def row_from_source(src: dict[str, Any], rels: list[str], now: datetime | None = None) -> SessionRow:
    sid = str(src["source_id"])[len(SESSION_PREFIX):]
    meta = src.get("meta") or {}
    return SessionRow(session_id=sid, title=str(src.get("title") or ""), project=str(src.get("project") or ""),
                      cwd=str(src.get("cwd") or ""), branch=str(meta.get("git_branch") or ""),
                      first_ts=str(src.get("first_ts") or ""), last_ts=str(src.get("last_ts") or ""),
                      state=activity(str(src.get("last_ts") or ""), now), rels=tuple(sorted(set(rels))))


# ── the one service both the CLI and the deck route call ─────


def find_sessions(store: Any, target: str | None = None, *, project: str | None = None,
                  resolver: ProjectResolver | None = None, limit: int = 200) -> list[SessionRow]:
    """Sessions for *target* (path / plan file / commit / key) or for a whole *project*.
    Raises ``ValueError`` when *target* maps to no project."""
    key = None
    if target:
        key = query_key(target, resolver or ProjectResolver())
        if key is None:
            raise ValueError(f"{target!r} is not inside a known project")
    now = datetime.now(timezone.utc)
    return [row_from_source(src, rels, now) for src, rels in store.sessions_for(key, project=project, limit=limit)]


def _session_source(store: Any, session_id: str) -> str:
    if not is_session_id(session_id):
        raise ValueError(f"{session_id!r} is not a Claude Code session id")
    sid = SESSION_PREFIX + session_id.lower()
    if store.get_source(sid) is None:
        raise LookupError(f"session {session_id} is not in the recall bank — run `navig recall ingest`")
    return sid


def link(store: Any, vault: Any, session_id: str, target: str, resolver: ProjectResolver | None = None) -> bool:
    """Link a session to a target by hand. True when the link is new."""
    sid = _session_source(store, session_id)
    key = query_key(target, resolver or ProjectResolver())
    if key is None:
        raise ValueError(f"{target!r} is not inside a known project")
    store.clear_tombstone(sid, key, "linked")  # the operator's own link may follow their unlink
    fresh = store.add_links([(sid, key, "linked")])
    vault.append_links(fresh)
    return bool(fresh)


def unlink(store: Any, vault: Any, session_id: str, target: str, resolver: ProjectResolver | None = None,
           rels: tuple[str, ...] = RELS) -> int:
    """Remove a session's links to a target (any relation by default). Survives reindex."""
    sid = _session_source(store, session_id)
    key = query_key(target, resolver or ProjectResolver())
    if key is None:
        raise ValueError(f"{target!r} is not inside a known project")
    tombstones = store.unlink(sid, key, rels)
    vault.append_links(tombstones)
    return len(tombstones)

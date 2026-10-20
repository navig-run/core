"""History views — what happened to a file, a plan, a commit, a session or a project.

Deterministic, zero LLM, indexed point lookups only (the benchmark in
``tests/recall/test_recall_bench.py`` pins the no-full-scan plans and the latency
budget). Every view returns plain dicts so the CLI, and later MCP/deck, render the
same answer from ``navig.recall.query`` delegates.

Shapes (all ``to_dict``-able, ``limit``-bounded):

* :func:`history` — one target (path · plan · commit key) → sessions, commits and
  records in one merged timeline;
* :func:`trace` — a commit sha **or** a session id → the other side of the edge plus
  touched files and linked records;
* :func:`changes` — recent commits of a project (git-observed, cached in ``commits``)
  each joined to the sessions and records it explains;
* :func:`unfinished` — plans/tasks nothing has verified yet (display-only: it never
  spawns, resumes or schedules anything).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from navig.recall import gitprov
from navig.recall.projects import ProjectResolver
from navig.recall.session_links import SESSION_PREFIX, is_commit_id, is_session_id, query_key

#: Lifecycle states an unfinished item may be in (unknown counts: never derived/observed).
UNFINISHED_LIFECYCLES = ("proposed", "implemented_uncommitted", "committed", "unknown")
UNFINISHED_TYPES = ("plan", "task")


def _store(store: Any) -> Any:
    if store is not None:
        return store
    from navig.recall.store import get_recall_store  # noqa: PLC0415

    return get_recall_store()


def _sessions_rows(store: Any, key: str, limit: int, project: str | None = None) -> list[dict[str, Any]]:
    from navig.recall.session_links import find_sessions  # noqa: PLC0415

    try:
        return [r.to_dict() for r in find_sessions(store, key, project=project, limit=limit)]
    except ValueError:
        return []


def _records_for_sessions(store: Any, source_ids: list[str], limit: int) -> list[dict[str, Any]]:
    """Records whose evidence comes from these sessions (one batched query)."""
    from navig.recall.query import record_row  # noqa: PLC0415

    if not source_ids:
        return []
    marks = ", ".join("?" for _ in source_ids)
    rows = store._read_all(  # noqa: SLF001 — one scoped read, not a schema reach-around
        f"SELECT DISTINCT r.id FROM evidence e JOIN records r ON r.id = e.record_id "
        f"WHERE e.source_id IN ({marks}) ORDER BY r.updated DESC LIMIT ?",
        (*source_ids, limit),
    )
    out = []
    for (rid,) in rows:
        rec = store.get_record(rid)
        if rec is not None:
            out.append(record_row(rec))
    return out


def _commit_entries(store: Any, root: str | None, shas: list[str], limit: int) -> list[dict[str, Any]]:
    """Commits as timeline entries: cached metadata when present, git otherwise."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in shas:
        sha = key[len("commit:"):] if key.startswith("commit:") else key
        if sha in seen:
            continue
        seen.add(sha)
        meta = gitprov.commit_meta(root, sha) if root else None
        if meta is None:
            meta = next((c for c in store.commits_for(limit=500)
                         if c["sha"].startswith(sha)), None)
        if meta is None:
            out.append({"sha": sha, "subject": "", "ts": "", "files": [], "known": False})
        else:
            out.append({"sha": meta["sha"][:12], "subject": meta.get("subject", ""),
                        "ts": meta.get("committer_ts") or meta.get("author_ts") or "",
                        "author": meta.get("author", ""),
                        "files": [f.get("path", "") for f in meta.get("files", [])],
                        "known": True})
        if len(out) >= limit:
            break
    return out


def history(target: str, *, project: str | None = None, limit: int = 50,
            keyword: str | None = None, root: str | None = None,
            resolver: ProjectResolver | None = None, store: Any = None) -> dict[str, Any]:
    """Sessions + commits + records for one target, as one merged timeline."""
    st = _store(store)
    key = query_key(target, resolver or ProjectResolver())
    if key is None:
        raise ValueError(f"{target!r} is not inside a known project")
    sessions = _sessions_rows(st, key, limit, project)
    source_ids = [SESSION_PREFIX + s["session_id"] for s in sessions]

    shas: list[str] = []
    if key.startswith("commit:"):
        shas = [key]
    elif key.startswith("file:"):
        # The design (D-history): git's own history of the file ∪ the commits its
        # sessions linked. Without git, history only showed commits whose SHA a
        # transcript happened to print — a landed-by-land change touching the file
        # was invisible (real benchmark miss, 2026-10-20).
        #
        # File keys are PROJECT-relative (folded to the canonical checkout), so the
        # path must be joined against the key's project canonical root — joining
        # against an arbitrary caller `root` (a worktree) yields a double-nested
        # path that does not exist and silently drops the git half (second
        # benchmark miss). First existing candidate wins; none → linked-only.
        rel = key.split("/", 1)[1] if "/" in key else ""
        proj_of_key = key[len("file:"):].split("/", 1)[0]
        git_root: str | None = None
        if rel:
            canon = (resolver or ProjectResolver()).root_for(proj_of_key)
            for base in (canon, root):
                if base and Path(base, rel).is_file():
                    git_root = base
                    break
        git_shas = (gitprov.commits_touching(git_root, f"{git_root}/{rel}", limit=limit)
                    if git_root else [])
        linked = [ln["b"] for ln in st._read_all(  # noqa: SLF001 — scoped link read
            "SELECT DISTINCT b FROM links WHERE a IN "
            f"(SELECT a FROM links WHERE b = ? AND rel IN ('touched', 'linked', 'committed')) "
            "AND rel = 'committed' AND b LIKE 'commit:%' LIMIT ?",
            (key, limit))]
        git7 = {s[:7] for s in git_shas}
        shas = list(git_shas) + [b for b in linked if b[len("commit:"):][:7] not in git7]
    else:
        shas = [ln["b"] for ln in st._read_all(  # noqa: SLF001 — scoped link read
            "SELECT DISTINCT b FROM links WHERE a IN "
            f"(SELECT a FROM links WHERE b = ? AND rel IN ('touched', 'linked', 'committed')) "
            "AND rel = 'committed' AND b LIKE 'commit:%' LIMIT ?",
            (key, limit))]
    commits = _commit_entries(st, root, shas, limit)
    records = _records_for_sessions(st, source_ids, limit)
    if keyword:
        kw = keyword.casefold()
        records = [r for r in records
                   if kw in (str(r.get("title", "")) + str(r.get("summary", ""))).casefold()]
        commits = [c for c in commits if kw in str(c.get("subject", "")).casefold()]

    timeline: list[dict[str, Any]] = [
        {"kind": "session", "ts": s.get("last_ts") or s.get("first_ts") or "",
         "title": s.get("title") or s["session_id"][:8], "ref": s["session_id"]} for s in sessions]
    timeline += [{"kind": "commit", "ts": c.get("ts", ""),
                  "title": c.get("subject") or c["sha"], "ref": c["sha"]} for c in commits]
    timeline += [{"kind": "record", "ts": r.get("updated", ""),
                  "title": r.get("title", ""), "ref": r["id"]} for r in records]
    timeline.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return {"key": key, "sessions": sessions, "commits": commits, "records": records,
            "timeline": timeline[:limit], "count": len(timeline[:limit])}


def trace(ref: str, *, project: str | None = None, limit: int = 50,
          root: str | None = None, store: Any = None) -> dict[str, Any]:
    """A commit sha or a Claude session id → the other side of its edges."""
    st = _store(store)
    ref = (ref or "").strip()
    if is_session_id(ref):
        sid = SESSION_PREFIX + ref.lower()
        src = st.get_source(sid)
        if src is None:
            raise LookupError(f"session {ref} is not in the recall bank — run `navig recall ingest`")
        links = st.links_of(sid)
        shas = [ln["b"] for ln in links if ln["b"].startswith("commit:")]
        files = [ln["b"] for ln in links if ln["b"].startswith("file:")]
        plans = [ln["b"] for ln in links if ln["b"].startswith("plan:")]
        records = _records_for_sessions(st, [sid], limit)
        return {"kind": "session", "session": {**src, "links": links},
                "commits": _commit_entries(st, root, shas, limit),
                "files": files, "plans": plans, "records": records,
                "count": len(links) + len(records)}
    if is_commit_id(ref):
        sha = ref.lower()[:12]
        key = f"commit:{sha}"
        sessions = _sessions_rows(st, key, limit, project)
        source_ids = [SESSION_PREFIX + s["session_id"] for s in sessions]
        commits = _commit_entries(st, root, [key], limit)
        meta = commits[0] if commits else None
        return {"kind": "commit",
                "commit": {"sha": meta.get("sha", sha) if meta else sha,
                           "subject": meta.get("subject", "") if meta else "",
                           "ts": meta.get("ts", "") if meta else "",
                           "files": meta.get("files", []) if meta else [],
                           "known": bool(meta and meta.get("known"))},
                "sessions": sessions, "records": _records_for_sessions(st, source_ids, limit),
                "count": len(sessions)}
    raise ValueError(f"{ref!r} is neither a commit id nor a Claude session id")


def changes(project: str | None, *, root: str | None = None, since_days: int = 90,
            limit: int = 50, refresh: bool = False, store: Any = None) -> dict[str, Any]:
    """Recent commits of a project, each joined to the sessions and records it explains."""
    st = _store(store)
    if not project:
        raise ValueError("name a project (--project / --here)")
    if refresh and root:
        # git log over the project folder, newest first — names only, never diffs
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, since_days))).strftime("%Y-%m-%d")
        shas = gitprov.commits_touching(root, ".", since=since, limit=limit)
        rows = []
        for sha in shas:
            meta = gitprov.commit_meta(root, sha)
            if meta:
                branch = gitprov.default_branch(root) or ""
                merged = gitprov.is_ancestor(root, sha, branch) if branch else None
                rows.append({**meta, "project": project, "branch": branch,
                             "merged": {True: "yes", False: "no"}.get(merged, "unknown"),
                             "git_root": gitprov.toplevel(root) or root,
                             "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        if rows:
            st.upsert_commits(rows)
    since_ts = (datetime.now(timezone.utc) - timedelta(days=max(1, since_days))).strftime("%Y-%m-%d")
    commits = st.commits_for(project=project, since=since_ts, limit=limit)
    out = []
    for c in commits:
        key = f"commit:{c['sha']}"
        sessions = _sessions_rows(st, key, 3)
        out.append({**c, "sessions": sessions,
                    "records": _records_for_sessions(
                        st, [SESSION_PREFIX + s["session_id"] for s in sessions], 5)})
    return {"project": project, "since": since_ts, "commits": out, "count": len(out)}


def unfinished(project: str | None, *, limit: int = 30, store: Any = None) -> dict[str, Any]:
    """Plans/tasks nothing verified yet — oldest activity first. Display-only output:
    no spawning, no resuming, no scheduling, ever."""
    from navig.recall.query import record_row  # noqa: PLC0415

    st = _store(store)
    recs = st.records_by_lifecycle(UNFINISHED_LIFECYCLES, rtypes=UNFINISHED_TYPES,
                                   projects=[project] if project else None, limit=limit,
                                   oldest_first=True)
    rows = []
    for r in recs:
        if r.impl == "verified":
            continue
        rows.append({**record_row(r),
                     "lifecycle": r.lifecycle,
                     "basis": r.attrs.get("lifecycle_basis", "unknown")})
    rows.sort(key=lambda r: r.get("updated") or "")
    return {"project": project, "records": rows[:limit], "count": min(len(rows), limit),
            "note": "display-only — navigate with: navig recall show <id>"}

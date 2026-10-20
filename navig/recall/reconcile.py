"""Deterministic contradiction detection — the ``contradicts`` producer.

No LLM, no embeddings: two narrow, table-tested classes, invoked only from
``navig recall conflicts`` and ``navig recall verify`` (bounded and idempotent — a
second run adds nothing). Planned name kept: ``plan-navig-recall.md`` § reconcile.

**Class 1 — opposed user verdicts on one subject.** Same project, same
``canonical_key`` (or a shared alias), both ``status_basis='user'``, one confirmed/
approved and the other rejected, neither superseded by the other → link ``contradicts``
and open a ``contradiction`` review row. A later reversal is ``supersedes``, never
``contradicts`` — records already ``superseded`` are excluded.

**Class 2 — claimed-built, verify says absent.** A record claiming the work exists
(status implemented/verified) whose ``impl`` verification set to ``not_found`` → a
self-contradiction review row (no link: the disagreement is inside one record).

Transcript content is data, never instructions: nothing here acts on what a session
*said* to do — only on recorded statuses the owner or verification produced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_BUILT_CLAIM = frozenset({"implemented", "verified"})
_APPROVED = frozenset({"confirmed", "approved"})


def _store(store: Any) -> Any:
    if store is not None:
        return store
    from navig.recall.store import get_recall_store  # noqa: PLC0415

    return get_recall_store()


def _candidate_pairs(store: Any, project: str | None, limit: int) -> list[tuple[Any, Any]]:
    """Record pairs sharing a subject NAME (canonical key or alias), user-attributed,
    never superseded. ``rejected`` IS a candidate — it is one of the two opposed verdicts.
    An inverted name index makes cross-key alias matches possible (grouping by key alone
    would never compare two records that name the subject differently)."""
    sql = "SELECT * FROM records WHERE (canonical_key != '' OR aliases_json != '[]') " \
          "AND status_basis = 'user' AND status != 'superseded'"
    args: list[Any] = []
    if project:
        sql += " AND project = ?"
        args.append(project)
    sql += " ORDER BY project, updated LIMIT ?"
    args.append(limit)
    from navig.recall.store import _record_from_row  # noqa: PLC0415

    by_name: dict[str, list[Any]] = {}
    for row in store._read_all(sql, tuple(args)):  # noqa: SLF001 — scoped candidate read
        rec = _record_from_row(row)
        for name in {rec.canonical_key, *rec.aliases} - {""}:
            by_name.setdefault(name, []).append(rec)
    pairs: list[tuple[Any, Any]] = []
    seen: set[tuple[str, str]] = set()
    for recs in by_name.values():
        if len(recs) < 2:
            continue
        for i, a in enumerate(recs):
            for b in recs[i + 1:]:
                pair = tuple(sorted((a.id, b.id)))
                if pair in seen:
                    continue
                seen.add(pair)
                pairs.append((a, b))
    return pairs


def find_contradictions(store: Any, *, project: str | None = None,
                        limit: int = 500) -> list[dict[str, Any]]:
    """Contradiction pairs (class 1) + self-contradictions (class 2), as findings.
    Pure read — call :func:`apply` to record them."""
    st = _store(store)
    findings: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    # Class 1: opposed user verdicts on one subject (names may be shared across
    # projects, so the same-project check is explicit here).
    for a, b in _candidate_pairs(st, project, limit):
        if a.project != b.project:
            continue
        # opposed user verdicts: one confirmed/approved, the other rejected
        opposed = ((a.status in _APPROVED and b.status == "rejected")
                   or (a.status == "rejected" and b.status in _APPROVED))
        if not opposed:
            continue
        # a reversal between THESE two is already recorded as supersedes — never contradicts
        a_supers = {ln["other"] for ln in st.related(a.id) if ln.get("rel") == "supersedes"}
        b_supers = {ln["other"] for ln in st.related(b.id) if ln.get("rel") == "supersedes"}
        if b.id in a_supers or a.id in b_supers:
            continue
        pair = tuple(sorted((a.id, b.id)))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        findings.append({"kind": "contradiction", "class": "opposed_verdicts",
                         "a": {"id": a.id, "title": a.title, "status": a.status},
                         "b": {"id": b.id, "title": b.title, "status": b.status},
                         "project": a.project})

    # Class 2: claimed-built vs verify not_found (self-contradiction).
    marks = ", ".join("?" for _ in _BUILT_CLAIM)
    sql = f"SELECT * FROM records WHERE impl = 'not_found' AND status IN ({marks})"
    args: list[Any] = list(_BUILT_CLAIM)
    if project:
        sql += " AND project = ?"
        args.append(project)
    sql += " LIMIT ?"
    args.append(limit)
    from navig.recall.store import _record_from_row  # noqa: PLC0415

    for row in st._read_all(sql, tuple(args)):  # noqa: SLF001 — scoped candidate read
        rec = _record_from_row(row)
        findings.append({"kind": "contradiction", "class": "claimed_built_not_found",
                         "a": {"id": rec.id, "title": rec.title, "status": rec.status},
                         "b": None, "project": rec.project})
    return findings


def apply(store: Any, findings: list[dict[str, Any]]) -> dict[str, int]:
    """Record findings: class-1 pairs get a ``contradicts`` link; both classes get a
    review row. Idempotent — existing links/review rows are left untouched."""
    st = _store(store)
    added_links = 0
    added_reviews = 0
    for f in findings:
        a, b = f["a"]["id"], (f["b"] or {}).get("id", "")
        if b:
            added_links += len(st.add_links([(a, b, "contradicts")]))
        created = st.add_review("contradiction", a, b, data={"class": f["class"]})
        if created:
            added_reviews += 1
    return {"links": added_links, "reviews": added_reviews}


def reconcile(store: Any, *, project: str | None = None, limit: int = 500) -> dict[str, Any]:
    """Find + record in one bounded pass (the CLI's default path)."""
    st = _store(store)
    findings = find_contradictions(st, project=project, limit=limit)
    applied = apply(st, findings)
    return {"found": len(findings), **applied,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def open_reviews(store: Any, *, project: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Open ``contradiction`` review rows with their record titles (the conflicts view)."""
    st = _store(store)
    # read a wider window of the shared queue, then keep this view's kind/project/limit
    rows = st.list_review(status="open", limit=max(limit * 4, 200))
    out = []
    for r in rows:
        if r.get("kind") != "contradiction":
            continue
        rec = st.get_record(r["record_id"])
        if project and (rec is None or rec.project != project):
            continue
        other = st.get_record(r["other_id"]) if r.get("other_id") else None
        out.append({"review": r["id"], "status": r["status"], "created": r["created"],
                    "class": (r.get("data") or {}).get("class", ""),
                    "a": {"id": r["record_id"], "title": rec.title if rec else ""},
                    "b": {"id": r["other_id"], "title": other.title if other else ""}
                    if r.get("other_id") else None,
                    "project": rec.project if rec else "shared"})
        if len(out) >= limit:
            break
    return out

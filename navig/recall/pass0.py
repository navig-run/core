"""Pass 0 — deterministic records from attributed turns (no LLM, nothing leaves the machine).

* ``user_answer`` (AskUserQuestion)  → one **decision** per question, endorsed by the user
* ``plan`` + ``user_plan_verdict``    → a **plan**; approved only by the operator's verdict
* Markdown tables in assistant text / agent reports / web captures → **table** records, kept verbatim
* ``agent_report``                     → a **finding** (research the operator commissioned)

Also home of :func:`merge`, the one rule for folding new evidence into an existing record:
evidence is unioned, never dropped; the status only moves when the new evidence carries
equal-or-stronger authority (user > verify > audit > assistant), and every move is an event.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from navig.recall.mdtables import find_tables
from navig.recall.model import Evidence, Record, Turn, canonical_key, record_id, text_hash

_BASIS_RANK = {"assistant": 0, "audit": 1, "verify": 2, "user": 3}
_FIRST_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_MAX_FINDING_BODY = 20_000


def _first_line(text: str, limit: int = 140) -> str:
    m = _FIRST_HEADING.search(text)
    if m:
        return m.group(1).strip()[:limit]
    for line in text.splitlines():
        s = line.strip(" #*>-\t")
        if s:
            return s[:limit]
    return "untitled"


def _norm(text: str) -> str:
    return " ".join(text.split())


def records_from_turns(source_id: str, project: str, turns: list[Turn], *,
                       plan_for_tool: Callable[[str], str | None] | None = None) -> list[Record]:
    """Records for *turns* (already redacted). *plan_for_tool* finds a plan record created in
    an earlier incremental read, so a verdict that arrives later still reaches its plan."""
    out: list[Record] = []
    plans_by_tool: dict[str, Record] = {}
    for t in turns:
        if t.speaker == "user_answer":
            out.extend(_decisions(source_id, project, t))
        elif t.kind == "plan":
            rec = _plan(source_id, project, t)
            out.append(rec)
            if t.meta.get("tool_use_id"):
                plans_by_tool[str(t.meta["tool_use_id"])] = rec
        elif t.speaker == "user_plan_verdict":
            tid = str(t.meta.get("tool_use_id") or "")
            target = plans_by_tool.get(tid)
            if target is None and plan_for_tool and tid:
                pid = plan_for_tool(tid)
                if pid:
                    target = Record(id=pid, type="plan", title="", project=project, status_basis="assistant")
                    out.append(target)
            if target is not None:
                _apply_verdict(target, source_id, t)
        if t.speaker in ("assistant", "agent_report", "web_capture") and "|" in t.text:
            out.extend(_tables(source_id, project, t))
        if t.speaker == "agent_report" and t.kind == "report":
            out.append(_finding(source_id, project, t))
    return out


def _decisions(source_id: str, project: str, t: Turn) -> list[Record]:
    pairs = t.meta.get("pairs") or [{"question": t.text, "answer": "", "header": "", "options": []}]
    out = []
    for i, p in enumerate(pairs):
        q, a = str(p.get("question") or "").strip(), str(p.get("answer") or "").strip()
        if not q:
            continue
        title = (p.get("header") + ": " if p.get("header") else "") + q
        out.append(Record(
            id=record_id(source_id, t.msg_id, "decision", str(i)), type="decision", title=title[:160],
            summary=f"Chose: {a}" if a else q, body=f"**Question:** {q}\n\n**Answer:** {a}\n\n"
            + ("**Options offered:** " + " · ".join(p.get("options") or []) if p.get("options") else ""),
            project=project, status="confirmed", status_basis="user", endorsement="user_endorsed",
            confidence=0.95, canonical_key=canonical_key(q), tags=["asked"], created=t.ts, updated=t.ts,
            attrs={"answer": a, "options": p.get("options") or [], "header": p.get("header") or ""},
            evidence=[Evidence(source_id, t.msg_id, t.speaker, f"Q: {q}\nA: {a}"[:1000], t.ts)],
        ))
    return out


def _plan(source_id: str, project: str, t: Turn) -> Record:
    return Record(
        id=record_id("plan", project, text_hash(_norm(t.text))), type="plan", title=_first_line(t.text),
        summary="", body=t.text, project=project, status="proposed", status_basis="assistant",
        confidence=0.9, canonical_key=canonical_key(_first_line(t.text)), tags=["plan"],
        created=t.ts, updated=t.ts, attrs={"tool_use_id": str(t.meta.get("tool_use_id") or "")},
        evidence=[Evidence(source_id, t.msg_id, t.speaker, _first_line(t.text, 400), t.ts)],
    )


def _apply_verdict(rec: Record, source_id: str, t: Turn) -> None:
    approved = bool(t.meta.get("approved"))
    rec.evidence.append(Evidence(source_id, t.msg_id, t.speaker, t.text[:600], t.ts))
    rec.updated = t.ts or rec.updated
    if approved:
        rec.status, rec.status_basis, rec.endorsement = "approved", "user", "user_endorsed"
    else:
        # Declining to leave plan mode asks for revision; it is not a rejection of the idea.
        rec.attrs["last_verdict"] = "not approved"


def _tables(source_id: str, project: str, t: Turn) -> list[Record]:
    out = []
    for i, tbl in enumerate(find_tables(t.text)):
        if len(tbl.columns) < 2:
            continue
        title = tbl.caption.strip(" :*#") or f"Table: {' · '.join(tbl.columns[:4])}"
        out.append(Record(
            id=record_id(source_id, t.msg_id, "table", str(i)), type="table", title=title[:160],
            summary=f"{len(tbl.rows)} rows × {len(tbl.columns)} columns: {', '.join(tbl.columns[:8])}"[:300],
            body=tbl.raw, project=project, status="proposed", status_basis="assistant", confidence=1.0,
            canonical_key=canonical_key(title), tags=["table"], created=t.ts, updated=t.ts,
            attrs={"columns": tbl.columns, "rows": len(tbl.rows), "speaker": t.speaker},
            evidence=[Evidence(source_id, t.msg_id, t.speaker, tbl.raw[:600], t.ts)],
        ))
    return out


def _finding(source_id: str, project: str, t: Turn) -> Record:
    desc = str(t.meta.get("description") or "").strip()
    title = desc or _first_line(t.text)
    body = t.text if len(t.text) <= _MAX_FINDING_BODY else t.text[:_MAX_FINDING_BODY] + "\n…[truncated]"
    return Record(
        id=record_id(source_id, t.msg_id, "finding"), type="finding", title=title[:160],
        summary=_norm(t.text)[:400], body=body, project=project, status="proposed",
        status_basis="assistant", confidence=0.8, canonical_key=canonical_key(title),
        tags=["agent-report"], created=t.ts, updated=t.ts, attrs={"description": desc},
        evidence=[Evidence(source_id, t.msg_id, t.speaker, _norm(t.text)[:600], t.ts)],
    )


# ── Merge ────────────────────────────────────────────────────


def merge(existing: Record, new: Record) -> tuple[Record, list[tuple[str, dict[str, Any]]]]:
    """Fold *new* into *existing*. Returns the merged record and ``(kind, data)`` events."""
    events: list[tuple[str, dict[str, Any]]] = []
    seen = {(e.source_id, e.msg_id, e.excerpt) for e in existing.evidence}
    added = [e for e in new.evidence if (e.source_id, e.msg_id, e.excerpt) not in seen]
    existing.evidence.extend(added)
    if added:
        events.append(("evidence", {"added": [f"{e.source_id}#{e.msg_id}" for e in added]}))
    if new.status != existing.status:
        stronger = _BASIS_RANK.get(new.status_basis, 0) >= _BASIS_RANK.get(existing.status_basis, 0)
        newer = (new.updated or "") >= (existing.updated or "")
        if stronger and newer:
            events.append(("status", {"from": existing.status, "to": new.status,
                                      "basis": new.status_basis}))
            existing.status, existing.status_basis = new.status, new.status_basis
    if new.endorsement != "none" and new.endorsement != existing.endorsement:
        events.append(("endorsement", {"from": existing.endorsement, "to": new.endorsement}))
        existing.endorsement = new.endorsement
    for attr in ("title", "summary", "body", "canonical_key"):
        if not getattr(existing, attr) and getattr(new, attr):
            setattr(existing, attr, getattr(new, attr))
    if new.impl != "unknown" and existing.impl == "unknown":
        existing.impl = new.impl
    existing.tags = sorted(set(existing.tags) | set(new.tags))
    existing.aliases = sorted(set(existing.aliases) | set(new.aliases))
    for k, v in new.attrs.items():
        existing.attrs.setdefault(k, v)
    existing.updated = max(existing.updated or "", new.updated or "")
    existing.created = min(filter(None, [existing.created, new.created]), default="")
    return existing, events

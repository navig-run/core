"""Import a completed session audit (``<claude home>/session-audit*``) as seed knowledge.

Two outputs, both read-only against the audit folder:

* **documents** — every audit report (roadmap, completion plan, decisions, recovered ideas,
  conflicts, registers, per-session audits) indexed section by section, so it is searchable
  and citable (``audit:<root>:<relpath>#s<n>-<hash>``);
* **records** — the feature registry (navig and cybesis shapes), decision rows (reversals
  kept as timeline events), recovered ideas, conflicts and the session relationship graph.

Everything carries speaker ``prior_audit`` and ``status_basis = audit``: it is derived agent
analysis and never counts as the operator's own endorsement.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from navig.recall.mdtables import find_tables
from navig.recall.model import Evidence, Record, Turn, canonical_key, record_id
from navig.recall.sources.base import ParseResult, SourceRef

ADAPTER = "session_audit"
DOC_GLOBS = ("*.md", "reports/*.md", "projects/*/*.md", "sessions/*/audit.md")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)
_SID8 = re.compile(r"`?\b([0-9a-f]{8})(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\b`?")
_BACKTICK = re.compile(r"`([a-z0-9][a-z0-9-]{6,})`")
_CONFLICT = re.compile(r"^##\s+(C-\d+)\s*[·:-]\s*(.+)$", re.M)
_MAX_SECTION = 20_000

# audit status → (record status, impl, extra tags)
_STATUS_MAP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "VERIFIED COMPLETE": ("implemented", "verified", ("static-verify",)),
    "IMPLEMENTED": ("implemented", "likely", ()),
    "PARTIAL": ("exploring", "partial", ()),
    "MISSING": ("proposed", "not_found", ()),
    "BROKEN": ("implemented", "partial", ("broken",)),
    "IDEA ONLY": ("proposed", "not_found", ()),
    "REJECTED": ("rejected", "not_found", ()),
    "SUPERSEDED": ("superseded", "superseded", ()),
    "OBSOLETE": ("superseded", "superseded", ("obsolete",)),
    "DUPLICATED": ("superseded", "superseded", ("duplicated",)),
    "UNKNOWN": ("unresolved", "unknown", ()),
}
_CONFIDENCE = {"verified": 0.9, "high": 0.85, "probable": 0.7, "likely": 0.7, "possible": 0.5, "low": 0.4}


def audit_roots(homes: list[Path] | None = None) -> list[Path]:
    from navig.recall.sources.claude_code import claude_homes  # noqa: PLC0415

    out: list[Path] = []
    for home in homes if homes is not None else claude_homes():
        for cand in [home / "session-audit", *sorted(home.glob("session-audit-*"))]:
            if cand.is_dir() and (cand / "README.md").exists():
                out.append(cand)
    return out


def project_for_root(root: Path, resolver: Any) -> str:
    """The audited project: its single ``projects/<slug>`` folder, else the root-name suffix."""
    slugs = [p.name for p in (root / "projects").glob("*") if p.is_dir()] if (root / "projects").is_dir() else []
    if len(slugs) == 1:
        hit = resolver.by_claude_slug(slugs[0])
        if hit:
            return hit
    name = root.name.removeprefix("session-audit").lstrip("-")
    if name:
        return resolver.by_name(name) or name
    return "shared"


def _norm_status(raw: str) -> str:
    s = re.sub(r"[^A-Z ]+", " ", str(raw).upper()).strip()
    s = re.sub(r"\s+", " ", s)
    for key in _STATUS_MAP:
        if s.startswith(key):
            return key
    return "UNKNOWN"


# ── Documents ────────────────────────────────────────────────


def discover(roots: list[Path] | None = None) -> Iterator[SourceRef]:
    for root in roots if roots is not None else audit_roots():
        seen: set[Path] = set()
        for pattern in DOC_GLOBS:
            for f in sorted(root.glob(pattern)):
                if f in seen or not f.is_file():
                    continue
                seen.add(f)
                rel = f.relative_to(root).as_posix()
                yield SourceRef(source_id=f"audit:{root.name}:{rel}", adapter=ADAPTER, path=str(f),
                                meta={"root": str(root), "rel": rel})


def parse_doc(path: Path) -> ParseResult:
    """Split a Markdown report into heading sections. A section's id includes its content
    hash, so an edited report adds the new version and keeps the old one citable."""
    text = path.read_text(encoding="utf-8", errors="replace")
    res = ParseResult(turns=[], end_offset=len(text.encode("utf-8")))
    m = re.search(r"^#\s+(.+)$", text, re.M)
    res.title = m.group(1).strip() if m else path.stem
    marks = [h.start() for h in _HEADING.finditer(text)] or [0]
    if marks[0] != 0:
        marks.insert(0, 0)
    marks.append(len(text))
    for n, (a, b) in enumerate(zip(marks, marks[1:])):
        sec = text[a:b].strip()
        if not sec:
            continue
        if len(sec) > _MAX_SECTION:
            sec = sec[:_MAX_SECTION] + "\n…[truncated]"
        h = hashlib.sha1(sec.encode("utf-8")).hexdigest()[:8]
        res.turns.append(Turn(msg_id=f"s{n}-{h}", speaker="prior_audit", kind="doc", text=sec))
    return res


# ── Records ──────────────────────────────────────────────────


def build_records(root: Path, project: str) -> tuple[list[Record], list[tuple[str, str, str]], list[dict[str, Any]]]:
    """(records, links, events) derived deterministically from one audit root."""
    from navig.recall.vault import make_event  # noqa: PLC0415

    records: list[Record] = []
    links: list[tuple[str, str, str]] = []
    events: list[dict[str, Any]] = []
    tag = root.name
    feature_ids: dict[str, str] = {}

    reg_path, items, updated = _load_registry(root)
    reg_src = f"audit:{tag}:{reg_path.relative_to(root).as_posix()}" if reg_path else ""
    for it in items:
        rec, evs = _feature_record(it, tag, project, reg_src, updated)
        if rec is None:
            continue
        feature_ids[rec.ext_id] = rec.id
        records.append(rec)
        events.append(make_event(rec.id, "imported", updated or "", {"from": reg_src, "status": it.get("status")}))
        for ev in evs:
            events.append(make_event(rec.id, ev["kind"], ev.get("ts") or updated or "", ev["data"]))

    for rec in _decision_records(root, tag, project):
        records.append(rec)
        events.append(make_event(rec.id, "imported", "", {"from": rec.evidence[0].source_id if rec.evidence else ""}))
        if rec.status == "superseded":
            events.append(make_event(rec.id, "reversal", rec.attrs.get("date", ""),
                                     {"status_today": rec.attrs.get("status_today", "")}))
        for fid in _BACKTICK.findall(rec.attrs.get("status_today", "") + " " + rec.summary):
            if fid in feature_ids:
                links.append((rec.id, feature_ids[fid], "related"))

    records.extend(_idea_records(root, tag, project))
    records.extend(_conflict_records(root, tag, project))

    rel = root / "relationships.json"
    if rel.exists():
        try:
            data = json.loads(rel.read_text(encoding="utf-8"))
            for e in data.get("edges") or []:
                if isinstance(e, dict) and e.get("from") and e.get("to"):
                    links.append((f"cc:{e['from']}", f"cc:{e['to']}", str(e.get("type") or "related")))
        except (OSError, ValueError):
            pass
    return records, links, events


def _load_registry(root: Path) -> tuple[Path | None, list[dict[str, Any]], str]:
    for rel in ("registry/features.json", "feature-registry.json"):
        p = root / rel
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return p, [], ""
        items = data.get("features", data) if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = list(items.values())
        updated = str(data.get("updatedAt") or "") if isinstance(data, dict) else ""
        return p, [i for i in items if isinstance(i, dict)], updated
    return None, [], ""


def _feature_record(it: dict[str, Any], tag: str, project: str, reg_src: str,
                    updated: str) -> tuple[Record | None, list[dict[str, Any]]]:
    fid = str(it.get("featureId") or it.get("id") or "").strip()
    title = str(it.get("canonicalName") or it.get("title") or fid).strip()
    if not fid or not title:
        return None, []
    status_key = _norm_status(str(it.get("status") or ""))
    status, impl, tags = _STATUS_MAP[status_key]
    rtype = "idea" if status_key == "IDEA ONLY" else "feature"
    task = it.get("completionTask")
    task_text = str(task.get("text") or "") if isinstance(task, dict) else str(task or "")
    summary = task_text or str(it.get("requirement") or "")
    body_lines: list[str] = []
    for label, key in (("Current reality", "current_reality"), ("Missing", "missing"), ("Code", "code"),
                       ("Priority", "priority")):
        if it.get(key):
            body_lines.append(f"**{label}:** {it[key]}")
    ev_raw = it.get("evidence")
    ev_list = [str(e) for e in ev_raw] if isinstance(ev_raw, list) else ([str(ev_raw)] if ev_raw else [])
    if ev_list:
        body_lines.append("**Audit evidence:**\n" + "\n".join(f"- {e}" for e in ev_list))
    files = it.get("touchedFiles") or []
    if files:
        body_lines.append("**Touched files:** " + ", ".join(f"`{f}`" for f in files[:30]))
    evidence = [Evidence(source_id=reg_src, msg_id=fid, speaker="prior_audit",
                         excerpt=(ev_list[0] if ev_list else f"status {it.get('status')}")[:600], ts=updated)]
    sessions = it.get("sessions")
    sids: list[str] = []
    if isinstance(sessions, list):
        for s in sessions:
            if isinstance(s, dict) and s.get("id"):
                sids.append(str(s["id"]))
                evidence.append(Evidence(source_id=f"cc:{s['id']}", msg_id="", speaker="prior_audit",
                                         excerpt=f"{s.get('role') or 'session'} (audit ref {s.get('auditRef') or '—'})"))
    elif it.get("session_id"):
        sids.append(str(it["session_id"]))
        evidence.append(Evidence(source_id=f"cc:{it['session_id']}", msg_id="", speaker="prior_audit",
                                 excerpt=str(it.get("sessions") or "originating session")[:300]))
    conf = _CONFIDENCE.get(str(it.get("confidence") or "").lower(), 0.6)
    rec = Record(
        id=record_id("audit", tag, fid), type=rtype, title=title[:200], summary=summary,
        body="\n\n".join(body_lines), project=project, status=status, status_basis="audit",
        impl=impl, confidence=conf, canonical_key=canonical_key(title),
        aliases=[str(a) for a in it.get("aliases") or []], tags=["audit", *tags], ext_id=fid,
        created=updated, updated=updated,
        attrs={"audit_status": str(it.get("status") or ""), "audit": tag, "sessions": sids,
               "touched_files": list(files)[:50], "plans": list(it.get("plans") or [])},
        evidence=evidence,
    )
    evs = [{"kind": "merged", "data": m if isinstance(m, dict) else {"note": str(m)}}
           for m in it.get("mergeHistory") or []]
    return rec, evs


def _rows_with(root: Path, rels: tuple[str, ...], column: str) -> Iterator[tuple[str, dict[str, str], str]]:
    for rel in rels:
        p = root / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for tbl in find_tables(text):
            cols = [c.strip().casefold() for c in tbl.columns]
            if column not in cols:
                continue
            for row in tbl.rows:
                d = {cols[i]: (row[i] if i < len(row) else "") for i in range(len(cols))}
                yield rel, d, tbl.caption


def _plain(text: str) -> str:
    return re.sub(r"\*\*|__", "", text).strip()


def _decision_records(root: Path, tag: str, project: str) -> list[Record]:
    out: list[Record] = []
    for rel, row, _cap in _rows_with(root, ("decisions.md",), "decision"):
        decision = _plain(row.get("decision", ""))
        if not decision:
            continue
        today = row.get("status today", "") or row.get("status", "")
        low = today.casefold()
        if "reversed" in low or "superseded" in low:
            status = "superseded"
        elif "rejected" in low:
            status = "rejected"
        elif "in force" in low:
            status = "confirmed"
        else:
            status = "unresolved"
        sids = sorted(set(_SID8.findall(row.get("session", ""))))
        num = row.get("#", "")
        src = f"audit:{tag}:{rel}"
        rec = Record(
            id=record_id("audit", tag, "decision", num or decision), type="decision", title=decision[:160],
            summary=decision, body=f"**Status today:** {_plain(today)}", project=project, status=status,
            status_basis="audit", confidence=0.7, canonical_key=canonical_key(decision),
            tags=["audit", "decision"], ext_id=f"decision-{num}" if num else "",
            attrs={"date": row.get("date", ""), "sessions": sids, "status_today": today, "audit": tag},
            evidence=[Evidence(source_id=src, msg_id=f"row{num}", speaker="prior_audit",
                               excerpt=f"{decision} — {_plain(today)}"[:600])]
                     + [Evidence(source_id=f"cc-prefix:{s}", msg_id="", speaker="prior_audit",
                                 excerpt="session cited by the audit") for s in sids],
        )
        out.append(rec)
    return out


def _idea_records(root: Path, tag: str, project: str) -> list[Record]:
    out: list[Record] = []
    seen: set[str] = set()
    for rel, row, cap in _rows_with(root, ("recovered-ideas.md", "reports/ideas-backlog.md"), "idea"):
        idea = _plain(row.get("idea", ""))
        key = canonical_key(idea)
        if not idea or key in seen:
            continue
        seen.add(key)
        rec_txt = (row.get("recommendation") or "").casefold()
        status = "rejected" if any(w in rec_txt for w in ("reject", "drop")) else "proposed"
        details = [f"**{k.title()}:** {_plain(v)}" for k, v in row.items() if k not in ("idea", "rank") and v]
        rank = row.get("rank", "")
        out.append(Record(
            id=record_id("audit", tag, "idea", key), type="idea", title=idea[:160], summary=idea,
            body="\n\n".join(details), project=project, status=status, status_basis="audit",
            impl="not_found", confidence=0.7, canonical_key=key,
            tags=["audit", "recovered-idea"] + (["top10"] if rank and rel == "recovered-ideas.md" else []),
            attrs={"rank": rank, "section": cap, "audit": tag, **{k: v for k, v in row.items() if k != "idea"}},
            evidence=[Evidence(source_id=f"audit:{tag}:{rel}", msg_id=f"idea-{key[:40]}", speaker="prior_audit",
                               excerpt=idea[:600])],
        ))
    return out


def _conflict_records(root: Path, tag: str, project: str) -> list[Record]:
    p = root / "conflicts.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    matches = list(_CONFLICT.finditer(text))
    out: list[Record] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[m.end():end].strip()
        cid, title = m.group(1), m.group(2).strip()
        out.append(Record(
            id=record_id("audit", tag, "conflict", cid), type="question", title=f"{cid} · {title}"[:160],
            summary=title, body=section[:_MAX_SECTION], project=project, status="unresolved",
            status_basis="audit", confidence=0.7, canonical_key=canonical_key(title),
            tags=["audit", "conflict"], ext_id=cid, attrs={"audit": tag},
            evidence=[Evidence(source_id=f"audit:{tag}:conflicts.md", msg_id=cid, speaker="prior_audit",
                               excerpt=section[:600])],
        ))
    return out

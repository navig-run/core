"""Shared Recall queries — one implementation behind every surface that reads the bank.

``navig recall`` (CLI), the MCP bundle (``navig mcp serve --only recall``, read by Claude
Code) and the deck routes (NAVIG OS → Knowledge → Recall) all call these functions, so a
search, an "asked before?" answer or a view means the same thing everywhere.

Two rules every caller inherits:

* **Citations travel with every result.** A turn carries ``cite`` (source id, message id,
  timestamp, speaker, and whether that speaker is the owner); a record carries its
  ``status`` together with ``status_basis`` — ``user`` is the owner's own decision,
  ``audit`` an imported audit, ``assistant`` model output, ``verify`` a verification step.
* **``for_model=True`` drops what may not leave the machine.** A shared chat whose level is
  below ``agent`` (``sources.chats.may_leave_machine``) is readable locally but must never
  reach a model, so its turns, its excerpts and every record it supports are withheld from
  the MCP surface. Local surfaces (CLI, OS) pass ``for_model=False``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from navig.recall.model import USER_SPEAKERS, Record
from navig.recall.projects import SHARED

if TYPE_CHECKING:
    from navig.recall.store import RecallStore


@dataclass(frozen=True)
class ViewSpec:
    rtype: str
    status: str | None = None
    forgotten: bool = False


#: The named views, shared by the CLI, the MCP tools and the OS rail.
VIEWS: dict[str, ViewSpec] = {
    "forgotten_ideas": ViewSpec("idea", forgotten=True),
    "open_questions": ViewSpec("question", status="unresolved"),
    "decisions": ViewSpec("decision"),
    "plans": ViewSpec("plan"),
    "tables": ViewSpec("table"),
    "findings": ViewSpec("finding"),
    "features": ViewSpec("feature"),
    "ideas": ViewSpec("idea"),
    "questions": ViewSpec("question"),
}

#: impl states that mean "nobody has shown this was built".
UNBUILT_IMPL = ("not_found", "unknown")


@dataclass(frozen=True)
class Scope:
    """Which projects a read covers. ``projects=None`` means every project."""

    projects: tuple[str, ...] | None = None

    @classmethod
    def everything(cls) -> Scope:
        return cls(None)

    @classmethod
    def only(cls, project: str | None) -> Scope:
        """The CLI's ``--project``: exactly that project, or everything when unset."""
        return cls((project,)) if project else cls(None)

    def to_dict(self) -> dict[str, Any]:
        return {"projects": list(self.projects) if self.projects is not None else "all"}


def scope_for(project: str | None = None, *, all_projects: bool = False, cwd: str | None = None) -> Scope:
    """The model-facing default: one project plus ``shared`` (consent tier 4).

    ``project`` names it explicitly; otherwise it is the project *cwd* belongs to.
    ``all_projects`` widens the read to every project and must be asked for.
    """
    if all_projects:
        return Scope.everything()
    pid = (project or "").strip()
    if not pid:
        from navig.recall.projects import project_for_path  # noqa: PLC0415

        pid = project_for_path(cwd).id if cwd else SHARED
    return Scope(tuple(dict.fromkeys((pid, SHARED))))


def _store(store: RecallStore | None) -> RecallStore:
    if store is not None:
        return store
    from navig.recall.store import get_recall_store  # noqa: PLC0415

    return get_recall_store()


def is_enabled() -> bool:
    """``recall.read_local`` — may Recall read local transcripts at all."""
    from navig.config import get_config_manager  # noqa: PLC0415
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    return coerce_bool(get_config_manager().get("recall.read_local"), default=False)


# ── Citations and the model filter ────────────────────────────


def cite(source_id: str, msg_id: str, ts: str = "", speaker: str = "") -> dict[str, Any]:
    return {"source_id": source_id, "msg_id": msg_id, "ts": ts or "", "speaker": speaker,
            "owner": speaker in USER_SPEAKERS, "ref": f"{source_id}#{msg_id}"}


def private_sources(store: RecallStore) -> frozenset[str]:
    """Sources whose text must not be sent to a model (chats shared below ``agent``)."""
    from navig.recall.sources.chats import ADAPTER, may_leave_machine  # noqa: PLC0415

    return frozenset(s["source_id"] for s in store.list_sources(adapter=ADAPTER)
                     if not may_leave_machine(s.get("meta")))


def _drop_private_records(store: RecallStore, rows: list[Any], private: frozenset[str]) -> list[Any]:
    """Withhold every record with ANY evidence from a private source (most private wins)."""
    if not private or not rows:
        return rows
    ids = [r.id if isinstance(r, Record) else r["id"] for r in rows]
    srcs = store.evidence_sources(ids)
    return [r for r, rid in zip(rows, ids, strict=True) if not (srcs.get(rid, set()) & private)]


def _with_cite(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "cite": cite(row["source_id"], row["msg_id"], row.get("ts", ""), row.get("speaker", ""))}


def record_row(rec: Record, *, with_body: bool = False) -> dict[str, Any]:
    """A record as a list row (no evidence; the body only when asked)."""
    d = asdict(rec)
    d.pop("evidence", None)
    if not with_body:
        d.pop("body", None)
    return d


# ── Queries ───────────────────────────────────────────────────


def search(query: str, *, scope: Scope | None = None, rtype: str | None = None, status: str | None = None,
           raw: bool = False, limit: int = 15, for_model: bool = False,
           store: RecallStore | None = None) -> dict[str, Any]:
    """Records matching *query*; the conversation turns too when ``raw`` or nothing matched."""
    st = _store(store)
    projects = (scope or Scope()).projects
    private = private_sources(st) if for_model else frozenset()
    recs = [] if raw else st.search_records(query, rtype=rtype, status=status, limit=limit, projects=projects)
    recs = _drop_private_records(st, recs, private)
    turns: list[dict[str, Any]] = []
    if raw or not recs:
        turns = [_with_cite(t) for t in st.search_turns(query, limit=limit, projects=projects)
                 if t["source_id"] not in private]
    return {"records": recs, "turns": turns}


def ask(question: str, *, scope: Scope | None = None, limit: int = 5, answer_chars: int | None = None,
        for_model: bool = False, store: RecallStore | None = None) -> dict[str, Any]:
    """Have I asked this before? Earlier owner questions, the reply that followed, and decisions."""
    st = _store(store)
    projects = (scope or Scope()).projects
    private = private_sources(st) if for_model else frozenset()
    asked = []
    for h in st.search_turns(question, speakers=["user_typed"], limit=limit, projects=projects):
        if h["source_id"] in private:
            continue
        nxt = st.next_turn(h["source_id"], int(h["seq"]), speaker="assistant")
        if nxt is not None:
            nxt = {**nxt, "cite": cite(h["source_id"], nxt["msg_id"], nxt.get("ts", ""), nxt["speaker"])}
            if answer_chars and len(nxt["text"]) > answer_chars:
                nxt["text"] = nxt["text"][: answer_chars - 1] + "…"
                nxt["truncated"] = True
        asked.append({**_with_cite(h), "answer": nxt})
    decisions = st.search_records(question, rtype="decision", limit=limit, projects=projects)
    return {"asked": asked, "decisions": _drop_private_records(st, decisions, private)}


def view(name: str, *, scope: Scope | None = None, status: str | None = None, limit: int = 30,
         for_model: bool = False, store: RecallStore | None = None) -> list[Record]:
    """One named view (``VIEWS``). ``status`` overrides the view's default status filter."""
    spec = VIEWS.get(name)
    if spec is None:
        raise ValueError(f"unknown view '{name}' (use one of: {', '.join(VIEWS)})")
    st = _store(store)
    projects = (scope or Scope()).projects
    if spec.forgotten:
        recs = st.list_records(rtype=spec.rtype, status=status, limit=limit, projects=projects,
                               impls=UNBUILT_IMPL, exclude_statuses=("rejected",), oldest_first=True)
    else:
        recs = st.list_records(rtype=spec.rtype, status=status or spec.status, limit=limit, projects=projects)
    if for_model:
        recs = _drop_private_records(st, recs, private_sources(st))
    return recs


def record_detail(record_id: str, *, evidence_limit: int | None = None, for_model: bool = False,
                  store: RecallStore | None = None) -> dict[str, Any] | None:
    """A record with its evidence (each cited), timeline, related records and vault path.

    ``None`` when the record does not exist — or, for a model, when it rests on a source
    that may not leave the machine (indistinguishable on purpose).
    """
    st = _store(store)
    rec = st.get_record(record_id)
    if rec is None:
        return None
    if for_model and {e.source_id for e in rec.evidence} & private_sources(st):
        return None
    evidence = [{**e.to_dict(), "cite": cite(e.source_id, e.msg_id, e.ts, e.speaker),
                 "link": source_link(st, e.source_id, e.msg_id)} for e in rec.evidence]
    total = len(evidence)
    if evidence_limit is not None:
        evidence = evidence[:evidence_limit]
    related = st.related(record_id)
    if for_model and related:
        visible = {r["id"] for r in _drop_private_records(
            st, [{"id": r["other"]} for r in related], private_sources(st))}
        related = [r for r in related if r["other"] in visible]
    return {"record": record_row(rec, with_body=True), "evidence": evidence, "evidence_total": total,
            "events": st.events_for(record_id), "related": related, "path": st.record_path(record_id)}


def excerpt(source_id: str, msg_id: str, *, before: int = 3, after: int = 3, for_model: bool = False,
            store: RecallStore | None = None) -> dict[str, Any] | None:
    """The turns around one cited message. ``None`` when it is missing (or private, for a model)."""
    st = _store(store)
    if for_model and source_id in private_sources(st):
        return None
    rows = st.turn_window(source_id, msg_id, before=max(0, before), after=max(0, after))
    if not rows:
        return None
    src = st.get_source(source_id) or {}
    turns = [{**r, "focus": r["msg_id"] == msg_id, "owner": r["speaker"] in USER_SPEAKERS} for r in rows]
    return {"source": {"source_id": source_id, "adapter": src.get("adapter", ""), "project": src.get("project", ""),
                       "title": src.get("title", ""), "link": source_link(st, source_id, msg_id)},
            "cite": cite(source_id, msg_id, next((r["ts"] for r in rows if r["msg_id"] == msg_id), ""),
                         next((r["speaker"] for r in rows if r["msg_id"] == msg_id), "")),
            "turns": turns}


def source_link(st: RecallStore, source_id: str, msg_id: str) -> str:
    """A link that opens the exact message, for sources that have one (shared web chats)."""
    if not source_id.startswith("chats:"):
        return ""
    src = st.get_source(source_id) or {}
    url = (src.get("meta") or {}).get("url") or ""
    return f"{url}#navig-msg={msg_id}" if url else ""


def status(store: RecallStore | None = None) -> dict[str, Any]:
    """What is in the bank, whether reading is enabled, where it lives, and the projects."""
    from navig.recall.paths import recall_root  # noqa: PLC0415

    st = _store(store)
    s = st.stats()
    s["enabled"] = is_enabled()
    s["root"] = str(recall_root())
    s["projects"] = st.project_counts()
    return s


__all__ = ["UNBUILT_IMPL", "VIEWS", "Scope", "ViewSpec", "ask", "cite", "excerpt", "is_enabled",
           "private_sources", "record_detail", "record_row", "scope_for", "search", "source_link",
           "status", "view"]

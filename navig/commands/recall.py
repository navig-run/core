"""navig recall — the knowledge bank built from your AI conversations.

Reads Claude Code and Codex transcripts plus earlier session audits (only after
``navig recall enable``), keeps a redacted archive, and derives cited records: decisions you
answered, plans and their verdicts, tables, agent findings, audited features and ideas.
Everything stays on this machine.
"""

from __future__ import annotations

from typing import Any

import typer

from navig import console_helper as ch

recall_app = typer.Typer(
    name="recall",
    help="Recall — search decisions, plans, ideas and tables from past AI conversations.",
    no_args_is_help=True,
)

_TYPES = ("idea", "decision", "term", "plan", "table", "question", "finding", "risk", "architecture", "lore",
          "feature", "task", "snippet")
_ADAPTER_ALIASES = {"claude": "claude_code", "claude-code": "claude_code", "cc": "claude_code",
                    "codex": "codex", "audit": "session_audit", "session-audit": "session_audit",
                    "chats": "chats", "chatgpt": "chats", "chat-library": "chats",
                    "capture": "capture", "web": "capture", "dock": "capture",
                    "notes": "notes", "web-notes": "notes"}


# ── helpers ───────────────────────────────────────────────────


def _enabled() -> bool:
    from navig.recall.query import is_enabled  # noqa: PLC0415

    return is_enabled()


def _require_enabled() -> None:
    if not _enabled():
        ch.error("Recall has not been enabled on this machine.",
                 "Turn it on (reads local transcripts, nothing leaves the machine): navig recall enable")
        raise typer.Exit(1)


def _store():
    from navig.recall.store import get_recall_store  # noqa: PLC0415

    return get_recall_store()


def _here_project() -> str:
    from navig.platform.paths import invocation_cwd  # noqa: PLC0415
    from navig.recall.projects import project_for_path  # noqa: PLC0415

    return project_for_path(str(invocation_cwd())).id


def _project(project: str | None, here: bool) -> str | None:
    if here:
        return _here_project()
    return project or None


def _scope(project: str | None, here: bool):
    from navig.recall.query import Scope  # noqa: PLC0415

    return Scope.only(_project(project, here))


def _check_type(rtype: str | None) -> None:
    if rtype and rtype not in _TYPES:
        ch.error(f"Unknown record type '{rtype}'.", f"Use one of: {', '.join(_TYPES)}")
        raise typer.Exit(2)


def _chip(status: str, basis: str) -> str:
    colour = {"approved": "green", "confirmed": "green", "implemented": "cyan", "verified": "cyan",
              "rejected": "red", "superseded": "dim", "unresolved": "yellow"}.get(status, "white")
    return f"[{colour}]{status}[/{colour}][dim]·{basis}[/dim]"


def _snip(text: str | None, n: int = 110) -> str:
    s = " ".join(str(text or "").split())
    s = s.replace("[", "\\[")
    return s if len(s) <= n else s[: n - 1] + "…"


def _table(*cols: tuple[str, dict[str, Any]]):
    from navig.console_helper import Table  # noqa: PLC0415

    t = Table(box=None, show_header=True, padding=(0, 2))
    for name, kw in cols:
        t.add_column(name, **kw)
    return t


# ── enable / status ───────────────────────────────────────────


@recall_app.command("enable")
def enable_cmd(
    remote: bool = typer.Option(False, "--remote",
                                help="Also allow `navig recall extract` to send redacted prose to the configured model."),
    auto: bool = typer.Option(False, "--auto",
                              help="Also let the daemon read new conversations every 5 minutes "
                                   "(monitor recall_ingest)."),
) -> None:
    """Allow Recall to read local transcripts (Claude Code, Codex, session audits)."""
    from navig.config import get_config_manager  # noqa: PLC0415
    from navig.recall.paths import recall_root  # noqa: PLC0415

    cm = get_config_manager()
    cm.set_global("recall.read_local", True)
    ch.success("Recall enabled — local reading.")
    ch.dim(f"Reads ~/.claude/projects, ~/.codex/sessions and ~/.claude/session-audit*; "
           f"writes only to {recall_root()}. Secrets are redacted before anything is stored.")
    if remote:
        cm.set_global("recall.extract.remote", True)
        provider = cm.get("recall.extract.provider") or "mimo"
        model = cm.get("recall.extract.model") or "mimo-v2.6-flash"
        ch.success(f"Extraction allowed: secret- and PII-redacted conversation prose may be sent to {provider}:{model} "
                   "when you run navig recall extract (budgeted; --dry-run shows the cost first).")
    else:
        ch.dim("Nothing is sent to any AI provider (model extraction needs: navig recall enable --remote).")
    if auto:
        cm.set_global("monitors.recall_ingest.enabled", True)
        ch.success("Automatic reading on — the daemon reads new conversations every 5 minutes.")
        ch.dim("It starts with the next daemon start (navig service restart), or switch it on now in "
               "NAVIG OS → Settings → Notifications → Monitors → Recall reading.")
    ch.dim("Next: navig recall ingest")

@recall_app.command("disable")
def disable_cmd() -> None:
    """Stop Recall from reading transcripts (the existing bank is kept)."""
    from navig.config import get_config_manager  # noqa: PLC0415

    cm = get_config_manager()
    cm.set_global("recall.read_local", False)
    cm.set_global("recall.extract.remote", False)
    ch.success("Recall disabled. The bank on disk is untouched; delete it with the folder shown by: navig recall status")


@recall_app.command("status")
def status_cmd(as_json: bool = typer.Option(False, "--json", help="Machine-readable output.")) -> None:
    """What is in the bank, where it lives, and whether reading is enabled."""
    from navig.recall import query  # noqa: PLC0415

    s = query.status(_store())
    if as_json:
        ch.emit_json(s)
        return
    t = _table(("", {"style": "dim", "no_wrap": True}), ("", {}))
    t.show_header = False
    t.add_row("Reading", "[green]● enabled[/green]" if s["enabled"] else "[dim]○ disabled[/dim]")
    t.add_row("Sources", f"{s['sources']}  " + " · ".join(f"{k} {v}" for k, v in sorted(s["sources_by_adapter"].items())))
    t.add_row("Turns", str(s["turns"]))
    t.add_row("Records", f"{s['records']}  " + " · ".join(f"{k} {v}" for k, v in sorted(s["records_by_type"].items())))
    t.add_row("Links · events", f"{s['links']} · {s['events']}")
    if s["source_errors"]:
        t.add_row("Errors", f"[red]✗ {s['source_errors']} source(s)[/red] — navig recall sources --errors")
    t.add_row("Search", "FTS5" if s["fts"] else "[yellow]LIKE fallback[/yellow]")
    ext = _store().extract_summary()
    if ext:
        ok = ext.get("ok", {})
        spent = sum(v["cost_usd"] for v in ext.values())
        t.add_row("Extraction", f"{ok.get('runs', 0)} window(s) done · ${spent:.2f} spent"
                  + (f" · [yellow]{ext['failed']['runs']} failed[/yellow]" if "failed" in ext else ""))
    n_review = len(_store().list_review(limit=1000))
    if n_review:
        t.add_row("Review", f"{n_review} item(s) — navig recall review")
    t.add_row("Folder", s["root"])
    ch.console.print(t)
    ch.dim("next: navig recall search <words>  ·  navig recall decisions  ·  navig recall ingest")


@recall_app.command("sources")
def sources_cmd(
    adapter: str = typer.Option(None, "--adapter", "-a", help="claude_code · codex · session_audit · chats · capture"),
    project: str = typer.Option(None, "--project", "-p", help="Only this project."),
    errors: bool = typer.Option(False, "--errors", help="Only sources whose last read failed."),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Conversation sources Recall has read, newest first."""
    rows = _store().list_sources(adapter=_ADAPTER_ALIASES.get(adapter or "", adapter), project=project)
    if errors:
        rows = [r for r in rows if r["error"]]
    rows = rows[:limit]
    if as_json:
        ch.emit_json({"sources": rows, "count": len(rows)})
        return
    if not rows:
        ch.info("No sources yet.", "Read them: navig recall ingest")
        return
    t = _table(("Source", {"no_wrap": True, "style": "bold"}), ("Project", {"no_wrap": True}),
               ("Turns", {"no_wrap": True, "justify": "right"}), ("Last", {"no_wrap": True, "style": "dim"}),
               ("Title", {}))
    for r in rows:
        title = _snip(r["title"], 70) if not r["error"] else f"[red]✗ {_snip(r['error'], 70)}[/red]"
        t.add_row(r["source_id"][:40], r["project"], str(r["turns"]), (r["last_ts"] or "")[:10] or "—", title)
    ch.console.print(t)
    ch.dim("open a turn: navig recall excerpt <source> <msg-id>")


# ── ingest / reindex ─────────────────────────────────────────


@recall_app.command("ingest")
def ingest_cmd(
    source: list[str] = typer.Option(None, "--source", "-s",
                                     help="claude-code · codex · audit · chats · capture · notes (repeatable; default all)"),
    project: str = typer.Option(None, "--project", "-p", help="Only sources that belong to this project."),
    limit: int = typer.Option(None, "--limit", "-n", help="At most N transcripts (for a quick trial)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Read new or changed conversations into the bank (incremental; safe to re-run)."""
    _require_enabled()
    from navig.recall.ingest import Ingestor  # noqa: PLC0415
    from navig.recall.sources import ADAPTERS  # noqa: PLC0415

    adapters = [_ADAPTER_ALIASES.get(s, s) for s in source] if source else list(ADAPTERS)
    bad = [a for a in adapters if a not in ADAPTERS]
    if bad:
        ch.error(f"Unknown source: {', '.join(bad)}", "Use claude-code, codex, audit, chats, capture or notes.")
        raise typer.Exit(2)

    def _progress(i: int, n: int, sid: str) -> None:
        if not as_json and (i == n or i % 50 == 0):
            ch.dim(f"  {i}/{n}  {sid[:48]}")

    rep = Ingestor().run(adapters, project=project, limit=limit, progress=_progress)
    if as_json:
        ch.emit_json(rep.to_dict())
    else:
        ch.success(f"Read {rep.sources_read} source(s) · {rep.turns_added} new turn(s) · "
                   f"{rep.records_created} new record(s) · {rep.records_updated} updated")
        ch.dim(f"unchanged {rep.unchanged} · rewritten {rep.rewritten} · missing {rep.missing} · "
               f"forgotten (no longer shared) {rep.forgotten} · unreadable lines {rep.bad_lines}")
        for sid, err in rep.errors[:10]:
            ch.warning(f"{sid}: {err}")
    if rep.errors:
        raise typer.Exit(1)


@recall_app.command("reindex")
def reindex_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """Rebuild the search index from the archive and the vault (both are left untouched)."""
    from navig.recall.ingest import Ingestor  # noqa: PLC0415

    counts = Ingestor().reindex()
    if as_json:
        ch.emit_json(counts)
        return
    ch.success("Index rebuilt: " + " · ".join(f"{v} {k}" for k, v in counts.items()))


# ── search / ask / show / excerpt ────────────────────────────


@recall_app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help='Words to find; "quoted text" matches an exact phrase.'),
    project: str = typer.Option(None, "--project", "-p", help="Only this project."),
    here: bool = typer.Option(False, "--here", help="Only the project you are standing in."),
    rtype: str = typer.Option(None, "--type", "-t", help="Only this record type."),
    status: str = typer.Option(None, "--status", help="Only this status."),
    raw: bool = typer.Option(False, "--raw", help="Search the conversations themselves, not records."),
    limit: int = typer.Option(15, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Find records (and, with --raw, the conversation turns) that match."""
    from navig.recall import query as rq  # noqa: PLC0415

    _check_type(rtype)
    found = rq.search(query, scope=_scope(project, here), rtype=rtype, status=status, raw=raw, limit=limit,
                      store=_store())
    recs, turns = found["records"], found["turns"]
    if as_json:
        ch.emit_json({"records": recs, "turns": turns})
        return
    if recs:
        t = _table(("ID", {"no_wrap": True, "style": "bold"}), ("Type", {"no_wrap": True}),
                   ("Status", {"no_wrap": True}), ("Project", {"no_wrap": True, "style": "dim"}), ("Match", {}))
        for r in recs:
            t.add_row(r["id"], r["type"], _chip(r["status"], r["status_basis"]), r["project"],
                      f"[bold]{_snip(r['title'], 70)}[/bold]\n[dim]{_snip(r['snip'], 140)}[/dim]")
        ch.console.print(t)
    if turns:
        if recs:
            ch.console.print()
        t = _table(("Source · msg", {"no_wrap": True, "style": "dim"}), ("Who", {"no_wrap": True}),
                   ("When", {"no_wrap": True, "style": "dim"}), ("Match", {}))
        for r in turns:
            t.add_row(f"{r['source_id'][:22]} {r['msg_id'][:14]}", r["speaker"], (r["ts"] or "")[:10] or "—",
                      _snip(r["snip"], 150))
        ch.console.print(t)
    if not recs and not turns:
        ch.info(f"Nothing matches '{query}'.", "Try fewer words, or: navig recall search --raw <words>")
        return
    ch.dim("open a record: navig recall show <id>  ·  a turn: navig recall excerpt <source> <msg-id>")


@recall_app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="A question you may have asked before."),
    project: str = typer.Option(None, "--project", "-p"),
    here: bool = typer.Option(False, "--here", help="Only the project you are standing in."),
    limit: int = typer.Option(5, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Have I asked this before? Earlier questions, the answers that followed, and decisions."""
    from navig.recall import query as rq  # noqa: PLC0415

    found = rq.ask(question, scope=_scope(project, here), limit=limit, store=_store())
    answers, decisions = found["asked"], found["decisions"]
    if as_json:
        ch.emit_json({"asked": answers, "decisions": decisions})
        return
    if not answers and not decisions:
        ch.info("No earlier question or decision matches.", "Broaden it: navig recall search --raw <words>")
        return
    for a in answers:
        ch.console.print(f"[bold]You[/bold] [dim]{(a['ts'] or '')[:10]} · {a['source_id']} {a['msg_id']}[/dim]")
        ch.console.print(f"  {_snip(a['snip'], 200)}")
        if a["answer"]:
            ch.console.print(f"  [cyan]→[/cyan] {_snip(a['answer']['text'], 400)}")
        ch.console.print()
    for d in decisions:
        ch.console.print(f"[green]●[/green] [bold]{_snip(d['title'], 100)}[/bold] [dim]{d['id']}[/dim]")
        ch.console.print(f"  {_snip(d['snip'], 200)}")
    ch.dim("full context: navig recall excerpt <source> <msg-id>")


@recall_app.command("show")
def show_cmd(
    record_id: str = typer.Argument(..., help="Record id (rk-…)."),
    full: bool = typer.Option(False, "--full", help="Print the whole body."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """One record: status, summary, body, evidence with citations, timeline, links."""
    from navig.recall import query as rq  # noqa: PLC0415
    from navig.recall.model import Evidence  # noqa: PLC0415

    detail = rq.record_detail(record_id, store=_store())
    if detail is None:
        ch.error(f"No record {record_id}.", "Find one: navig recall search <words>")
        raise typer.Exit(1)
    evidence = [Evidence.from_dict(e) for e in detail["evidence"]]
    events, related = detail["events"], detail["related"]
    if as_json:
        ch.emit_json({"record": {**detail["record"], "evidence": [e.to_dict() for e in evidence]},
                      "events": events, "related": related, "path": detail["path"]})
        return
    rec = detail["record"]
    ch.console.print(f"[bold]{_snip(rec['title'], 200)}[/bold]  [dim]{rec['id']}[/dim]")
    ch.console.print(f"{rec['type']} · {_chip(rec['status'], rec['status_basis'])} · endorsement {rec['endorsement']} · "
                     f"impl {rec['impl']} · confidence {rec['confidence']:.2f} · project {rec['project']}")
    if rec['summary']:
        ch.console.print(f"\n{_snip(rec['summary'], 600)}")
    if rec['body']:
        body = rec['body'] if full else rec['body'][:1500] + ("\n… (--full for the rest)" if len(rec['body']) > 1500 else "")
        ch.console.print()
        ch.console.print(body, markup=False, highlight=False)
    if evidence:
        ch.console.print("\n[bold]Evidence[/bold]")
        for e, rd in zip(evidence[:12], detail["evidence"][:12], strict=True):
            ch.console.print(f"  [dim]{e.speaker} · {(e.ts or '')[:10] or 'undated'} · {e.source_id}#{e.msg_id}[/dim]")
            ch.console.print(f"    {_snip(e.excerpt, 220)}")
            link = rd["link"]
            if link:
                ch.console.print(f"    [cyan]{link}[/cyan]", highlight=False)
        if len(evidence) > 12:
            ch.dim(f"  … {len(evidence) - 12} more (--json)")
    if events:
        ch.console.print("\n[bold]Timeline[/bold]")
        for ev in events[-15:]:
            ch.console.print(f"  [dim]{(ev['ts'] or '')[:10] or '—'}[/dim] {ev['kind']} {_snip(str(ev['data']), 120)}")
    if related:
        ch.console.print("\n[bold]Related[/bold]")
        for r in related[:15]:
            ch.console.print(f"  {r['rel']} {r['dir']} {r['other']} [dim]{_snip(r.get('title') or '', 80)}[/dim]")
    ch.dim(f"\nfile: {detail['path']}")


@recall_app.command("excerpt")
def excerpt_cmd(
    source_id: str = typer.Argument(..., help="Source id (e.g. cc:<session>)."),
    msg_id: str = typer.Argument(..., help="Message id within the source."),
    before: int = typer.Option(3, "--before", "-B"),
    after: int = typer.Option(3, "--after", "-A"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The original conversation around one turn — the citation behind a record."""
    from navig.recall import query as rq  # noqa: PLC0415

    found = rq.excerpt(source_id, msg_id, before=before, after=after, store=_store())
    rows = found["turns"] if found else []
    if as_json:
        ch.emit_json({"turns": rows})
        return
    if not rows:
        ch.error(f"No turn {msg_id} in {source_id}.", "List sources: navig recall sources")
        raise typer.Exit(1)
    for r in rows:
        mark = "[bold yellow]▶[/bold yellow]" if r["msg_id"] == msg_id else " "
        ch.console.print(f"{mark} [bold]{r['speaker']}[/bold] [dim]{(r['ts'] or '')[:19]} · {r['msg_id']}[/dim]")
        ch.console.print(r["text"][:3000], markup=False, highlight=False)
        ch.console.print()


def _source_link(st: Any, source_id: str, msg_id: str) -> str:
    """A link that opens the exact message, for sources that have one (shared web chats)."""
    from navig.recall.query import source_link  # noqa: PLC0415

    return source_link(st, source_id, msg_id)


# ── views ─────────────────────────────────────────────────────


def _view(rtype: str, project: str | None, here: bool, status: str | None, limit: int, as_json: bool,
          empty_hint: str) -> None:
    from navig.recall import query as rq  # noqa: PLC0415

    name = {"decision": "decisions", "plan": "plans", "idea": "ideas", "table": "tables", "finding": "findings",
            "feature": "features", "question": "questions"}[rtype]
    recs = rq.view(name, scope=_scope(project, here), status=status, limit=limit, store=_store())
    if as_json:
        ch.emit_json({"records": [rq.record_row(r) for r in recs], "count": len(recs)})
        return
    if not recs:
        ch.info(f"No {rtype} records yet.", empty_hint)
        return
    t = _table(("ID", {"no_wrap": True, "style": "bold"}), ("Status", {"no_wrap": True}),
               ("Impl", {"no_wrap": True, "style": "dim"}), ("Project", {"no_wrap": True, "style": "dim"}),
               ("When", {"no_wrap": True, "style": "dim"}), ("Title", {}))
    for r in recs:
        t.add_row(r.id, _chip(r.status, r.status_basis), r.impl, r.project, (r.updated or "")[:10] or "—",
                  _snip(r.title, 90))
    ch.console.print(t)
    ch.dim(f"{len(recs)} shown · open one: navig recall show <id>")



@recall_app.command("decisions")
def decisions_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
                  status: str = typer.Option(None, "--status"), limit: int = typer.Option(30, "--limit", "-n"),
                  as_json: bool = typer.Option(False, "--json")) -> None:
    """Decisions — your answers to structured questions, and audited decisions with reversals."""
    _view("decision", project, here, status, limit, as_json, "Read conversations first: navig recall ingest")


@recall_app.command("plans")
def plans_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
              status: str = typer.Option(None, "--status", help="approved · proposed …"),
              limit: int = typer.Option(30, "--limit", "-n"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Plans proposed in sessions, with the verdict you gave each one."""
    _view("plan", project, here, status, limit, as_json, "Read conversations first: navig recall ingest")


@recall_app.command("ideas")
def ideas_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
              status: str = typer.Option(None, "--status"), limit: int = typer.Option(30, "--limit", "-n"),
              as_json: bool = typer.Option(False, "--json")) -> None:
    """Ideas — including the audit's recovered, never-built ones."""
    _view("idea", project, here, status, limit, as_json, "Import the session audit: navig recall ingest -s audit")


@recall_app.command("tables")
def tables_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
               limit: int = typer.Option(30, "--limit", "-n"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Tables from past conversations, kept verbatim (show one to copy it)."""
    _view("table", project, here, None, limit, as_json, "Read conversations first: navig recall ingest")


@recall_app.command("findings")
def findings_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
                 limit: int = typer.Option(30, "--limit", "-n"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Research reports produced by agents during your sessions."""
    _view("finding", project, here, None, limit, as_json, "Read conversations first: navig recall ingest")


@recall_app.command("features")
def features_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
                 status: str = typer.Option(None, "--status", help="implemented · exploring · proposed …"),
                 limit: int = typer.Option(30, "--limit", "-n"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Features from the session audit, with their verified implementation state."""
    _view("feature", project, here, status, limit, as_json, "Import the session audit: navig recall ingest -s audit")


@recall_app.command("questions")
def questions_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
                  status: str = typer.Option("unresolved", "--status"), limit: int = typer.Option(30, "--limit", "-n"),
                  as_json: bool = typer.Option(False, "--json")) -> None:
    """Open questions and recorded conflicts."""
    _view("question", project, here, status, limit, as_json, "Import the session audit: navig recall ingest -s audit")


# ── sessions ↔ files, plans, commits ──────────────────────────


def _target(file: str | None, plan: str | None, commit: str | None) -> str | None:
    """The one target the operator named; a typed path is anchored where they typed it."""
    from navig.platform.paths import resolve_user_path  # noqa: PLC0415

    given = [v for v in (file, plan, commit) if v]
    if len(given) > 1:
        ch.error("Name one target.", "Use --file, --plan or --commit, not several.")
        raise typer.Exit(2)
    if commit:
        return f"commit:{commit.strip().lower()[:12]}"
    path = file or plan
    return str(resolve_user_path(path)) if path else None


_STATE = {"active": "[green]● active[/green]", "recent": "[cyan]◐ recent[/cyan]", "historical": "[dim]○ old[/dim]"}


@recall_app.command("sessions")
def sessions_cmd(
    file: str = typer.Option(None, "--file", "-f", help="Sessions that edited or were linked to this file."),
    plan: str = typer.Option(None, "--plan", help="Sessions that wrote, approved or were linked to this plan file."),
    commit: str = typer.Option(None, "--commit", help="Sessions that made this commit (short or full id)."),
    project: str = typer.Option(None, "--project", "-p", help="Only this project."),
    here: bool = typer.Option(False, "--here", help="Only the project of the current folder."),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Claude Code sessions linked to a file, a plan or a commit — or every session of a project."""
    from navig.recall.session_links import find_sessions  # noqa: PLC0415

    _require_enabled()
    target = _target(file, plan, commit)
    proj = _project(project, here)
    try:
        rows = find_sessions(_store(), target, project=proj, limit=limit)
    except ValueError as exc:
        ch.error(str(exc), "Name a file inside a registered project (navig space list), or a commit id.")
        raise typer.Exit(2) from None
    if as_json:
        ch.emit_json({"target": target, "project": proj, "sessions": [r.to_dict() for r in rows], "count": len(rows)})
        return
    if not rows:
        ch.info("No linked sessions.", "Read new transcripts first: navig recall ingest")
        return
    t = _table(("Session", {"no_wrap": True, "style": "bold"}), ("State", {"no_wrap": True}),
               ("Branch", {"no_wrap": True}), ("Last", {"no_wrap": True, "style": "dim"}),
               ("Why", {"no_wrap": True, "style": "dim"}), ("Title", {}))
    for r in rows:
        t.add_row(r.session_id[:8], _STATE.get(r.state, r.state), _snip(r.branch, 28) or "—",
                  r.last_ts[:16].replace("T", " ") or "—", ",".join(r.rels) or "project", _snip(r.title, 70) or "—")
    ch.console.print(t)
    ch.dim("resume one: claude --resume <full id>  ·  ids: --json  ·  link by hand: navig recall link <id> <file>")


@recall_app.command("link")
def link_cmd(
    session: str = typer.Argument(..., help="Claude Code session id (full UUID)."),
    target: str = typer.Argument(..., help="A file or plan path, or a commit id."),
) -> None:
    """Link a session to a file, plan or commit by hand (kept across reindex)."""
    from navig.recall import session_links as sl  # noqa: PLC0415
    from navig.recall.vault import Vault  # noqa: PLC0415

    _require_enabled()
    key = _target(None, None, target) if sl.is_commit_id(target) else _target(target, None, None)
    try:
        fresh = sl.link(_store(), Vault(), session, key or target)
    except (ValueError, LookupError) as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from None
    ch.success("Linked." if fresh else "Already linked.")


@recall_app.command("unlink")
def unlink_cmd(
    session: str = typer.Argument(..., help="Claude Code session id (full UUID)."),
    target: str = typer.Argument(..., help="A file or plan path, or a commit id."),
) -> None:
    """Remove every link between a session and a target (recorded, so reindex keeps it removed)."""
    from navig.recall import session_links as sl  # noqa: PLC0415
    from navig.recall.vault import Vault  # noqa: PLC0415

    _require_enabled()
    key = _target(None, None, target) if sl.is_commit_id(target) else _target(target, None, None)
    try:
        n = sl.unlink(_store(), Vault(), session, key or target)
    except (ValueError, LookupError) as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from None
    if not n:
        ch.warning("Nothing was linked between them.")
        raise typer.Exit(1)
    ch.success(f"Removed {n} link(s).")


# ── Provenance views: history · trace · changes · unfinished · conflicts ──────


def _anchor_target(target: str) -> str:
    """A typed relative path is anchored where the operator typed it; shas, session ids
    and already-prefixed keys pass through to ``query_key`` untouched."""
    from navig.platform.paths import resolve_user_path  # noqa: PLC0415
    from navig.recall.session_links import is_commit_id, is_session_id  # noqa: PLC0415

    t = (target or "").strip()
    if t.startswith(("file:", "plan:", "commit:")) or is_commit_id(t) or is_session_id(t):
        return t
    return str(resolve_user_path(t))


def _project_root(project: str | None) -> str | None:
    """The project's folder for git lookups (None → git verdicts degrade to inferred)."""
    if not project:
        return None
    try:
        from navig.projects.model import resolve_project  # noqa: PLC0415

        return str(resolve_project(project)[1])
    except Exception:  # noqa: BLE001 — a project without a folder must not break the view
        return None


@recall_app.command("history")
def history_cmd(
    target: str = typer.Argument(..., help="A file path, a plan file, or a commit id."),
    project: str = typer.Option(None, "--project", "-p"),
    here: bool = typer.Option(False, "--here"),
    keyword: str = typer.Option(None, "--keyword", "-k", help="Filter commits/records by substring."),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """What happened to one file, plan or commit: sessions, commits and records, merged."""
    from navig.recall import query as rq  # noqa: PLC0415

    _require_enabled()
    proj = _project(project, here)
    try:
        out = rq.history(_anchor_target(target), project=proj, limit=limit, keyword=keyword,
                         root=_project_root(proj), store=_store())
    except ValueError as exc:
        ch.error(str(exc), "Name a file inside a registered project (navig space list), or a commit id.")
        raise typer.Exit(2) from None
    if as_json:
        ch.emit_json(out)
        return
    if not out["timeline"]:
        ch.info(f"Nothing recorded for {out['key']}.", "Read the sessions first: navig recall ingest")
        return
    t = _table(("When", {"no_wrap": True, "style": "dim"}), ("Kind", {"no_wrap": True}),
               ("Ref", {"no_wrap": True}), ("Title", {}))
    for e in out["timeline"]:
        t.add_row((e["ts"] or "")[:16].replace("T", " ") or "—", e["kind"],
                  str(e["ref"])[:12], _snip(e["title"], 80))
    ch.console.print(t)
    ch.dim(f"key: {out['key']} · open a record: navig recall show <id> · "
           f"trace a session: navig recall trace <session-id>")


@recall_app.command("trace")
def trace_cmd(
    ref: str = typer.Argument(..., help="A commit id (short or full) or a Claude session UUID."),
    project: str = typer.Option(None, "--project", "-p"),
    here: bool = typer.Option(False, "--here"),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """One commit or one session → the other side: sessions ↔ commits ↔ files ↔ records."""
    from navig.recall import query as rq  # noqa: PLC0415

    _require_enabled()
    proj = _project(project, here)
    try:
        out = rq.trace(ref, project=proj, limit=limit, root=_project_root(proj), store=_store())
    except (ValueError, LookupError) as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from None
    if as_json:
        ch.emit_json(out)
        return
    if out["kind"] == "session":
        if out["commits"]:
            t = _table(("Sha", {"no_wrap": True, "style": "bold"}),
                       ("When", {"no_wrap": True, "style": "dim"}), ("Subject", {}))
            for c in out["commits"]:
                t.add_row(c["sha"][:12], (c.get("ts") or "")[:10] or "—",
                          _snip(c.get("subject") or "(unverified sha)", 80))
            ch.console.print(t)
        ch.info(f"{len(out['files'])} file(s), {len(out['plans'])} plan(s), "
                f"{len(out['records'])} record(s) in this session.",
                "files: --json · record: navig recall show <id>")
    else:
        c = out["commit"]
        ch.console.print(f"[bold]{c['sha']}  {_snip(c['subject'], 70) or '(unknown subject)'}[/bold]")
        if not c["known"]:
            ch.warning("git cannot see this sha (rebase/amended away?) — sessions below come from links alone.")
        if out["sessions"]:
            t = _table(("Session", {"no_wrap": True, "style": "bold"}), ("State", {"no_wrap": True}),
                       ("Title", {}))
            for s in out["sessions"]:
                t.add_row(s["session_id"][:8], _STATE.get(s["state"], s["state"]), _snip(s["title"], 70))
            ch.console.print(t)
        ch.dim(f"{len(out['records'])} record(s) · resume a session: claude --resume <full id>")


@recall_app.command("changes")
def changes_cmd(
    project: str = typer.Option(None, "--project", "-p"),
    here: bool = typer.Option(False, "--here"),
    since: int = typer.Option(90, "--since", help="Days of history."),
    refresh: bool = typer.Option(False, "--refresh", help="Ask git for fresh observations first."),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Recent commits of a project, each joined to the sessions and records it explains."""
    from navig.recall import query as rq  # noqa: PLC0415

    _require_enabled()
    proj = _project(project, here)
    if not proj:
        ch.error("Name a project.", "navig recall changes -p <project>  ·  or --here")
        raise typer.Exit(2)
    try:
        out = rq.changes(proj, root=_project_root(proj), since_days=since, limit=limit,
                         refresh=refresh, store=_store())
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(2) from None
    if as_json:
        ch.emit_json(out)
        return
    if not out["commits"]:
        ch.info(f"No commits observed for {proj} since {out['since']}.",
                "Ask git: navig recall changes -p <proj> --refresh")
        return
    t = _table(("Sha", {"no_wrap": True, "style": "bold"}), ("When", {"no_wrap": True, "style": "dim"}),
               ("Merged", {"no_wrap": True}), ("Subject", {}), ("Sess", {"no_wrap": True, "style": "dim"}))
    for c in out["commits"]:
        t.add_row(c["sha"][:12], (c.get("committer_ts") or "")[:10] or "—",
                  {"yes": "[green]●[/green]", "no": "[yellow]◐[/yellow]"}.get(c.get("merged", ""), "[dim]?[/dim]"),
                  _snip(c.get("subject", ""), 70), str(len(c["sessions"])))
    ch.console.print(t)
    ch.dim("trace one: navig recall trace <sha>  ·  fresh from git: --refresh")


@recall_app.command("unfinished")
def unfinished_cmd(
    project: str = typer.Option(None, "--project", "-p"),
    here: bool = typer.Option(False, "--here"),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Plans and tasks nothing has verified yet (display-only — it never starts anything)."""
    from navig.recall import query as rq  # noqa: PLC0415

    _require_enabled()
    proj = _project(project, here)
    out = rq.unfinished(proj, limit=limit, store=_store())
    if as_json:
        ch.emit_json(out)
        return
    if not out["records"]:
        ch.info("Nothing unfinished.", "Verify the code: navig recall verify -p <project>")
        return
    t = _table(("ID", {"no_wrap": True, "style": "bold"}), ("Type", {"no_wrap": True}),
               ("Lifecycle", {"no_wrap": True}), ("Status", {"no_wrap": True}),
               ("Updated", {"no_wrap": True, "style": "dim"}), ("Title", {}))
    for r in out["records"]:
        t.add_row(r["id"], r["type"], r.get("lifecycle", "unknown"),
                  _chip(r["status"], r["status_basis"]),
                  (r.get("updated") or "")[:10] or "—", _snip(r["title"], 70))
    ch.console.print(t)
    ch.dim(f"{out['count']} item(s) · open one: navig recall show <id> · "
           f"verify: navig recall verify -p <proj or --here>")


@recall_app.command("conflicts")
def conflicts_cmd(
    project: str = typer.Option(None, "--project", "-p"),
    here: bool = typer.Option(False, "--here"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-derive lifecycles from git first."),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Recorded contradictions — opposed user verdicts on one subject, or claims verify disproved."""
    from navig.recall import query as rq  # noqa: PLC0415

    _require_enabled()
    proj = _project(project, here)
    out = rq.conflicts(proj, limit=limit, refresh=refresh, root=_project_root(proj), store=_store())
    if as_json:
        ch.emit_json(out)
        return
    reviews = out["reviews"]
    if not reviews:
        ch.info("No recorded contradictions.", "Re-check now: navig recall conflicts --refresh")
        return
    t = _table(("Class", {"no_wrap": True}), ("Record A", {"style": "bold"}),
               ("vs", {"no_wrap": True, "style": "dim"}), ("Record B", {}),
               ("Project", {"no_wrap": True, "style": "dim"}))
    for r in reviews:
        t.add_row(r["class"], f"{r['a']['id']} {_snip(r['a']['title'], 40)}", "vs",
                  (f"{r['b']['id']} {_snip(r['b']['title'], 40)}" if r.get("b") else "[dim]self[/dim]"),
                  r["project"])
    ch.console.print(t)
    ch.dim(f"{len(reviews)} open · evidence: navig recall show <id> · "
           f"rebuild from git: --refresh")


@recall_app.command("migrate")
def migrate_cmd(
    file: str = typer.Argument(..., help="The legacy changelog file to import."),
    project: str = typer.Option(..., "--project", "-p", help="Project to index it under."),
    apply: bool = typer.Option(False, "--apply", help="Write the import (default: dry run, zero writes)."),
    retire: bool = typer.Option(False, "--retire",
                                 help="With --apply: move a PRIVATE dev changelog to its backup "
                                      "after verification (consumers must be empty; release notes "
                                      "are never retired)."),
    delete_backup: bool = typer.Option(False, "--delete-backup",
                                       help="IRREVERSIBLE: delete a verified backup (interactive; "
                                            "type the path back to confirm)."),
    root: str = typer.Option(None, "--root",
                             help="Folder whose files are scanned for consumers (default: the "
                                  "project's registered folder) — pass the tree the FILE lives "
                                  "in when a worktree and the registry disagree."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Import a legacy changelog into Recall — dry-run by default, verify before any retire."""

    from navig.platform.paths import resolve_user_path  # noqa: PLC0415
    from navig.recall.ingest import Ingestor  # noqa: PLC0415

    _require_enabled()
    path = resolve_user_path(file)
    root = resolve_user_path(root) if root else _project_root(project)
    ing = Ingestor()
    try:
        out = ing.import_changelog(project, path, root=root, apply=apply, retire=retire)
    except (FileNotFoundError, ValueError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from None
    except RuntimeError as exc:
        ch.error(str(exc), "Fix the file (or the store) and re-run; the original is untouched.")
        raise typer.Exit(1) from None

    if delete_backup:
        # The one irreversible step: runs only AFTER a verified import, is interactive
        # and refuses under --json — the path must be typed back exactly.
        if as_json or not apply:
            ch.error("--delete-backup is interactive and runs with --apply.",
                     "Run: navig recall migrate <file> -p <project> --apply --delete-backup")
            raise typer.Exit(2)
        if not out.get("verify", {}).get("ok"):
            ch.error("Refusing to delete a backup without a verified import.")
            raise typer.Exit(1)
        bdir = ing.vault.root / ".migrations" / project
        backup = bdir / path.name
        if not backup.exists():
            ch.error(f"No backup at {backup}", "Nothing to delete.")
            raise typer.Exit(1)
        typed = ch.console.input(f"Type the full path to delete [{backup}]: ").strip()
        if typed != str(backup):
            ch.error("Path did not match — nothing was deleted.")
            raise typer.Exit(1)
        (bdir / f"{path.name}.manifest.json").unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        out["backup_deleted"] = True

    if as_json:
        ch.emit_json(out)
        return
    head = f"{out['entries']} entr(ies) · {out['classification']} · sha256 {out['sha256'][:12]}"
    if out["consumers"]:
        head += f" · {len(out['consumers'])} consumer(s)"
    if not apply:
        ch.console.print(f"[bold]dry run[/bold] — {head}")
        from navig.recall import changelog as cl  # noqa: PLC0415

        t = _table(("Line", {"no_wrap": True}), ("Date", {"no_wrap": True, "style": "dim"}),
                   ("Record id", {"no_wrap": True}), ("Title", {}))
        for e in cl.parse(path.read_text(encoding="utf-8", errors="replace"))[:12]:
            t.add_row(f"{e.start}-{e.end}", e.date or "—",
                      cl.record_id(project, path.name, e), _snip(e.title, 70))
        ch.console.print(t)
        if out["consumers"]:
            ch.warning("consumers still reference this file: " + ", ".join(out["consumers"][:5]))
        ch.dim("nothing written — apply: navig recall migrate <file> -p <project> --apply")
        return
    ch.success(f"imported — {head}" + (" (already imported)" if out["applied"] == "unchanged" else ""))
    if out.get("verify"):
        ch.dim(f"verified: digest + {out['verify']['entries']} record id(s) + turns match the original")
    if retire and out.get("retired"):
        ch.success(f"retired: {path.name} → {out.get('backup', 'vault/.migrations')} (recoverable backup)")
    elif retire and out.get("retire_refused"):
        ch.warning(f"not retired: {out['retire_refused']}")
    if delete_backup:
        if out.get("backup_deleted"):
            ch.success("backup deleted (irreversible).")
        else:
            ch.error("The backup was NOT deleted — this run stopped before the deletion step.",
                     "Re-run the command; nothing was removed.")
            raise typer.Exit(1)
    ch.dim("search it: navig recall search \"<words>\"  ·  view: navig recall history <file>")


# Extraction, evaluation, review and verification commands register on recall_app.
from navig.commands import recall_extract  # noqa: E402,F401

"""navig recall — model extraction, evaluation, review and implementation checks.

Registered on the same ``recall`` group as :mod:`navig.commands.recall`. Extraction sends
redacted conversation prose to the configured provider only after ``navig recall enable
--remote``; ``--dry-run`` never sends anything and prints the cost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch
from navig.commands.recall import (
    _chip,
    _enabled,
    _project,
    _snip,
    _store,
    _table,
    _view,
    recall_app,
)

_FOCUS = {"all": ("all",), "split": ("ideas", "decisions", "findings")}


def _cfg(key: str, default: Any) -> Any:
    from navig.config import get_config_manager  # noqa: PLC0415

    value = get_config_manager().get(key)
    return default if value in (None, "") else value


def remote_allowed() -> bool:
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    return coerce_bool(_cfg("recall.extract.remote", False), default=False)


def _options(*, budget: float | None, day_cap: float | None, focus: str, recovery: bool, dry_run: bool):
    from navig.recall.extract import ExtractOptions  # noqa: PLC0415

    if focus not in _FOCUS:
        ch.error(f"Unknown focus '{focus}'.", "Use all (one combined pass) or split (three focused passes).")
        raise typer.Exit(2)
    return ExtractOptions(
        provider=str(_cfg("recall.extract.provider", "mimo")),
        model=str(_cfg("recall.extract.model", "mimo-v2.6-flash")),
        budget_usd=float(budget if budget is not None else _cfg("recall.extract.budget_usd_run", 5.0)),
        day_cap_usd=float(day_cap if day_cap is not None else _cfg("recall.extract.budget_usd_day", 20.0)),
        focus=_FOCUS[focus], recovery=recovery, dry_run=dry_run,
    )


def _gate(dry_run: bool, opts: Any) -> None:
    if not _enabled():
        ch.error("Recall has not been enabled on this machine.", "Turn it on first: navig recall enable")
        raise typer.Exit(1)
    if not dry_run and not remote_allowed():
        ch.error(f"Extraction sends redacted conversation text to {opts.provider}:{opts.model}, "
                 "and that has not been allowed.",
                 "See the cost first: --dry-run  ·  allow it: navig recall enable --remote")
        raise typer.Exit(1)


def _print_report(rep: Any, opts: Any) -> None:
    if opts.dry_run:
        ch.info(f"Dry run — nothing sent. {rep.sessions} session(s) · {rep.windows} window(s) "
                f"({rep.cached} already extracted) · ≈{rep.est_prompt_tokens:,} input tokens · "
                f"≈${rep.est_cost_usd:.2f} with {opts.provider}:{opts.model}")
        return
    ch.success(f"{rep.calls} call(s) · {rep.items} item(s) → {rep.records_created} new / "
               f"{rep.records_updated} updated record(s) · ${rep.cost_usd:.4f} "
               f"({rep.prompt_tokens:,} in / {rep.completion_tokens:,} out)")
    ch.dim(f"quotes verified {rep.valid_quotes} · rejected {rep.rejected_quotes} · unquoted items to review "
           f"{rep.unquoted_items} · review queue +{rep.review_added} · cached windows {rep.cached} · "
           f"parse errors {rep.parse_errors}")
    if rep.stopped:
        ch.warning(f"Stopped: {rep.stopped}")


@recall_app.command("extract")
def extract_cmd(
    project: str = typer.Option(None, "--project", "-p", help="Only sessions of this project."),
    here: bool = typer.Option(False, "--here", help="Only the project you are standing in."),
    source: list[str] = typer.Option(None, "--source", "-s", help="Only these source ids (repeatable)."),
    sessions: int = typer.Option(None, "--sessions", "-n", help="At most N sessions."),
    budget: float = typer.Option(None, "--budget", help="Stop before spending more than this (USD) in this run."),
    day_cap: float = typer.Option(None, "--day-cap", help="Daily spending cap across runs (USD)."),
    focus: str = typer.Option("all", "--focus", help="all = one combined pass · split = three focused passes."),
    no_recovery: bool = typer.Option(False, "--no-recovery", help="Skip the second look at uncited cue turns."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count windows and estimate the cost; send nothing."),
    workers: int = typer.Option(6, "--workers", help="Model calls in flight at once (1–16)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Extract ideas, decisions, terms, plans, questions, findings and risks with the model —
    every record cites a verified quote. Resumable; already-extracted windows are free."""
    from navig.recall.extract import run_extraction  # noqa: PLC0415
    from navig.recall.ingest import Ingestor  # noqa: PLC0415

    opts = _options(budget=budget, day_cap=day_cap, focus=focus, recovery=not no_recovery, dry_run=dry_run)
    _gate(dry_run, opts)
    opts.project = _project(project, here)
    opts.source_ids = tuple(source or ())
    opts.max_sessions = sessions
    opts.workers = max(1, min(16, workers))

    def _progress(i: int, n: int, sid: str) -> None:
        if not as_json and (i == n or i % 25 == 0):
            ch.dim(f"  {i}/{n}  {sid[:48]}")

    rep = run_extraction(Ingestor(), opts, progress=_progress)
    if as_json:
        ch.emit_json(rep.to_dict())
    else:
        _print_report(rep, opts)
    if rep.stopped.startswith("extractor unavailable"):
        raise typer.Exit(1)


@recall_app.command("eval")
def eval_cmd(
    sessions: int = typer.Option(6, "--sessions", "-n", help="How many audited sessions to score."),
    budget: float = typer.Option(1.0, "--budget", help="Spending limit for this evaluation (USD)."),
    focus: str = typer.Option("all", "--focus", help="all · split"),
    project: str = typer.Option(None, "--project", "-p"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Pick the sessions and estimate the cost only."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Measure extraction against the session audit: recall of the audit's features/ideas per
    session, quote validity, and attribution errors (must be 0)."""
    from navig.recall.evaluate import evaluate  # noqa: PLC0415
    from navig.recall.ingest import Ingestor  # noqa: PLC0415

    opts = _options(budget=budget, day_cap=None, focus=focus, recovery=True, dry_run=dry_run)
    _gate(dry_run, opts)
    rep = evaluate(Ingestor(), opts, sessions=sessions, project=project)
    if as_json:
        ch.emit_json(rep.to_dict())
        return
    if dry_run:
        _print_report(rep.extraction, opts)
        ch.dim("sessions: " + ", ".join(s.source_id for s in rep.sessions))
        return
    t = _table(("Session", {"no_wrap": True, "style": "dim"}), ("Gold", {"justify": "right", "no_wrap": True}),
               ("Found", {"justify": "right", "no_wrap": True}), ("Missed (first)", {}))
    for s in rep.sessions:
        t.add_row(s.source_id[:22], str(s.gold), str(s.found), _snip("; ".join(s.missed[:3]), 110))
    ch.console.print(t)
    _print_report(rep.extraction, opts)
    colour = "green" if rep.attribution_errors == 0 else "red"
    ch.console.print(f"recall {rep.recall:.0%} · quote validity {rep.quote_validity:.0%} · "
                     f"[{colour}]attribution errors {rep.attribution_errors}[/{colour}]")


@recall_app.command("review")
def review_cmd(
    action: str = typer.Argument("list", help="list · accept · reject"),
    review_id: int = typer.Argument(None, help="Review item id (for accept / reject)."),
    limit: int = typer.Option(30, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Items that need a human: possible duplicates and model items without a verifiable quote."""
    st = _store()
    if action == "list":
        items = st.list_review(limit=limit)
        if as_json:
            ch.emit_json({"review": items, "count": len(items)})
            return
        if not items:
            ch.info("Review queue is empty.")
            return
        t = _table(("ID", {"no_wrap": True, "style": "bold"}), ("Kind", {"no_wrap": True}), ("What", {}))
        for it in items:
            if it["kind"] == "possible_duplicate":
                other = st.get_record(it["other_id"])
                what = f"{it['record_id']} may duplicate {it['other_id']} ({_snip(other.title if other else '?', 60)})"
            else:
                item = it["data"].get("item") or {}
                what = f"{item.get('type', '?')}: {_snip(item.get('title'), 90)} [dim]({it['data'].get('source_id')})[/dim]"
            t.add_row(str(it["id"]), it["kind"], what)
        ch.console.print(t)
        ch.dim("navig recall review accept <id>  ·  navig recall review reject <id>")
        return
    if action not in ("accept", "reject") or review_id is None:
        ch.error("Usage: navig recall review [list | accept <id> | reject <id>]")
        raise typer.Exit(2)
    it = st.get_review(review_id)
    if it is None or it["status"] != "open":
        ch.error(f"No open review item {review_id}.", "List them: navig recall review")
        raise typer.Exit(1)
    if action == "reject":
        st.set_review(review_id, "rejected")
        ch.success(f"Review {review_id} rejected — nothing changed in the bank.")
        return
    _accept(st, it)
    st.set_review(review_id, "accepted")


def _accept(st: Any, it: dict[str, Any]) -> None:
    from navig.recall.ingest import Ingestor  # noqa: PLC0415
    from navig.recall.vault import make_event  # noqa: PLC0415
    from navig.store.base import _utcnow  # noqa: PLC0415

    ing = Ingestor()
    if it["kind"] == "possible_duplicate":
        dup, keep = st.get_record(it["record_id"]), st.get_record(it["other_id"])
        if dup is None or keep is None:
            ch.error("One of the two records no longer exists.")
            raise typer.Exit(1)
        merged = keep
        merged.evidence = dup.evidence
        merged.aliases = sorted(set(keep.aliases) | {dup.title} | set(dup.aliases))
        ing.commit([merged], extra_events=[make_event(keep.id, "merged", _utcnow(), {"from": dup.id})])
        dup.status, dup.status_basis = "superseded", "user"
        path = ing.vault.write(dup, existing=st.record_path(dup.id))
        st.upsert_record(dup, path=str(path))
        fresh = st.add_links([(keep.id, dup.id, "supersedes")])
        ing.vault.append_links(fresh)
        ing.vault.append_events(st.add_events([make_event(dup.id, "status", _utcnow(),
                                                          {"to": "superseded", "basis": "user", "by": "review"})]))
        ch.success(f"Merged {dup.id} into {keep.id}; {dup.id} is kept as superseded (history intact).")
    else:
        ch.warning("An unquoted item cannot be cited, so it is not added as a record. "
                   "Search the conversation for it instead: navig recall search --raw <words>")


@recall_app.command("verify")
def verify_cmd(
    project: str = typer.Option(None, "--project", "-p"),
    here: bool = typer.Option(False, "--here", help="The project you are standing in."),
    root: str = typer.Option(None, "--root", help="Repository folder (default: the project's registered folder)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Check ideas/plans against the project's code: likely · partial · not_found · unknown.
    Never marks anything verified — a name in the code is not proof of behaviour."""
    from navig.platform.paths import resolve_user_path  # noqa: PLC0415
    from navig.recall.ingest import Ingestor  # noqa: PLC0415
    from navig.recall.verify import verify_project  # noqa: PLC0415

    proj = _project(project, here)
    if not proj:
        ch.error("Name a project.", "navig recall verify -p <project>  ·  or --here")
        raise typer.Exit(2)
    repo: Path | None = resolve_user_path(root) if root else None
    if repo is None:
        try:
            from navig.projects.model import resolve_project  # noqa: PLC0415

            repo = Path(resolve_project(proj)[1])
        except Exception:  # noqa: BLE001
            repo = None
    if repo is None or not repo.is_dir():
        ch.error(f"No folder known for project '{proj}'.", "Pass it: --root <folder>")
        raise typer.Exit(1)
    ing = Ingestor()
    counts = verify_project(ing, proj, repo)
    # Verification just re-read the code — re-derive the delivery lifecycle with fresh
    # git verdicts (deterministic, zero inference); it rides along with every verify.
    from navig.recall.lifecycle import refresh as refresh_lifecycle  # noqa: PLC0415

    life = refresh_lifecycle(ing.store, project=proj, root=repo, vault=ing.vault)
    # Fresh impl values can create a claimed-built-vs-not_found contradiction — record it.
    from navig.recall import reconcile  # noqa: PLC0415

    rec_res = reconcile.reconcile(ing.store, project=proj)
    if as_json:
        ch.emit_json({"project": proj, "root": str(repo), "impl": counts, "lifecycle": life,
                      "reconcile": rec_res})
        return
    ch.success(f"Checked {sum(counts.values())} record(s) in {proj}: "
               + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    ch.dim(f"lifecycle re-derived: {life['checked']} checked · {life['changed']} updated "
           f"(git: {life['git']})")
    if rec_res["found"]:
        ch.warning(f"{rec_res['found']} contradiction(s) recorded — see: navig recall conflicts")
    ch.dim("see one: navig recall show <id>  (impl + the files that matched)")


@recall_app.command("terms")
def terms_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
              status: str = typer.Option(None, "--status", help="rejected = names turned down"),
              limit: int = typer.Option(30, "--limit", "-n"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Vocabulary — names and terms coined, adopted or rejected (with the reason)."""
    _view("term", project, here, status, limit, as_json, "Extract them first: navig recall extract")


@recall_app.command("forgotten")
def forgotten_cmd(project: str = typer.Option(None, "--project", "-p"), here: bool = typer.Option(False, "--here"),
                  limit: int = typer.Option(30, "--limit", "-n"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Forgotten ideas — not rejected, no implementation found, oldest first."""
    recs = [r for r in _store().list_records(rtype="idea", project=_project(project, here), limit=5000)
            if r.status not in ("rejected", "superseded")
            and r.impl in ("not_found", "unknown", "discussed_unverified")]
    recs.sort(key=lambda r: r.updated or "")
    recs = recs[:limit]
    if as_json:
        ch.emit_json({"records": [{"id": r.id, "title": r.title, "project": r.project, "status": r.status,
                                   "status_basis": r.status_basis, "impl": r.impl, "updated": r.updated}
                                  for r in recs], "count": len(recs)})
        return
    if not recs:
        ch.info("No forgotten ideas found.", "Extract ideas first: navig recall extract")
        return
    t = _table(("ID", {"no_wrap": True, "style": "bold"}), ("Status", {"no_wrap": True}),
               ("Impl", {"no_wrap": True, "style": "dim"}), ("Since", {"no_wrap": True, "style": "dim"}),
               ("Idea", {}))
    for r in recs:
        t.add_row(r.id, _chip(r.status, r.status_basis), r.impl, (r.updated or "")[:10] or "—", _snip(r.title, 90))
    ch.console.print(t)
    ch.dim("open one: navig recall show <id>  ·  check the code: navig recall verify -p <project>")

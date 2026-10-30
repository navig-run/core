"""``navig webflow`` — browser procedures recorded in the NAVIG Dock, replayed with no model.

A Webflow is a ``navig.webflow/1`` document: steps over semantic locators, parameters, the
sites it may act on, and checks that prove the outcome. Record one in the Dock's side panel
(Webflows); manage and replay it here.

    navig webflow list [--match TEXT]            what exists (a goal discovers flows here)
    navig webflow show <id>
    navig webflow run <id> -p name=value …       replay headless; exit 0 only when VERIFIED
    navig webflow run <id> --allow-vault         fill vault-backed secrets (never as arguments)
    navig webflow export <id> [-o file]          the sanitized, versioned JSON
    navig webflow import <file>
    navig webflow run <id> --repair         …repair a changed site (new revision, incident)
    navig webflow run <id> --resume <run>   …continue an interrupted run
    navig webflow runs [id]                 run checkpoints (pause / resume / evidence)
    navig webflow rollback <id> -r N        restore an old revision as a new one
    navig webflow delete <id> --yes

``run`` exit codes — what a goal subtask is judged by: 0 verified (every step passed and a
check proved the outcome) · 4 needs you (paused: a secret nobody may fill or a missing vault
login — checkpoint kept, the pause notification sent) · 3 completed without a check (NOT proof)
· 1 failed · 130 stopped · 2 the run could not start (unknown flow, missing value).
"""

from __future__ import annotations

import typer

from navig import console_helper as ch

webflow_app = typer.Typer(
    help="Browser procedures recorded in the NAVIG Dock — list, replay (no AI), export, import.",
    no_args_is_help=True,
)


def _esc(text: object) -> str:
    from rich.markup import escape

    return escape(str(text))


def _store():
    from navig.webflows import WebflowStore

    return WebflowStore()


def _load(wf_id: str, revision: int | None = None) -> dict:
    from navig.webflows import WebflowError

    try:
        doc = _store().get(wf_id, revision)
    except WebflowError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(2) from None
    if doc is None:
        ch.error(f"No webflow '{_esc(wf_id)}'" + (f" revision {revision}" if revision else "") + ".",
                 "See: navig webflow list")
        raise typer.Exit(2)
    return doc


def step_line(step: dict) -> str:
    """One line a person reads (the Dock's wording)."""
    loc = step.get("name") and f"{step.get('role', '')} “{step['name']}”" or step.get("testid") or step.get("css") \
        or (step.get("has_text") and f"“{step['has_text']}”") or step.get("role") or ""
    action = step["action"]
    if action == "navigate":
        return f"Open {step['url']}"
    if action == "click":
        return f"Click {loc}"
    if action == "fill":
        if isinstance(step.get("value"), bool):
            return f"{'Tick' if step['value'] else 'Untick'} {loc}"
        return f"Fill {loc} with {step.get('value')}"
    if action == "select":
        return f"Choose “{step.get('value')}” in {loc}"
    if action == "key":
        return f"Press {step['keys']}"
    if action == "wait_for":
        return f"Wait for {loc or 'the address to contain “' + str(step.get('url_contains')) + '”'}"
    if action == "assert":
        return f"Check the page shows {loc}" if loc else f"Check the address contains “{step.get('url_contains')}”"
    return action


@webflow_app.command("list")
def webflow_list(
    match: str = typer.Option("", "--match", "-m", help="Only flows whose name, sites or description contain these words."),
    as_json: bool = typer.Option(False, "--json", help="Emit the flows as JSON."),
) -> None:
    """List saved webflows (newest first)."""
    store = _store()
    rows = store.list()
    if match:
        from navig.webflows.goals import find_webflows

        keep = {f["id"] for f in find_webflows(match, store)}
        rows = [r for r in rows if r.id in keep]
    if as_json:
        ch.emit_json([r.to_dict() for r in rows])
        return
    if not rows:
        ch.info("No webflows yet. Record one in the NAVIG Dock: side panel → Webflows → Record."
                if not match else f"No webflow matches '{_esc(match)}'.")
        return
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Id", no_wrap=True, style="cyan")
    table.add_column("Name")
    table.add_column("Steps", no_wrap=True, justify="right")
    table.add_column("Sites", no_wrap=True)
    table.add_column("Rev", no_wrap=True, justify="right")
    table.add_column("Next step", no_wrap=True, style="dim")
    for r in rows:
        sites = ", ".join(o.split("://", 1)[-1] for o in r.origins)
        table.add_row(r.id, _esc(r.name), str(r.steps), _esc(sites), f"r{r.revision}", f"navig webflow run {r.id}")
    ch.console.print(table)
    ch.dim("Replay one with: navig webflow run <id> -p name=value  (exit 0 = verified)")


@webflow_app.command("show")
def webflow_show(
    wf_id: str = typer.Argument(..., help="Webflow id (see navig webflow list)."),
    revision: int = typer.Option(None, "--revision", "-r", help="An older revision."),
    as_json: bool = typer.Option(False, "--json", help="Emit the document as JSON."),
) -> None:
    """Show a webflow: its sites, parameters and steps."""
    doc = _load(wf_id, revision)
    if as_json:
        ch.emit_json(doc)
        return
    from navig.console_helper import Table

    ch.panel(
        f"[bold]{_esc(doc['name'])}[/bold]  r{doc.get('revision')}\n"
        f"Starts at {_esc(doc['start_url'])}\nSites: {_esc(', '.join(doc['origins']))}"
        + (f"\n{_esc(doc['description'])}" if doc.get("description") else ""),
        title=doc["id"],
    )
    if doc["params"]:
        pt = Table(box=None, show_header=True, padding=(0, 2))
        pt.add_column("Param", no_wrap=True, style="cyan")
        pt.add_column("Type", no_wrap=True)
        pt.add_column("Value")
        for p in doc["params"]:
            if p["type"] == "secret":
                src = f"vault: {p['vault']['username']} @ {p['vault']['domain']}" if p.get("vault") else "asked at each run"
            else:
                src = f"default {p['default']!r}" if "default" in p else "[yellow]required[/yellow]"
            pt.add_row(p["name"], p["type"], _esc(src) if "[yellow]" not in src else src)
        ch.console.print(pt)
    st = Table(box=None, show_header=True, padding=(0, 2))
    st.add_column("#", no_wrap=True, justify="right", style="dim")
    st.add_column("Action", no_wrap=True)
    st.add_column("Step")
    for i, s in enumerate(doc["steps"], 1):
        st.add_row(str(i), s["action"], _esc(step_line(s)))
    ch.console.print(st)


def _parse_params(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        name, sep, value = pair.partition("=")
        if not sep or not name.strip():
            ch.error(f"--param takes name=value (got '{_esc(pair)}').")
            raise typer.Exit(2)
        out[name.strip()] = value
    return out


@webflow_app.command("run")
def webflow_run(
    wf_id: str = typer.Argument(..., help="Webflow id (see navig webflow list)."),
    param: list[str] = typer.Option(None, "--param", "-p", help="name=value for a parameter (repeatable). Never a secret."),
    allow_vault: bool = typer.Option(False, "--allow-vault", help="Fill vault-backed secret params from the NAVIG vault for this run."),
    headed: bool = typer.Option(False, "--headed", help="Show the browser."),
    repair: bool = typer.Option(False, "--repair",
                                 help="Let a renamed element be repaired from a live observation "
                                      "(recorded, and saved as a new revision)."),
    resume: str = typer.Option(None, "--resume", help="Continue a recorded run (see navig webflow runs)."),
    revision: int = typer.Option(None, "--revision", "-r", help="Replay an older revision."),
    as_json: bool = typer.Option(False, "--json", help="Emit the run report as JSON."),
) -> None:
    """Replay a webflow headless; exit 0 only when a check verified the outcome, not merely ran."""
    import asyncio
    import sys

    from navig.webflows import WebflowError
    from navig.webflows.headless import exit_code, run_headless, vault_backed

    doc = _load(wf_id, revision)
    if resume:
        # Continue on the revision the interrupted run was ON: an edited flow resumes
        # its own step list, never the newest one (run_headless refuses a mismatch too).
        from navig.webflows.runs import load as load_cp

        try:
            cp = load_cp(_store()._dir(wf_id), resume)
        except WebflowError:
            cp = None
        cp_rev = (cp or {}).get("revision")
        if cp_rev and cp_rev != doc.get("revision"):
            if revision is not None:
                ch.error(f"--resume continues run {resume} on revision {cp_rev}, not r{revision}.",
                         "Drop --revision to continue it, or run fresh without --resume.")
                raise typer.Exit(2)
            doc = _load(wf_id, cp_rev)
            if not as_json:
                ch.dim(f"Continuing revision {cp_rev} — the revision this run started on.")
    given = _parse_params(param)
    secrets = {p["name"] for p in doc["params"] if p["type"] == "secret"}
    passed_secret = sorted(secrets & given.keys())
    if passed_secret:
        # A secret on the command line lands in shell history and process listings.
        ch.error(f"Never pass a secret as an argument ({', '.join(passed_secret)}).",
                 "Tie it to a vault login in the Dock editor and use --allow-vault, or run interactively to be asked.")
        raise typer.Exit(2)
    for name in sorted(secrets - (vault_backed(doc) if allow_vault else set())):
        if not sys.stdin.isatty():
            # Not a usage error: the flow is fine, a human is missing. Pause properly —
            # tell them (when notify is configured) and exit with the needs-you status.
            from navig.webflows.notify import notify_needs_user

            reason = (
                f"the secret '{name}' can come from the vault — re-run with --allow-vault"
                if name in vault_backed(doc)
                else f"the secret '{name}' needs a person — type it at a terminal, or tie it "
                     "to a vault login in the Dock editor and use --allow-vault"
            )
            asyncio.run(notify_needs_user(flow_id=wf_id, flow_name=doc["name"], reason=reason))
            if as_json:
                ch.emit_json({"status": "needs_user", "reason": reason})
            else:
                ch.warning("Paused: this run needs you.", reason)
            raise typer.Exit(4)
        label = next(p.get("label") or name for p in doc["params"] if p["name"] == name)
        given[name] = typer.prompt(label, hide_input=True)

    def on_step(res) -> None:
        if as_json:
            return
        line = step_line(doc["steps"][res.index]) if res.index >= 0 else "start"
        if res.category == "skipped":
            # A resume verdict: proven done on the restored page — not executed again.
            ch.console.print(f"  [dim]– {_esc(line)}  already done[/dim]")
            return
        mark = "[green]✓[/green]" if res.ok else "[red]✗[/red]"
        note = " [cyan]· repaired[/cyan]" if res.repaired else ""
        ch.console.print(f"  {mark} {_esc(line)}{note}  [dim]{res.ms} ms[/dim]")
        if not res.ok:
            ch.console.print(f"      [red]{_esc(res.category)}: {_esc(res.error)}[/red]")

    if resume:
        run_id = resume
    else:
        from navig.webflows.runs import new_run_id

        run_id = new_run_id()
        if not as_json:
            ch.dim(f"Run {run_id} — if this stops midway, continue with: navig webflow run {wf_id} --resume {run_id}")
    try:
        report = asyncio.run(run_headless(doc, given, allow_vault=allow_vault, headless=not headed,
                                          on_step=on_step, repair=repair, resume=resume, run_id=run_id))
    except WebflowError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(2) from None
    status = report["status"]
    if report.get("resumed") and not as_json:
        r = report["resumed"]
        ch.info(f"Resumed {r['run_id']}: {r['skipped']} completed steps skipped — page {r['from']}")
    if report.get("repairs") and not as_json:
        saved = report.get("saved_revision")
        ch.info(f"Repaired {len(report['repairs'])} step(s) after the site changed"
                + (f" — saved as revision {saved}." if saved else " — not saved (this flow is not in the store)."))
    if as_json:
        ch.emit_json(report)
    elif status == "verified":
        ch.success(f"Verified — {len(report['steps'])} steps, every check passed ({report['ms']} ms).")
    elif status == "completed":
        ch.warning("Every step ran, but this flow has no check — the outcome is NOT verified.",
                   "Add a check in the Dock editor (Add check / + Check text).")
    elif status == "needs_user":
        ch.warning("Paused — this run needs you; nothing after that step ran.",
                   f"Finish it, then: navig webflow run {wf_id} --resume {report.get('run_id')}")
    elif status == "stopped":
        ch.warning("Stopped.")
    else:
        ch.error("The run failed — nothing after the failing step ran.")
    raise typer.Exit(exit_code(status))


@webflow_app.command("export")
def webflow_export(
    wf_id: str = typer.Argument(..., help="Webflow id."),
    output: str = typer.Option(None, "--output", "-o", help="Write to this file instead of stdout."),
    revision: int = typer.Option(None, "--revision", "-r", help="An older revision."),
) -> None:
    """Export the sanitized navig.webflow/1 JSON (what import accepts)."""
    from navig.webflows import dumps

    text = dumps(_load(wf_id, revision))
    if not output:
        typer.echo(text, nl=False)
        return
    from navig.platform.paths import resolve_user_path

    path = resolve_user_path(output)
    path.write_text(text, encoding="utf-8")
    ch.success(f"Exported {wf_id} → {_esc(path)}")


@webflow_app.command("import")
def webflow_import(
    file: str = typer.Argument(..., help="A .webflow.json file."),
) -> None:
    """Import a webflow as a new flow (validated and sanitized first)."""
    import json

    from navig.platform.paths import resolve_user_path
    from navig.webflows import WebflowError

    path = resolve_user_path(file)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        ch.error(f"Cannot read {_esc(path)}: {_esc(exc)}")
        raise typer.Exit(1) from None
    if isinstance(raw, dict):
        raw.pop("id", None)  # an import never overwrites an existing flow
        raw.pop("revision", None)
    try:
        doc = _store().save(raw)
    except WebflowError as exc:
        ch.error("This file is not a valid webflow:", "\n".join(f"  • {_esc(e)}" for e in exc.errors))
        raise typer.Exit(1) from None
    ch.success(f"Imported as {doc['id']} ({len(doc['steps'])} steps).")


@webflow_app.command("delete")
def webflow_delete(
    wf_id: str = typer.Argument(..., help="Webflow id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Delete without asking."),
) -> None:
    """Delete a webflow and every revision of it."""
    doc = _load(wf_id)
    if not yes and not typer.confirm(f"Delete '{doc['name']}' and its {len(_store().revisions(wf_id))} revision(s)?"):
        raise typer.Exit(1)
    _store().delete(wf_id)
    ch.success(f"Deleted {wf_id}.")



@webflow_app.command("rollback")
def webflow_rollback(
    wf_id: str = typer.Argument(..., help="Webflow id."),
    revision: int = typer.Option(..., "--revision", "-r", help="The revision to bring back."),
) -> None:
    """Restore an older revision as a new one — append-only, so nothing is lost."""
    from navig.webflows import WebflowError

    doc = _load(wf_id, revision)
    prov = dict(doc.get("provenance") or {})
    prov.update({"source": "rollback", "recorder": f"revision {revision}"})
    try:
        saved = _store().save({**doc, "provenance": prov}, wf_id=wf_id)
    except WebflowError as exc:
        ch.error(_esc(exc))
        raise typer.Exit(1) from None
    ch.success(f"{wf_id}: r{revision} restored as r{saved['revision']}.")
    ch.dim(f"  every revision is kept — compare any two with: navig webflow show {wf_id} --revision N")


@webflow_app.command("runs")
def webflow_runs(
    wf_id: str = typer.Argument(None, help="Only this flow's runs (default: every flow)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the checkpoints as JSON."),
) -> None:
    """List saved run checkpoints: what paused, what finished, what may resume."""
    from navig.webflows.runs import list_runs as checkpoints
    from navig.webflows.store import WebflowStore

    store = WebflowStore()
    ids = [wf_id] if wf_id else [r.id for r in store.list()]
    if wf_id:
        _load(wf_id)  # 404 (exit 2) on an unknown flow
    rows: list[dict] = []
    for flow_id in ids:
        for cp in checkpoints(store.root / flow_id):
            rows.append(cp)
    if as_json:
        ch.emit_json(rows)
        return
    if not rows:
        ch.info("No recorded runs yet." if wf_id is None else f"No recorded runs for '{_esc(wf_id)}'.",
                "Every `navig webflow run` leaves one here.")
        return
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Flow", no_wrap=True, style="cyan")
    table.add_column("Run", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("At step", no_wrap=True, justify="right")
    table.add_column("Next step", no_wrap=True, style="dim")
    styles = {"running": "[yellow]running[/yellow]", "interrupted": "[yellow]interrupted[/yellow]",
              "needs_user": "[red]needs you[/red]", "verified": "[green]verified[/green]",
              "completed": "completed", "failed": "[red]failed[/red]", "stopped": "stopped"}
    for cp in rows:
        flow, run = cp.get("flow_id", ""), cp.get("run_id", "")
        table.add_row(_esc(flow), _esc(run), styles.get(cp.get("status", ""), cp.get("status", "")),
                      str(cp.get("next_index", 0)), f"navig webflow run {flow} --resume {run}")
    ch.console.print(table)
    ch.dim("Resume a paused run with: navig webflow run <flow> --resume <run>")

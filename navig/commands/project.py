"""navig project — every folder with a `.navig/`, managed as one thing.

The same data the OS Projects hub shows: a project is a registered space, its
docs, todos, scripts and a budgeted context pack. `navig project context --json`
is how other agents (Claude Code, Forge) read the same context navig uses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch

project_app = typer.Typer(
    name="project",
    help="Projects — every folder with .navig/: plans, todos, scripts, context.",
    no_args_is_help=True,
)


def _resolve(project_id: str | None) -> tuple[str, Path]:
    """An explicit id, else the project the operator is standing in."""
    from navig.platform.paths import invocation_cwd  # noqa: PLC0415
    from navig.projects.model import ProjectNotFound, resolve_project  # noqa: PLC0415

    if project_id:
        try:
            return resolve_project(project_id)
        except ProjectNotFound as exc:
            ch.error(str(exc), "List them with: navig project list")
            raise typer.Exit(1) from exc
    from navig.spaces import registry  # noqa: PLC0415

    here = invocation_cwd()
    for candidate in (here, *here.parents):
        row = registry.entry_for(candidate)
        if row:
            return str(row.get("id")), candidate
        if (candidate / ".navig").is_dir() and candidate != Path.home():
            ch.error(f"{candidate} has .navig/ but is not registered",
                     f"Add it with: navig project add {candidate}")
            raise typer.Exit(1)
    ch.error("Not inside a project", "Name one (navig project list) or cd into it.")
    raise typer.Exit(1)


def _ago(ts: float | None) -> str:
    if not ts:
        return "—"
    import time  # noqa: PLC0415

    secs = max(0, int(time.time() - ts))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return "just now"


@project_app.command("list")
def list_cmd(
    refresh: bool = typer.Option(False, "--refresh", help="Re-scan instead of using cached summaries."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Every project, pinned first, then most recently active."""
    from navig.projects.model import list_projects  # noqa: PLC0415

    items = list_projects(refresh=refresh)
    if as_json:
        ch.emit_json({"projects": items, "count": len(items)})
        return
    if not items:
        ch.info("No projects yet.", "Add a folder: navig project add <path>   ·   a whole root: navig project scan <dir> --add")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("ID", style="bold", no_wrap=True)
    table.add_column("Phase")  # the one wrappable column
    table.add_column("Todos", no_wrap=True, justify="right")
    table.add_column("Stack", no_wrap=True, style="dim")
    table.add_column("Git", no_wrap=True)
    table.add_column("Active", no_wrap=True, style="dim")
    for p in items:
        todos = p.get("todos") or {}
        open_n = todos.get("open", 0) + todos.get("in_progress", 0)
        git = p.get("git") or {}
        git_cell = "[dim]—[/dim]"
        if git.get("unknown"):
            git_cell = "[dim]? slow git[/dim]"
        elif git.get("repo"):
            dirty = git.get("dirty") or 0
            git_cell = f"{git.get('branch')}" + (f" [yellow]●{dirty}[/yellow]" if dirty else "")
        pin = "[cyan]★[/cyan] " if (p.get("prefs") or {}).get("pinned") else ""
        off = " [dim](disabled)[/dim]" if not p.get("enabled", True) else ""
        phase = (p.get("phase") or {}).get("title") or "[dim]—[/dim]"
        table.add_row(f"{pin}{p['id']}{off}", phase, f"{open_n}" if todos else "—",
                      ", ".join(p.get("stack", [])[:3]) or "—", git_cell, _ago(p.get("last_activity")))
    ch.console.print(table)
    ch.dim(f"{len(items)} project(s) · open one: navig project show <id>")


@project_app.command("show")
def show_cmd(
    project_id: str = typer.Argument(None, help="Project id (default: the one you are in)."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """One project: phase, next action, todos, scripts, git."""
    from navig.projects.model import get_project  # noqa: PLC0415

    pid, _root = _resolve(project_id)
    d = get_project(pid)
    if as_json:
        ch.emit_json(d)
        return
    ch.console.print(f"[bold]{d['name']}[/bold]  [dim]{d['path']}[/dim]")
    if d.get("description"):
        ch.console.print(d["description"])
    phase = d.get("phase") or {}
    todos = d.get("todos") or {}
    rows = [
        ("Phase", phase.get("title") or "—"),
        ("Next", (d.get("next_action") or {}).get("text") or "—"),
        ("Todos", f"{todos.get('open', 0)} open · {todos.get('in_progress', 0)} in progress · {todos.get('done', 0)} done"),
        ("Stack", ", ".join(d.get("stack") or []) or "—"),
        ("Scripts", str(len((d.get("profile") or {}).get("scripts") or []))),
        ("Docs", f"{(d.get('doc_health') or {}).get('count', 0)}"
                 + (f" · missing {', '.join(d['doc_health']['missing'])}" if d["doc_health"].get("missing") else "")),
    ]
    git = d.get("git") or {}
    if git.get("unknown"):
        rows.append(("Git", "unknown — git did not answer in time"))
    elif git.get("repo"):
        rows.append(("Git", f"{git.get('branch')} · {git.get('dirty', 0)} uncommitted"))
    for link in d.get("links") or []:
        rows.append((str(link.get("provider", "link")).title(), str(link.get("summary") or link.get("title") or "")))

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    for k, v in rows:
        table.add_row(k, v)
    ch.console.print(table)
    ch.dim(f"context: navig project context {pid}  ·  scripts: navig project scripts {pid}")


@project_app.command("add")
def add_cmd(
    path: str = typer.Argument(..., help="Folder to add (relative paths are from where you are)."),
    scaffold: bool = typer.Option(False, "--scaffold", help="Also lay down the plans skeleton (never overwrites)."),
) -> None:
    """Register a folder as a project (reads its .navig/ immediately)."""
    from navig.platform.paths import resolve_user_path  # noqa: PLC0415
    from navig.projects.model import add_project  # noqa: PLC0415

    target = resolve_user_path(path)
    try:
        res = add_project(target, scaffold=scaffold)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    ch.success(f"Added {res['id']}", res["path"])
    if res.get("created"):
        ch.dim("created: " + ", ".join(res["created"][:8]))
    ch.dim(f"see it: navig project show {res['id']}")


@project_app.command("scan")
def scan_cmd(
    root: str = typer.Argument(..., help="A folder of projects, e.g. C:\\projects\\apps."),
    add: bool = typer.Option(False, "--add", help="Register every child that already has .navig/."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Find projects one level inside a folder."""
    from navig.platform.paths import resolve_user_path  # noqa: PLC0415
    from navig.projects.model import scan_root  # noqa: PLC0415

    try:
        res = scan_root(resolve_user_path(root), add=add)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    if as_json:
        ch.emit_json(res)
        return
    cands = res["candidates"]
    if not cands:
        ch.info("No projects found there.", "A project is a folder with .navig/ or a code manifest.")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Folder", style="bold", no_wrap=True)
    table.add_column("Kind", no_wrap=True, style="dim")
    table.add_column("State", no_wrap=True)
    table.add_column("Next step")
    errors = 0
    for c in cands:
        if c.get("error"):
            errors += 1
            state, step = "[red]✗ failed[/red]", c["error"]
        elif c["registered"]:
            state, step = "[green]● registered[/green]", "ready"
        elif c["has_navig"]:
            state, step = "[yellow]○ not added[/yellow]", "→ navig project scan … --add"
        else:
            state, step = "[dim]○ no .navig/[/dim]", f"→ navig project add {c['path']}"
        table.add_row(c["name"], c["kind"], state, step)
    ch.console.print(table)
    ch.dim(f"{sum(1 for c in cands if c['registered'])}/{len(cands)} registered"
           + (f" · {len(res['added'])} added now" if res.get("added") else ""))
    if errors:
        raise typer.Exit(1)


@project_app.command("context")
def context_cmd(
    project_id: str = typer.Argument(None, help="Project id (default: the one you are in)."),
    as_json: bool = typer.Option(False, "--json", help="The full pack + budget report as JSON."),
    report: bool = typer.Option(False, "--report", help="Show only the per-source budget report."),
    pin: bool = typer.Option(False, "--pin", help="Write it to .navig/context.pack.md for other agents."),
    rebuild: bool = typer.Option(False, "--rebuild", help="Ignore the cache."),
) -> None:
    """The project's context pack — what an agent is told about it."""
    from navig.projects.context_pack import build_pack, pin_pack  # noqa: PLC0415
    from navig.projects.docs import DocError  # noqa: PLC0415
    from navig.projects.store import get_project_store  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    _pid, root = _resolve(project_id)
    manifest = load_space_manifest(root)
    pack = build_pack(root, name=manifest.resolved_name or root.name, manifest=manifest.data,
                      store=get_project_store(), force=rebuild)
    if pin:
        try:
            res = pin_pack(root, pack)
        except DocError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        if not as_json:
            ch.success(f"Pinned to {res['path']}", f"{res['chars']} chars")
    if as_json:
        ch.emit_json(pack)
        return
    if not report and not pin:
        ch.console.print(pack["markdown"], markup=False, highlight=False)

    from navig.console_helper import Table  # noqa: PLC0415

    rep = pack["report"]
    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Source", no_wrap=True)
    table.add_column("Injected", no_wrap=True, justify="right")
    table.add_column("Raw", no_wrap=True, justify="right", style="dim")
    table.add_column("Budget", no_wrap=True, justify="right", style="dim")
    table.add_column("State", no_wrap=True)
    table.add_column("Files")
    for s in rep["sources"]:
        state = ("[yellow]✂ truncated[/yellow]" if s["truncated"]
                 else "[yellow]● near budget[/yellow]" if s["warn"]
                 else "[green]● ok[/green]" if s["injected_chars"] else "[dim]○ empty[/dim]")
        table.add_row(s["label"], f"{s['injected_chars']:,}", f"{s['raw_chars']:,}", f"{s['budget']:,}",
                      state, ", ".join(s["files"][:3]))
    ch.console.print(table)
    ch.dim(f"{rep['total_injected']:,} / {rep['total_budget']:,} chars (~{rep['approx_tokens']:,} tokens)"
           + (" · cached" if pack.get("cached") else "") + " · pin it: --pin")


@project_app.command("scripts")
def scripts_cmd(
    project_id: str = typer.Argument(None, help="Project id (default: the one you are in)."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Every runnable script — root and workspace packages."""
    from navig.projects.profile import build_profile  # noqa: PLC0415

    _pid, root = _resolve(project_id)
    scripts: list[dict[str, Any]] = build_profile(root).get("scripts", [])
    if as_json:
        ch.emit_json({"scripts": scripts, "count": len(scripts)})
        return
    if not scripts:
        ch.info("No scripts found.", "Looked in package.json (and workspaces), pyproject, Makefile, justfile.")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Where", no_wrap=True, style="dim")
    table.add_column("Script", style="bold", no_wrap=True)
    table.add_column("Run", no_wrap=True)
    table.add_column("Command")
    for s in scripts:
        flag = " [red]⚠[/red]" if s["danger"] else (" [cyan]↻[/cyan]" if s["long_running"] else "")
        table.add_row(s["cwd"] or ".", s["name"] + flag, s["run"], s["command"])
    ch.console.print(table)
    ch.dim(f"{len(scripts)} script(s) · ⚠ deploys/publishes/deletes · ↻ long-running")


@project_app.command("todos")
def todos_cmd(
    project_id: str = typer.Argument(None, help="Project id (default: the one you are in)."),
    all_todos: bool = typer.Option(False, "--all", "-a", help="Include done items."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """The checkboxes across the project's docs, as one list."""
    from navig.projects.todos import extract_todos, summarize  # noqa: PLC0415

    _pid, root = _resolve(project_id)
    todos = extract_todos(root)
    counts = summarize(todos)
    shown = todos if all_todos else [t for t in todos if t["state"] != "done"]
    if as_json:
        ch.emit_json({"todos": shown, "counts": counts})
        return
    if not shown:
        ch.info("Nothing open.", f"{counts['done']} done across the docs.")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("State", no_wrap=True)
    table.add_column("Todo")
    table.add_column("Where", no_wrap=True, style="dim")
    glyph = {"open": "[dim]○[/dim]", "in_progress": "[yellow]◐[/yellow]", "done": "[green]●[/green]"}
    for t in shown[:200]:
        table.add_row(glyph[t["state"]], t["text"], f"{t['path']}:{t['line'] + 1}")
    ch.console.print(table)
    ch.dim(f"{counts['open']} open · {counts['in_progress']} in progress · {counts['done']} done")


# ── ideas + the Idea Fabric ─────────────────────────────────────────────────


@project_app.command("ideas")
def ideas_cmd(
    project_id: str = typer.Argument(None, help="Project id (default: the one you are in)."),
    add: str = typer.Option(None, "--add", "-a", help="Capture an idea (use #tags inline)."),
    stage: str = typer.Option("inbox", "--stage", help="Stage for --add: inbox, exploring, parked."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Ideas not planned yet — by stage (IDEAS.md). Capture one with --add."""
    from navig.projects import ideas as _ideas  # noqa: PLC0415
    from navig.projects.todos import TodoError  # noqa: PLC0415

    _pid, root = _resolve(project_id)
    if add:
        try:
            res = _ideas.add_idea(root, add, stage=stage)
        except TodoError as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        ch.success(f"Captured: {res['idea']['text']}", _ideas.ideas_path(root))
        return
    state = _ideas.list_ideas(root)
    if as_json:
        ch.emit_json(state)
        return
    if not state["ideas"]:
        ch.info("No ideas yet.", 'Capture one: navig project ideas --add "…"   ·   or brainstorm: navig project fabric brainstorm')
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Stage", no_wrap=True)
    table.add_column("Idea")
    table.add_column("", no_wrap=True, style="dim")
    tone = {"inbox": "cyan", "exploring": "yellow", "parked": "dim", "promoted": "green", "dropped": "dim"}
    for i in state["ideas"]:
        extra = f"→ {i['promoted_to']}" if i.get("promoted_to") else ("brief" if i.get("brief") else "")
        table.add_row(f"[{tone[i['stage']]}]{i['stage']}[/{tone[i['stage']]}]", i["text"], extra)
    ch.console.print(table)
    c = state["counts"]
    ch.dim(f"{c['inbox']} new · {c['exploring']} exploring · {c['parked']} parked · {c['promoted']} promoted"
           f" · {len(state['docs'])} idea doc(s) · {state['path']}")


@project_app.command("fabric")
def fabric_cmd(
    pattern: str = typer.Argument(None, help="Pattern to run (omit to list them)."),
    project_id: str = typer.Option(None, "--project", "-p", help="Project id (default: the one you are in)."),
    idea: str = typer.Option(None, "--idea", help="For idea patterns: text the idea contains."),
    doc: str = typer.Option(None, "--doc", help="For doc patterns: the document path."),
    sessions: int = typer.Option(0, "--sessions", help="For harvest: use the N newest sessions about this project."),
    focus: str = typer.Option("", "--focus", help="Steer the pattern (e.g. 'retention')."),
    apply: bool = typer.Option(False, "--apply", help="Accept the result (ideas → inbox, brief/plan → files)."),
) -> None:
    """The Idea Fabric: run an AI pattern over the project — brainstorm, harvest, expand, critique, plan."""
    from navig.agent import project_analyst as pa  # noqa: PLC0415
    from navig.projects import ideas as _ideas  # noqa: PLC0415
    from navig.projects.context_pack import build_pack  # noqa: PLC0415
    from navig.projects.docs import DocError, read_doc  # noqa: PLC0415
    from navig.projects.patterns import PatternError, get_pattern, list_patterns  # noqa: PLC0415
    from navig.projects.sessions import find_sessions, read_source  # noqa: PLC0415
    from navig.projects.store import get_project_store  # noqa: PLC0415
    from navig.projects.todos import extract_todos  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    _pid, root = _resolve(project_id)
    if not pattern:
        from navig.console_helper import Table  # noqa: PLC0415

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Pattern", style="bold", no_wrap=True)
        table.add_column("Takes", no_wrap=True, style="dim")
        table.add_column("Gives", no_wrap=True, style="dim")
        table.add_column("What it does")
        for p in list_patterns(root):
            table.add_row(p["id"] + (" [dim](project)[/dim]" if p["scope"] == "project" else ""),
                          p["input"], p["output"], p["description"])
        ch.console.print(table)
        ch.dim("add your own: .navig/patterns/<name>.md  ·  run one: navig project fabric brainstorm --focus …")
        return
    try:
        pat = get_pattern(pattern, root)
    except PatternError as exc:
        ch.error(str(exc), "List them: navig project fabric")
        raise typer.Exit(1) from exc

    state = _ideas.list_ideas(root)
    manifest = load_space_manifest(root)
    pack = build_pack(root, name=manifest.resolved_name or root.name, manifest=manifest.data,
                      store=get_project_store())
    kw: dict[str, Any] = {"focus": focus, "existing_ideas": [i["text"] for i in state["ideas"]],
                          "existing_todos": [t["text"] for t in extract_todos(root) if t["state"] != "done"]}
    # Validate the input BEFORE the try below: typer.Exit is a RuntimeError, so raising it
    # inside would be caught and reported as an empty error.
    target = None
    found: list[dict[str, Any]] = []
    if pat["input"] == "idea":
        matches = [i for i in state["ideas"] if idea and idea.lower() in i["text"].lower()]
        if len(matches) != 1:
            ch.error("Name exactly one idea with --idea <words it contains>",
                     f"{len(matches)} ideas match" if matches else "See them: navig project ideas")
            raise typer.Exit(1)
        target = matches[0]
    elif pat["input"] == "sources":
        found = find_sessions(root)["sources"][: max(0, sessions)]
        if not found:
            ch.error("Harvest needs sources: --sessions N (the N newest sessions about this project)")
            raise typer.Exit(1)
    try:
        if target is not None:
            kw.update(input_label="IDEA", input_text=target["text"] + "\n" + "\n".join(target["notes"]))
        elif pat["input"] == "doc":
            d = read_doc(root, doc or "")
            kw.update(input_label=f"DOCUMENT {d['path']}", input_text=d["content"])
        elif found:
            kw["sources"] = [read_source(root, s["id"]) for s in found]
        with ch.console.status(f"Running {pat['title']}…"):
            out = pa.run_pattern(pat, pack, **kw)
    except pa.AnalystUnavailableError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    except (DocError, RuntimeError, ValueError, OSError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc

    get_project_store().add_analysis(root, "pattern", {**out, "pattern": pat["id"], "focus": focus},
                                     fingerprint=pack["fingerprint"])
    if out["kind"] == "ideas":
        for i in out["ideas"]:
            ch.console.print(f"  ○ {i['text']}  [dim]{i['why']}[/dim]")
        if apply and out["ideas"]:
            for i in out["ideas"]:
                _ideas.add_idea(root, i["text"], notes=[n for n in (i["why"], f"from: {i['source']}" if i["source"] else "") if n])
            ch.success(f"Added {len(out['ideas'])} idea(s) to the inbox", state["path"])
        elif out["ideas"]:
            ch.dim(f"{len(out['ideas'])} proposed · keep them: --apply")
        else:
            ch.info("Nothing new — everything it found is already captured.")
        return
    ch.console.print(out.get("markdown", ""), markup=False, highlight=False)
    if apply and out["kind"] == "doc" and target:
        res = _ideas.save_brief(root, target["line"], out["markdown"], expect=target["fingerprint"])
        ch.success(f"Saved {res['brief_path']}")
    elif apply and out["kind"] == "plan":
        res = _ideas.adopt_plan(root, out["markdown"], line=target["line"] if target else None,
                                expect=target["fingerprint"] if target else None)
        ch.success(f"Plan saved: {res['path']}", "its tasks are now in navig project todos")
    elif not apply:
        ch.dim("keep it: --apply")


# ── AI analysis ─────────────────────────────────────────────────────────────


@project_app.command("analyze")
def analyze_cmd(
    project_id: str = typer.Argument(None, help="Project id (default: the one you are in)."),
    doc: str = typer.Option(None, "--doc", help="Re-analyse one doc, e.g. .navig/plans/CURRENT_PHASE.md (shows a diff)."),
    todos: bool = typer.Option(False, "--todos", help="Propose todos from briefs, inbox and specs."),
    apply: bool = typer.Option(False, "--apply", help="Write the proposal (asks first; refuses if the doc changed meanwhile)."),
    into: str = typer.Option(".navig/plans/TASKS.md", "--into", help="With --todos --apply: the doc to add them to."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Re-analyse a project with AI — a brief, a doc update, or new todos. Proposes; you accept."""
    from navig.agent import project_analyst as pa  # noqa: PLC0415
    from navig.projects.context_pack import build_pack  # noqa: PLC0415
    from navig.projects.docs import DocError, index_docs, read_doc, save_doc  # noqa: PLC0415
    from navig.projects.store import get_project_store  # noqa: PLC0415
    from navig.projects.todos import TodoError, add_todo, extract_todos  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    _pid, root = _resolve(project_id)
    manifest = load_space_manifest(root)
    store = get_project_store()
    pack = build_pack(root, name=manifest.resolved_name or root.name, manifest=manifest.data, store=store)
    try:
        with ch.console.status("Analysing…"):
            if doc:
                current = read_doc(root, doc)
                result = pa.reanalyze_doc(pack, current["path"], current["content"])
            elif todos:
                index = index_docs(root)
                chosen = sorted((d for d in index if d["group"] in ("briefs", "inbox", "ideas", "tasks")
                                 or Path(d["path"]).stem.upper() == "SPEC"),
                                key=lambda d: -float(d.get("mtime") or 0))[:8]
                texts = [(d["path"], read_doc(root, d["path"])["content"]) for d in chosen]
                result = pa.extract_todos(texts, [t["text"] for t in extract_todos(root, index)])
            else:
                result = pa.brief(pack, pa.doc_freshness(root, index_docs(root)))
    except pa.AnalystUnavailableError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    except (DocError, RuntimeError, ValueError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc

    kind = "doc" if doc else "todos" if todos else "brief"
    saved = store.add_analysis(root, kind, result, target=doc, fingerprint=pack["fingerprint"])
    if as_json:
        ch.emit_json(saved)
        return

    if kind == "brief":
        ch.console.print(result["markdown"], markup=False, highlight=False)
        return

    if kind == "doc":
        import difflib  # noqa: PLC0415

        if not result["changed"]:
            ch.success("Up to date — the analyst proposes no change.")
            return
        diff = difflib.unified_diff(current["content"].splitlines(), result["proposed"].splitlines(),
                                    fromfile=f"{doc} (now)", tofile=f"{doc} (proposed)", lineterm="")
        for line in diff:
            style = "green" if line.startswith("+") else "red" if line.startswith("-") else "dim"
            ch.console.print(f"[{style}]{line}[/{style}]", markup=True, highlight=False, soft_wrap=True)
        if apply and typer.confirm("Write this to the file?"):
            try:
                save_doc(root, current["path"], result["proposed"], expect=current["fingerprint"])
            except DocError as exc:
                ch.error(str(exc))
                raise typer.Exit(1) from exc
            store.set_analysis_status(saved["id"], "accepted")
            ch.success(f"Updated {current['path']}")
        elif not apply:
            ch.dim("write it: add --apply")
        return

    items = result["todos"]
    if not items:
        ch.info("No new todos — everything in the sources is already tracked.")
        return
    for t in items:
        where = f" [dim]({t['heading']})[/dim]" if t["heading"] else ""
        ch.console.print(f"  ○ {t['text']}{where}")
    if apply and typer.confirm(f"Add {len(items)} todo(s) to {into}?"):
        try:
            for t in items:
                add_todo(root, into, t["text"], heading=t["heading"] or None)
        except (DocError, TodoError) as exc:
            ch.error(str(exc))
            raise typer.Exit(1) from exc
        store.set_analysis_status(saved["id"], "accepted")
        ch.success(f"Added {len(items)} todo(s) to {into}")
    elif not apply:
        ch.dim(f"{len(items)} proposed · add them: --apply [--into {into}]")


# ── runs ────────────────────────────────────────────────────────────────────

_STATE_GLYPH = {
    "running": "[green]● running[/green]", "starting": "[cyan]◌ starting[/cyan]",
    "exited": "[dim]○ exited 0[/dim]", "failed": "[red]✗ failed[/red]",
    "stopped": "[yellow]■ stopped[/yellow]", "lost": "[yellow]? lost[/yellow]",
}


def _match_script(root: Path, wanted: str) -> dict[str, Any]:
    """A script by key, or by name when exactly one script has that name."""
    from navig.projects.profile import build_profile  # noqa: PLC0415

    scripts = build_profile(root).get("scripts", [])
    exact = [s for s in scripts if s["key"] == wanted]
    if exact:
        return exact[0]
    by_name = [s for s in scripts if s["name"] == wanted]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        ch.error(f"'{wanted}' exists in {len(by_name)} packages — pass its key",
                 "  ".join(s["key"] for s in by_name))
    else:
        ch.error(f"No script '{wanted}'", "List them with: navig project scripts")
    raise typer.Exit(1)


@project_app.command("run")
def run_cmd(
    script: str = typer.Argument(..., help="Script name (or key like npm:apps/web:dev)."),
    project_id: str = typer.Option(None, "--project", "-p", help="Project id (default: the one you are in)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before a script that deploys/publishes/deletes."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream its output until it exits (Ctrl-C detaches)."),
) -> None:
    """Start a project script in the background (it keeps running if navig restarts)."""
    from navig.projects.runner import RunError, get_runner  # noqa: PLC0415

    pid, root = _resolve(project_id)
    s = _match_script(root, script)
    if s["danger"] and not yes:
        if not typer.confirm(f"'{s['name']}' looks like it deploys, publishes or deletes ({s['command']}). Run it?"):
            raise typer.Exit(1)
    try:
        run = get_runner().start(pid, root, s["key"])
    except RunError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    if run.get("already_running"):
        ch.info(f"{s['name']} is already running", f"run {run['id']}")
    else:
        ch.success(f"Started {s['name']}", f"run {run['id']} · {s['run']}")
    if follow:
        _follow(run["id"])
    else:
        ch.dim(f"logs: navig project logs {run['id']} --follow  ·  stop: navig project stop {run['id']}")


@project_app.command("stop")
def stop_cmd(run_id: str = typer.Argument(..., help="Run id (see navig project ps).")) -> None:
    """Stop a run and every process it started."""
    from navig.projects.runner import RunError, get_runner  # noqa: PLC0415

    try:
        view = get_runner().stop(run_id)
    except RunError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    ch.success(f"Stopped {view['name']}", f"run {view['id']} · {view['state']}")


@project_app.command("ps")
def ps_cmd(
    all_runs: bool = typer.Option(False, "--all", "-a", help="Include finished runs."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Project scripts running now (on this machine)."""
    from navig.projects.runner import get_runner  # noqa: PLC0415

    runs = get_runner().list_runs(include_finished=all_runs)
    if as_json:
        ch.emit_json({"runs": runs, "count": len(runs)})
        return
    if not runs:
        ch.info("Nothing running.", "Start one: navig project run <script>")
        return

    from navig.console_helper import Table  # noqa: PLC0415

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Run", style="dim", no_wrap=True)
    table.add_column("Project", style="bold", no_wrap=True)
    table.add_column("Script", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Ports", no_wrap=True)
    table.add_column("Started", style="dim")
    for r in runs:
        state = _STATE_GLYPH.get(r["state"], r["state"])
        if r["state"] == "failed" and r.get("exit_code") is not None:
            state = f"[red]✗ exit {r['exit_code']}[/red]"
        ports = " ".join(f"[cyan]:{p}[/cyan]" for p in r.get("ports") or []) or "—"
        table.add_row(r["id"], str(r.get("project_id")), str(r.get("name")), state, ports,
                      str(r.get("started_at") or "")[:19].replace("T", " "))
    ch.console.print(table)
    live = sum(1 for r in runs if r["state"] in ("running", "starting"))
    ch.dim(f"{live} running · logs: navig project logs <run> --follow · stop: navig project stop <run>")


def _follow(run_id: str) -> None:
    import time  # noqa: PLC0415

    from navig.projects.runner import get_runner  # noqa: PLC0415

    runner = get_runner()
    cursor = None
    try:
        while True:
            chunk = runner.tail(run_id, cursor)
            if chunk["text"]:
                ch.console.file.write(chunk["text"])
                ch.console.file.flush()
            cursor = chunk["cursor"]
            if chunk["state"] not in ("starting", "running"):
                code = chunk.get("exit_code")
                if chunk["state"] == "failed":
                    ch.error(f"Run ended: {chunk['state']}" + (f" (exit {code})" if code is not None else ""))
                    raise typer.Exit(1)
                ch.dim(f"run ended: {chunk['state']}")
                return
            time.sleep(0.5)
    except KeyboardInterrupt:
        ch.dim(f"detached — it keeps running · stop: navig project stop {run_id}")


@project_app.command("logs")
def logs_cmd(
    run_id: str = typer.Argument(..., help="Run id (see navig project ps)."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Keep streaming until it exits."),
) -> None:
    """Show a run's output."""
    from navig.projects.runner import RunError, get_runner  # noqa: PLC0415

    try:
        if follow:
            _follow(run_id)
            return
        chunk = get_runner().tail(run_id, None)
    except RunError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc
    ch.console.file.write(chunk["text"])
    ch.console.file.flush()

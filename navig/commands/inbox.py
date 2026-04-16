"""
CLI commands for the Inbox Router Agent.

Provides three commands:
  navig inbox process-current <file>  — process a single inbox file
  navig inbox process-all             — process all inbox .md files
  navig inbox dry-run                 — preview routing without writing

All commands use the InboxRouterAgent and execute_plan from
navig.agents.inbox_router.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from navig.commands.plans import _find_project_root

logger = logging.getLogger("navig.commands.inbox")

inbox_app = typer.Typer(
    name="inbox",
    help="Inbox Router — classify and route .navig/plans/inbox/ files",
    invoke_without_command=True,
    no_args_is_help=False,
)


@inbox_app.callback()
def _inbox_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            print(ctx.get_help())
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("inbox", inbox_app)


def _print_plan(plan: dict, verbose: bool = False) -> None:
    """Pretty-print a single plan result."""
    source = Path(plan.get("source_file", "?")).name
    ctype = plan.get("content_type", "?")
    confidence = plan.get("confidence", "?")
    target = plan.get("target_path") or "(stays in inbox)"
    space = plan.get("space") or "life"
    space_source = plan.get("space_source") or "default"
    rationale = plan.get("rationale", "")

    status_icon = {
        "task_roadmap": "[PLAN]",
        "brief": "[BRIEF]",
        "wiki_knowledge": "[WIKI]",
        "memory_log": "[MEM]",
        "other": "[?]",
    }.get(ctype, "[?]")

    typer.echo(f"  {status_icon} {source}")
    typer.echo(f"    Type: {ctype}  Confidence: {confidence}")
    typer.echo(f"    Space: {space}  Source: {space_source}")
    typer.echo(f"    Target: {target}")
    if rationale:
        typer.echo(f"    Rationale: {rationale}")
    if plan.get("error"):
        typer.secho(f"    Error: {plan['error']}", fg=typer.colors.RED)
    typer.echo("")


def _print_execution_result(result: dict) -> None:
    """Pretty-print an execution result."""
    status = result.get("status", "?")
    source = Path(result.get("source", "?")).name

    colors = {
        "written": typer.colors.GREEN,
        "dry_run": typer.colors.YELLOW,
        "kept_in_inbox": typer.colors.CYAN,
        "error": typer.colors.RED,
        "skipped": typer.colors.WHITE,
    }
    color = colors.get(status, typer.colors.WHITE)

    typer.secho(f"  [{status.upper()}] {source}", fg=color)
    if result.get("target"):
        typer.echo(f"    -> {result['target']}")
    if result.get("would_write"):
        typer.echo(f"    -> (would write) {result['would_write']}")
    if result.get("source_moved"):
        typer.echo(f"    Source moved to: {result['source_moved']}")
    if result.get("reason"):
        typer.echo(f"    Reason: {result['reason']}")
    if result.get("error"):
        typer.secho(f"    Error: {result['error']}", fg=typer.colors.RED)


@inbox_app.command("process-current")
def process_current(
    file: str = typer.Argument(..., help="Path to a single inbox .md file"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Use heuristic only (no LLM)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    no_move: bool = typer.Option(False, "--no-move", help="Don't move source after routing"),
    space: str | None = typer.Option(
        None,
        "--space",
        help="Manual space tag override (wins over frontmatter/classifier)",
    ),
    backend: str = typer.Option(
        "cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"
    ),
) -> None:
    """Process a single inbox file — classify, transform, and route."""
    from navig.agents.inbox_router import InboxRouterAgent, execute_plan

    file_path = Path(file).resolve()
    if not file_path.exists():
        typer.secho(f"File not found: {file}", fg=typer.colors.RED)
        raise typer.Exit(1)

    project_root = _find_project_root()
    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)

    typer.echo(f"Processing: {file_path.name}")
    plan = agent.process_single(file_path, dry_run=dry_run, manual_space=space)

    if json_output:
        typer.echo(json.dumps(plan, indent=2, default=str))
        return

    _print_plan(plan)

    if not dry_run and not plan.get("error"):
        result = execute_plan(project_root, plan, dry_run=False, move_source=not no_move)
        _print_execution_result(result)
    elif dry_run:
        result = execute_plan(project_root, plan, dry_run=True)
        _print_execution_result(result)


@inbox_app.command("process-all")
def process_all(
    no_llm: bool = typer.Option(False, "--no-llm", help="Use heuristic only (no LLM)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    no_move: bool = typer.Option(False, "--no-move", help="Don't move source after routing"),
    space: str | None = typer.Option(
        None,
        "--space",
        help="Manual space tag override applied to all files",
    ),
    backend: str = typer.Option(
        "cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"
    ),
) -> None:
    """Process ALL .md files in .navig/plans/inbox/."""
    from navig.agents.inbox_router import (
        InboxRouterAgent,
        execute_plan,
        list_inbox_files,
    )

    project_root = _find_project_root()
    files = list_inbox_files(project_root)

    if not files:
        typer.secho("No inbox files found in .navig/plans/inbox/", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    typer.echo(f"Found {len(files)} inbox file(s)\n")

    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)
    plans = agent.process_batch(files, dry_run=dry_run, manual_space=space)

    if json_output:
        typer.echo(json.dumps(plans, indent=2, default=str))
        return

    results = []
    for plan in plans:
        _print_plan(plan)
        if not dry_run and not plan.get("error"):
            result = execute_plan(project_root, plan, dry_run=False, move_source=not no_move)
        else:
            result = execute_plan(project_root, plan, dry_run=True)
        results.append(result)
        _print_execution_result(result)

    # Summary
    written = sum(1 for r in results if r.get("status") == "written")
    kept = sum(1 for r in results if r.get("status") == "kept_in_inbox")
    errors = sum(1 for r in results if r.get("status") == "error")
    typer.echo(f"\nSummary: {written} routed, {kept} kept in inbox, {errors} errors")


@inbox_app.command("dry-run")
def dry_run(
    no_llm: bool = typer.Option(False, "--no-llm", help="Use heuristic only (no LLM)"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    space: str | None = typer.Option(
        None,
        "--space",
        help="Manual space tag override applied to all files",
    ),
    backend: str = typer.Option(
        "cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"
    ),
) -> None:
    """Preview routing for all inbox files (no files written or moved)."""
    from navig.agents.inbox_router import (
        InboxRouterAgent,
        execute_plan,
        list_inbox_files,
    )

    project_root = _find_project_root()
    files = list_inbox_files(project_root)

    if not files:
        typer.secho("No inbox files found in .navig/plans/inbox/", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    typer.echo(f"Dry-run preview for {len(files)} inbox file(s)\n")

    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)
    plans = agent.process_batch(files, dry_run=True, manual_space=space)

    if json_output:
        typer.echo(json.dumps(plans, indent=2, default=str))
        return

    for plan in plans:
        _print_plan(plan, verbose=True)
        result = execute_plan(project_root, plan, dry_run=True)
        _print_execution_result(result)

    # Summary
    types_count = {}
    for p in plans:
        ct = p.get("content_type", "other")
        types_count[ct] = types_count.get(ct, 0) + 1
    typer.echo("\nClassification summary:")
    for ct, count in sorted(types_count.items()):
        typer.echo(f"  {ct}: {count}")


@inbox_app.command("filter")
def filter_cmd(
    path: str | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Project root (default: auto-detected from cwd)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only — no files written"),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Keep running and re-filter on changes"
    ),
    interval: float = typer.Option(5.0, "--interval", "-i", help="Watch interval in seconds"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Filter and normalize all .navig/**/*.md files in-place.

    Adds missing YAML frontmatter, normalizes heading structure, and writes
    each file back.  Files inside .navig/plans/inbox/ are always skipped —
    they are router inputs, not processed documents.

    \b
    Examples:
      navig inbox filter
      navig inbox filter --dry-run
      navig inbox filter --watch --interval 10
    """
    from navig.agents.filtering_engine import FilteringEngine

    project_root = Path(path).resolve() if path else _find_project_root()

    if not (project_root / ".navig").is_dir():
        typer.secho(
            f"No .navig/ directory found at {project_root}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    engine = FilteringEngine(project_root)

    if watch:
        typer.secho(
            f"Watching .navig/ under {project_root} (interval={interval}s) — Ctrl+C to stop",
            fg=typer.colors.CYAN,
        )
        try:
            engine.start_watch(interval_secs=interval, dry_run=dry_run)
        except KeyboardInterrupt:
            typer.echo("\nWatch stopped.")
        return

    # Single-pass scan
    typer.echo(f"Filtering .navig/ under {project_root} {'[dry-run]' if dry_run else ''}\n")
    results = engine.scan_and_filter(dry_run=dry_run)

    if json_output:
        typer.echo(
            __import__("json").dumps(
                [
                    {
                        "path": str(r.path),
                        "changed": r.changed,
                        "would_change": r.would_change,
                        "skipped": r.skipped,
                        "error": r.error,
                        "rules_applied": r.rules_applied,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return

    if not results:
        typer.secho("All files are already clean — nothing to do.", fg=typer.colors.GREEN)
        return

    changed = 0
    would_change = 0
    errors = 0

    for r in results:
        name = r.path.name
        if r.error:
            typer.secho(f"  [ERROR] {name}: {r.error}", fg=typer.colors.RED)
            errors += 1
        elif r.changed:
            typer.secho(f"  [UPDATED] {name}  rules={r.rules_applied}", fg=typer.colors.GREEN)
            changed += 1
        elif r.would_change:
            typer.secho(
                f"  [WOULD UPDATE] {name}  rules={r.rules_applied}",
                fg=typer.colors.YELLOW,
            )
            would_change += 1

    if dry_run:
        typer.echo(f"\nDry-run: {would_change} would be updated, {errors} errors.")
    else:
        typer.echo(f"\nDone: {changed} updated, {errors} errors.")


@inbox_app.command("watch")
def watch_cmd(
    path: str | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Project root (default: auto-detected from cwd)",
    ),
    interval: float = typer.Option(5.0, "--interval", "-i", help="Poll interval in seconds"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Detect and report changes only"),
) -> None:
    """Watch .navig/**/*.md for changes and re-filter automatically.

    Alias for: navig inbox filter --watch

    \b
    Examples:
      navig inbox watch
      navig inbox watch --interval 10 --dry-run
    """
    from navig.agents.filtering_engine import FilteringEngine

    project_root = Path(path).resolve() if path else _find_project_root()

    if not (project_root / ".navig").is_dir():
        typer.secho(
            f"No .navig/ directory found at {project_root}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    engine = FilteringEngine(project_root)
    typer.secho(
        f"Watching .navig/ under {project_root} (interval={interval}s) — Ctrl+C to stop",
        fg=typer.colors.CYAN,
    )
    try:
        engine.start_watch(interval_secs=interval, dry_run=dry_run)
    except KeyboardInterrupt:
        typer.echo("\nWatch stopped.")


@inbox_app.command("stats")
def stats_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Show routing summary statistics from the inbox SQLite store."""
    from navig.inbox.store import InboxStore

    store = InboxStore()
    data = store.stats()

    if json_output:
        typer.echo(json.dumps(data, indent=2))
        return

    total = data.get("total_events", 0)
    by_status = data.get("by_status", {})
    by_category = data.get("by_category", {})

    typer.secho("Inbox Statistics", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  Total events  : {total}")
    typer.echo("")
    typer.secho("  By status:", fg=typer.colors.WHITE)
    for status, count in sorted(by_status.items()):
        color = typer.colors.GREEN if status == "routed" else typer.colors.YELLOW
        typer.secho(f"    {status:<12} {count}", fg=color)
    typer.echo("")
    typer.secho("  By category:", fg=typer.colors.WHITE)
    for category, count in sorted(by_category.items(), key=lambda x: -x[1]):
        typer.echo(f"    {category:<26} {count}")


@inbox_app.command("add")
def add_url_cmd(
    url: str = typer.Argument(..., help="URL to fetch, classify, and route"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Use BM25 classifier only"),
    mode: str = typer.Option("copy", "--mode", "-m", help="Route mode: copy | move | link"),
    space: str | None = typer.Option(
        None,
        "--space",
        help="Manual space tag for this intake item",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Fetch a URL, classify it, and route it into the wiki inbox.

    \b
    Examples:
      navig inbox add https://example.com/article
      navig inbox add https://arxiv.org/abs/2401.00001 --mode move
    """
    import hashlib
    import re
    import time
    import urllib.error
    import urllib.request

    from navig.inbox.classifier import Classifier
    from navig.inbox.router import InboxRouter, RouteMode
    from navig.inbox.store import InboxEvent, InboxStore, RoutingDecision
    from navig.spaces import normalize_space_name

    typer.echo(f"Fetching: {url}")

    # Fetch content
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NAVIG/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(204800)  # cap at 200 KB
            content = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        typer.secho(f"Fetch failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    # Derive a filename from URL
    path_part = url.rstrip("/").split("/")[-1] or "index"
    path_part = re.sub(r"[^a-zA-Z0-9_-]", "_", path_part)
    filename = f"{path_part}.md"

    # Strip HTML tags for classification
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", text).strip()

    # Classify
    classifier = Classifier(use_llm=not no_llm)
    result = classifier.classify(text, filename=filename, extra_context=url)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "url": url,
                    "filename": filename,
                    "category": result.category,
                    "confidence": result.confidence,
                    "method": result.method,
                },
                indent=2,
            )
        )
        if dry_run:
            return

    typer.echo(f"  Category  : {result.category}")
    typer.echo(f"  Confidence: {result.confidence:.2%}  ({result.method})")

    # Build markdown content
    selected_space = normalize_space_name(space)
    md = (
        f"---\n"
        f"space: {selected_space}\n"
        f"tags: [{selected_space}]\n"
        f"source: {url}\n"
        f"fetched_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"category: {result.category}\n"
        f"confidence: {result.confidence}\n"
        f"---\n\n"
        f"# {path_part}\n\n"
        f"Source: {url}\n\n"
        f"{text[:8000]}\n"
    )

    # Route
    project_root = _find_project_root()
    router_mode = RouteMode(mode) if mode in ("copy", "move", "link") else RouteMode.COPY
    router = InboxRouter(project_root=project_root, mode=router_mode)
    route_result = router.route_url(url, md, filename, result, dry_run=dry_run)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "route_status": route_result.status,
                    "destination": route_result.destination,
                    "result_path": route_result.result_path,
                },
                indent=2,
            )
        )
        return

    if route_result.status == "routed":
        dest = route_result.result_path or route_result.destination
        if dry_run:
            typer.secho(f"  [dry-run] Would write → {dest}", fg=typer.colors.YELLOW)
        else:
            typer.secho(f"  ✓ Routed  → {dest}", fg=typer.colors.GREEN)
            # Persist to store
            store = InboxStore()
            event = InboxEvent(
                source_path=url,
                source_type="url",
                filename=filename,
                size_bytes=len(md.encode()),
                content_hash=hashlib.sha256(md.encode()).hexdigest()[:16],
                status="routed",
            )
            event_id = store.insert_event(event)
            decision = RoutingDecision(
                event_id=event_id,
                category=result.category,
                confidence=result.confidence,
                mode=mode,
                destination=route_result.destination or "",
                result_path=route_result.result_path,
                executed=True,
                classifier=result.method,
            )
            store.insert_decision(decision)
    else:
        typer.secho(
            f"  {route_result.status}: {route_result.error or 'no destination'}",
            fg=typer.colors.YELLOW,
        )


@inbox_app.command("ui")
def ui_cmd(
    path: str | None = typer.Option(
        None, "--path", "-p", help="Project root (default: auto-detected)"
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="BM25 only, no LLM"),
    mode: str = typer.Option("copy", "--mode", "-m", help="Route mode: copy | move | link"),
) -> None:
    """Interactive TUI review panel — inspect inbox files and approve routing.

    Shows each pending file with its classification result. Press:
      [y] Route now   [n] Keep in inbox   [q] Quit   [?] Details
    """
    from navig.inbox.classifier import Classifier
    from navig.inbox.router import InboxRouter, RouteMode
    from navig.inbox.store import InboxEvent, InboxStore, RoutingDecision

    project_root = Path(path).resolve() if path else _find_project_root()
    inbox_dir = project_root / ".navig" / "wiki" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    files = [f for f in inbox_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
    if not files:
        # Also check global inbox
        try:
            from navig.platform.paths import config_dir, navig_data_dir

            global_inbox = navig_data_dir() / "inbox"
        except Exception:
            global_inbox = config_dir() / "inbox"
        if global_inbox.is_dir():
            files += [
                f for f in global_inbox.iterdir() if f.is_file() and not f.name.startswith(".")
            ]

    if not files:
        typer.secho("No inbox files found.", fg=typer.colors.YELLOW)
        return

    classifier = Classifier(use_llm=not no_llm)
    router_mode = RouteMode(mode) if mode in ("copy", "move", "link") else RouteMode.COPY
    router = InboxRouter(project_root=project_root, mode=router_mode)
    store = InboxStore()

    typer.secho(
        f"NAVIG Inbox Review  ({len(files)} file(s))  [y=route  n=skip  q=quit  ?=details]",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo("")

    routed = skipped = 0
    for i, f in enumerate(files, 1):
        content = ""
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        result = classifier.classify(content, filename=f.name)

        typer.secho(f"[{i}/{len(files)}] {f.name}", fg=typer.colors.WHITE, bold=True)
        typer.echo(f"  Category  : {result.category}")
        typer.echo(f"  Confidence: {result.confidence:.2%}  ({result.method})")
        typer.echo(f"  Space     : {result.category}")

        while True:
            choice = typer.prompt("  → route? [y/n/q/?]", default="y").strip().lower()
            if choice == "?":
                typer.echo(f"  Explanation: {result.explanation}")
                if result.alternatives:
                    typer.echo("  Alternatives:")
                    for alt_cat, alt_score in result.alternatives:
                        typer.echo(f"    {alt_cat}: {alt_score:.4f}")
                continue
            break

        if choice == "q":
            typer.echo("Quit.")
            break
        elif choice == "y":
            route_result = router.route(f, result, dry_run=False)
            if route_result.status == "routed":
                typer.secho(f"  ✓ → {route_result.result_path}", fg=typer.colors.GREEN)
                routed += 1
                # Persist
                import hashlib

                event = InboxEvent(
                    source_path=str(f),
                    source_type="file",
                    filename=f.name,
                    size_bytes=f.stat().st_size,
                    content_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                    status="routed",
                )
                event_id = store.insert_event(event)
                decision = RoutingDecision(
                    event_id=event_id,
                    category=result.category,
                    confidence=result.confidence,
                    mode=router_mode.value,
                    destination=route_result.destination or "",
                    result_path=route_result.result_path,
                    executed=True,
                    classifier=result.method,
                )
                store.insert_decision(decision)
            else:
                typer.secho(
                    f"  ✗ {route_result.status}: {route_result.error}",
                    fg=typer.colors.RED,
                )
        else:
            typer.secho("  Kept in inbox.", fg=typer.colors.YELLOW)
            skipped += 1
        typer.echo("")

    typer.secho(f"Done. {routed} routed, {skipped} kept.", fg=typer.colors.CYAN)


@inbox_app.command("reroute")
def reroute_cmd(
    space: str | None = typer.Option(
        None,
        "--space",
        "-s",
        help=(
            "Path to a space root containing .navig/inbox/ "
            "(default: auto-detect from cwd by walking up until .navig/ found)"
        ),
    ),
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Execute redirects. Without this flag the command is always a dry-run.",
    ),
    log_file: str | None = typer.Option(
        None,
        "--log",
        help="Write per-file JSON-lines results to this file (appended).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print results as JSON to stdout."),
    mode: str = typer.Option("move", "--mode", "-m", help="Dispatch mode: move | copy | link"),
) -> None:
    """Re-evaluate routes.yaml exclude rules and redirect misplaced inbox files.

    Reads the ``exclude:`` block from the space's routes.yaml, scores every
    file in the inbox against it, and for each match finds the best sibling
    space (scored against each sibling's channel keywords).

    Dry-run by default — pass --confirm to execute actual moves/copies.

    \b
    Examples:
      navig inbox reroute --space D:/spaces/human --confirm
      navig inbox reroute                          # dry-run from cwd
      navig inbox reroute --mode copy --log out.jsonl
    """
    import json as _json  # noqa: PLC0415

    from navig.inbox.router import InboxRouter, RouteMode  # noqa: PLC0415
    from navig.inbox.routes_loader import load  # noqa: PLC0415
    from navig.inbox.space_scorer import (  # noqa: PLC0415
        check_exclude_rules,
        extract_terms,
        find_best_destination,
    )

    # ── Resolve space root ────────────────────────────────────
    if space:
        space_root = Path(space).resolve()
    else:
        # Walk up from cwd until we find a directory that contains .navig/
        cwd = Path.cwd()
        candidate = cwd
        while True:
            if (candidate / ".navig").is_dir():
                space_root = candidate
                break
            parent = candidate.parent
            if parent == candidate:
                typer.secho(
                    "Could not find a .navig/ directory in cwd or any parent. "
                    "Use --space to specify the space root explicitly.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
            candidate = parent

    inbox_dir = space_root / ".navig" / "inbox"
    if not inbox_dir.is_dir():
        typer.secho(f"No .navig/inbox/ found at {space_root}", fg=typer.colors.RED)
        raise typer.Exit(1)

    config = load(space_root)
    if config is None or not config.exclude:
        typer.secho(
            f"No routes.yaml with exclude rules found for space at {space_root}.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(0)

    spaces_root = config.spaces_root or space_root.parent
    is_dry = not confirm

    try:
        route_mode = RouteMode(mode)
    except ValueError:
        typer.secho(f"Invalid mode '{mode}'. Choose: move, copy, link", fg=typer.colors.RED)
        raise typer.Exit(1) from None

    router = InboxRouter(project_root=space_root, mode=route_mode)

    # ── Scan inbox files ──────────────────────────────────────
    inbox_files: list[Path] = []
    for item in sorted(inbox_dir.rglob("*")):
        if item.is_file() and item.suffix not in {".redirected", ".bak"}:
            # Skip the routes.yaml itself and README files
            if item.name in {"routes.yaml", "README.md"}:
                continue
            inbox_files.append(item)

    if not inbox_files:
        typer.secho("Inbox is empty — nothing to reroute.", fg=typer.colors.CYAN)
        raise typer.Exit(0)

    typer.echo(
        f"Space: {space_root}\n"
        f"Inbox: {inbox_dir}\n"
        f"Files: {len(inbox_files)}\n"
        f"Mode:  {'DRY-RUN (pass --confirm to execute)' if is_dry else 'EXECUTE'}\n"
    )

    # ── Process each file ─────────────────────────────────────
    results: list[dict] = []
    redirected = skipped = errors = 0

    log_handle = None
    if log_file:
        try:
            log_handle = open(log_file, "a", encoding="utf-8")  # noqa: SIM115
        except OSError as exc:
            typer.secho(f"Cannot open log file {log_file}: {exc}", fg=typer.colors.YELLOW)

    for f in inbox_files:
        entry: dict = {"file": str(f), "status": "ok"}
        try:
            raw_text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = f"read error: {exc}"
            errors += 1
            _emit(entry, log_handle, json_output)
            results.append(entry)
            continue

        terms = extract_terms(raw_text)
        rule = check_exclude_rules(terms, config) if terms else None

        if rule is None:
            entry["status"] = "kept"
            entry["reason"] = "no exclude rule matched"
            _emit(entry, log_handle, json_output)
            results.append(entry)
            continue

        dest_inbox = find_best_destination(terms, space_root, spaces_root)
        if dest_inbox is None:
            entry["status"] = "no_destination"
            entry["reason"] = "exclude rule matched but no suitable sibling space found"
            entry["rule_keywords"] = rule.keywords[:6]
            typer.secho(
                f"  [NO DEST] {f.name} — exclude rule matched but no sibling scored high enough",
                fg=typer.colors.YELLOW,
            )
            _emit(entry, log_handle, json_output)
            results.append(entry)
            continue

        dest_file = dest_inbox / f.name
        entry["would_redirect_to"] = str(dest_file)
        entry["rule_keywords"] = rule.keywords[:6]
        entry["terms_sample"] = terms[:10]

        if is_dry:
            entry["status"] = "redirect_dry_run"
            typer.secho(
                f"  [DRY-RUN] {f.name}",
                fg=typer.colors.YELLOW,
                nl=False,
            )
            typer.echo(f" → {dest_inbox.parent.name}/{dest_inbox.name}/{f.name}")
            redirected += 1
        else:
            # Execute redirect
            if dest_file.exists():
                if rule.on_conflict == "skip":
                    entry["status"] = "skipped"
                    entry["reason"] = "destination exists and on_conflict=skip"
                    skipped += 1
                    typer.secho(f"  [SKIP] {f.name} — destination exists", fg=typer.colors.WHITE)
                    _emit(entry, log_handle, json_output)
                    results.append(entry)
                    continue
                elif rule.on_conflict == "rename":
                    from navig.inbox.router import _unique_path  # noqa: PLC0415
                    dest_file = _unique_path(dest_file)
                # "overwrite" falls through

            try:
                dest_inbox.mkdir(parents=True, exist_ok=True)
                if route_mode == RouteMode.COPY:
                    import shutil as _shutil  # noqa: PLC0415
                    _shutil.copy2(str(f), str(dest_file))
                elif route_mode == RouteMode.MOVE:
                    import shutil as _shutil  # noqa: PLC0415
                    _shutil.move(str(f), str(dest_file))
                elif route_mode == RouteMode.LINK:
                    if dest_file.exists() or dest_file.is_symlink():
                        dest_file.unlink()
                    try:
                        dest_file.symlink_to(f.resolve())
                    except (NotImplementedError, OSError):
                        import shutil as _shutil  # noqa: PLC0415
                        _shutil.copy2(str(f), str(dest_file))

                # Write traceability sidecar
                sidecar = dest_file.with_suffix(dest_file.suffix + ".redirected")
                from navig.core.yaml_io import atomic_write_text

                atomic_write_text(
                    sidecar,
                    f"redirected_from: {f}\nrule_keywords: {rule.keywords[:6]}\n",
                )

                entry["status"] = "redirected"
                entry["result_path"] = str(dest_file)
                typer.secho(
                    f"  [MOVED] {f.name} → {dest_file}",
                    fg=typer.colors.GREEN,
                )
                redirected += 1
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = str(exc)
                errors += 1
                typer.secho(
                    f"  [ERROR] {f.name}: {exc}",
                    fg=typer.colors.RED,
                )

        _emit(entry, log_handle, json_output)
        results.append(entry)

    if log_handle:
        log_handle.close()

    # ── Summary ───────────────────────────────────────────────
    kept = sum(1 for r in results if r["status"] == "kept")
    typer.echo(
        f"\n{'─' * 50}\n"
        f"  {'Would redirect' if is_dry else 'Redirected'}: {redirected}\n"
        f"  Kept in space:  {kept}\n"
        f"  Skipped:        {skipped}\n"
        f"  Errors:         {errors}\n"
        f"{'─' * 50}"
    )
    if is_dry and redirected:
        typer.secho(
            "\nRe-run with --confirm to execute the redirects.",
            fg=typer.colors.YELLOW,
        )

    if json_output:
        typer.echo(_json.dumps(results, indent=2, default=str))


def _emit(entry: dict, log_handle, json_output: bool) -> None:
    """Write a result entry to the log file if provided. Stdout JSON is handled separately."""
    import json as _json  # noqa: PLC0415

    if log_handle:
        try:
            log_handle.write(_json.dumps(entry, default=str) + "\n")
            log_handle.flush()
        except Exception:
            pass
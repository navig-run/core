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
from typing import Optional

import typer

logger = logging.getLogger("navig.commands.inbox")

inbox_app = typer.Typer(
    name="inbox",
    help="Inbox Router — classify and route .navig/plans/inbox/ files",
    invoke_without_command=True,
    no_args_is_help=True,
)


def _find_project_root() -> Path:
    """Walk up from cwd to find a directory containing .navig/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".navig").is_dir():
            return parent
    # Fallback to cwd
    return cwd


def _print_plan(plan: dict, verbose: bool = False) -> None:
    """Pretty-print a single plan result."""
    source = Path(plan.get("source_file", "?")).name
    ctype = plan.get("content_type", "?")
    confidence = plan.get("confidence", "?")
    target = plan.get("target_path") or "(stays in inbox)"
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
    backend: str = typer.Option("cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"),
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
    plan = agent.process_single(file_path, dry_run=dry_run)

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
    backend: str = typer.Option("cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"),
) -> None:
    """Process ALL .md files in .navig/plans/inbox/."""
    from navig.agents.inbox_router import InboxRouterAgent, execute_plan, list_inbox_files

    project_root = _find_project_root()
    files = list_inbox_files(project_root)

    if not files:
        typer.secho("No inbox files found in .navig/plans/inbox/", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    typer.echo(f"Found {len(files)} inbox file(s)\n")

    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)
    plans = agent.process_batch(files, dry_run=dry_run)

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
    backend: str = typer.Option("cli_llm", "--backend", "-b", help="Backend caller (cli_llm or vscode_copilot)"),
) -> None:
    """Preview routing for all inbox files (no files written or moved)."""
    from navig.agents.inbox_router import InboxRouterAgent, execute_plan, list_inbox_files

    project_root = _find_project_root()
    files = list_inbox_files(project_root)

    if not files:
        typer.secho("No inbox files found in .navig/plans/inbox/", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    typer.echo(f"Dry-run preview for {len(files)} inbox file(s)\n")

    agent = InboxRouterAgent(project_root, use_llm=not no_llm, backend=backend)
    plans = agent.process_batch(files, dry_run=True)

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
    path: Optional[str] = typer.Option(
        None, "--path", "-p",
        help="Project root (default: auto-detected from cwd)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only — no files written"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Keep running and re-filter on changes"),
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
        import dataclasses
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
            typer.secho(f"  [WOULD UPDATE] {name}  rules={r.rules_applied}", fg=typer.colors.YELLOW)
            would_change += 1

    if dry_run:
        typer.echo(f"\nDry-run: {would_change} would be updated, {errors} errors.")
    else:
        typer.echo(f"\nDone: {changed} updated, {errors} errors.")


@inbox_app.command("watch")
def watch_cmd(
    path: Optional[str] = typer.Option(
        None, "--path", "-p",
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
    import urllib.request
    import urllib.error
    import time

    from navig.inbox.classifier import Classifier
    from navig.inbox.router import InboxRouter, RouteMode
    from navig.inbox.store import InboxStore, InboxEvent, RoutingDecision

    typer.echo(f"Fetching: {url}")

    # Fetch content
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NAVIG/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(204800)  # cap at 200 KB
            content_type = resp.headers.get("content-type", "")
            content = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        typer.secho(f"Fetch failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)

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
        import dataclasses
        typer.echo(json.dumps({
            "url": url,
            "filename": filename,
            "category": result.category,
            "confidence": result.confidence,
            "method": result.method,
        }, indent=2))
        if dry_run:
            return

    typer.echo(f"  Category  : {result.category}")
    typer.echo(f"  Confidence: {result.confidence:.2%}  ({result.method})")

    # Build markdown content
    md = (
        f"---\n"
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
        typer.echo(json.dumps({
            "route_status": route_result.status,
            "destination": route_result.destination,
            "result_path": route_result.result_path,
        }, indent=2))
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
    path: Optional[str] = typer.Option(
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
    from navig.inbox.router import InboxRouter, RouteMode, ConflictStrategy
    from navig.inbox.store import InboxStore, InboxEvent, RoutingDecision

    project_root = Path(path).resolve() if path else _find_project_root()
    inbox_dir = project_root / ".navig" / "wiki" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    files = [f for f in inbox_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
    if not files:
        # Also check global inbox
        try:
            from navig.platform.paths import navig_data_dir
            global_inbox = navig_data_dir() / "inbox"
        except Exception:
            global_inbox = Path.home() / ".navig" / "inbox"
        if global_inbox.is_dir():
            files += [f for f in global_inbox.iterdir() if f.is_file() and not f.name.startswith(".")]

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
        except Exception:
            pass
        result = classifier.classify(content, filename=f.name)

        typer.secho(f"[{i}/{len(files)}] {f.name}", fg=typer.colors.WHITE, bold=True)
        typer.echo(f"  Category  : {result.category}")
        typer.echo(f"  Confidence: {result.confidence:.2%}  ({result.method})")

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
                typer.secho(f"  ✗ {route_result.status}: {route_result.error}", fg=typer.colors.RED)
        else:
            typer.secho("  Kept in inbox.", fg=typer.colors.YELLOW)
            skipped += 1
        typer.echo("")

    typer.secho(f"Done. {routed} routed, {skipped} kept.", fg=typer.colors.CYAN)

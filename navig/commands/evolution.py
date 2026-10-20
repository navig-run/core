from pathlib import Path

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

evolution_app = typer.Typer(
    name="evolve",
    help="Auto-evolve skills, workflows, and more",
    no_args_is_help=True,
)


def _report_attempts(result) -> None:
    """Say how the artifact was produced — retries are the interesting part.

    Replaces a `# Could print path` comment that had never been implemented. The
    path is genuinely not available here: `BaseEvolver._save()` computes it
    locally and returns nothing, so surfacing it means changing that contract
    across six evolvers whose signatures have already diverged (`ahk.py` calls
    `evolve(goal, dry_run=...)`). That is its own change, not a rider on this one.
    `attempts` and the cache hit ARE on the result, and answer the question a
    caller actually asks next: did this take retries, or was it already there?
    """
    attempts = getattr(result, "attempts", None)
    if attempts == 0:
        ch.dim("  Reused an existing artifact from the library (no generation needed)")
    elif isinstance(attempts, int) and attempts > 1:
        ch.dim(f"  Took {attempts} attempts")


@evolution_app.command("skill")
def evolve_skill(
    goal: str = typer.Argument(..., help="Description of the skill to create"),
    skills_root: Path | None = typer.Option(None, "--root", "-r", help="Root directory for skills"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate and refine a new skill definition (SKILL.md)."""
    from navig.core.evolution.skill import SkillEvolver

    # skills_root=None lets SkillEvolver default to config_dir()/skills — where the agent reads
    # global skills (skills_context._global_skills_dir) and skill_drafter writes them, so an
    # evolved skill is discoverable. Never inject a CWD-relative default here. An explicit
    # --root/-r still wins.
    evolver = SkillEvolver(skills_root)
    evolver.max_retries = retries

    ch.info(f"Evolving skill for: {goal}")
    result = evolver.evolve(goal)

    if not result.success:
        ch.error(f"Skill evolution failed: {result.error}")
        raise typer.Exit(1)

    ch.success("Skill evolution successful!")
    _report_attempts(result)


@evolution_app.command("workflow")
def evolve_workflow(
    goal: str = typer.Argument(..., help="Description of the workflow to create"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate and refine a new automation workflow (YAML)."""
    from navig.core.evolution.workflow import WorkflowEvolver

    evolver = WorkflowEvolver()
    evolver.max_retries = retries

    ch.info(f"Evolving workflow for: {goal}")
    result = evolver.evolve(goal)

    if not result.success:
        ch.error(f"Workflow evolution failed: {result.error}")
        raise typer.Exit(1)

    ch.success("Workflow evolution successful!")
    _report_attempts(result)


@evolution_app.command("pack")
def evolve_pack(
    goal: str = typer.Argument(..., help="Description of the pack to create"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate a new Pack (collection of skills)."""
    from navig.core.evolution.pack import PackEvolver

    evolver = PackEvolver()
    evolver.max_retries = retries

    ch.info(f"Evolving pack for: {goal}")
    result = evolver.evolve(goal)

    if not result.success:
        ch.error(f"Pack evolution failed: {result.error}")
        raise typer.Exit(1)

    ch.success("Pack evolution successful!")
    _report_attempts(result)


@evolution_app.command("script")
def evolve_script(
    goal: str = typer.Argument(..., help="Description of the script to create"),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate a Python automation script."""
    from navig.core.evolution.script import ScriptEvolver

    evolver = ScriptEvolver()
    evolver.max_retries = retries

    ch.info(f"Evolving script for: {goal}")
    result = evolver.evolve(goal)

    if not result.success:
        ch.error(f"Script evolution failed: {result.error}")
        raise typer.Exit(1)

    ch.success("Script evolution successful!")
    _report_attempts(result)


@evolution_app.command("fix")
def evolve_fix(
    file_path: Path = typer.Argument(..., help="Path to the file to fix"),
    instruction: str = typer.Argument(..., help="Description of the bug or improvement"),
    check: str | None = typer.Option(
        None,
        "--check",
        "-c",
        help="Command to run for validation (use {file} as placeholder)",
    ),
):
    """Attempt to fix or improve an existing file."""
    if not file_path.exists():
        ch.error(f"File not found: {file_path}")
        raise typer.Exit(1)

    from navig.core.evolution.fix import FixEvolver

    evolver = FixEvolver(file_path, check_command=check)

    ch.info(f"Analyzing {file_path.name} for fix: {instruction}")
    result = evolver.evolve(instruction)

    if not result.success:
        ch.error(f"Fix failed: {result.error}")
        raise typer.Exit(1)

    ch.success("File update successful!")
    _report_attempts(result)


def _sweep_config(key: str, default: str) -> str:
    try:
        from navig.config import get_config_manager

        value = get_config_manager().get(f"evolve.sweep.{key}", default)
        return str(value) if value else default
    except Exception:  # noqa: BLE001 — no config is not an error; the default applies
        return default


@evolution_app.command("sweep")
def evolve_sweep(
    path: Path | None = typer.Argument(
        None, help="A folder inside the git repository to sweep (default: current directory)"
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Local Ollama model (default: evolve.sweep.model or qwen2.5-coder:7b)",
    ),
    fallback: str | None = typer.Option(
        None,
        "--fallback",
        help="Bigger model for failing tests the first one cannot fix "
        "(default: evolve.sweep.fallback_model or qwen2.5-coder:32b)",
    ),
    no_fallback: bool = typer.Option(
        False, "--no-fallback", help="Never escalate to the fallback model"
    ),
    lint: bool = typer.Option(True, "--lint/--no-lint", help="Fix ruff lint errors"),
    tests: bool | None = typer.Option(
        None,
        "--tests/--no-tests",
        help="Fix failing pytest tests (default: when the repo has tests)",
    ),
    autofix: bool = typer.Option(
        True, "--autofix/--no-autofix", help="Apply ruff's safe fixes first"
    ),
    select: str | None = typer.Option(
        None,
        "--select",
        help="Extra ruff rules to check, e.g. B,SIM (added to the project's config)",
    ),
    python: str | None = typer.Option(
        None,
        "--python",
        help="Interpreter that runs the tests (default: the project's venv, else py -3)",
    ),
    retries: int = typer.Option(2, "--retries", "-n", min=1, help="Attempts per model per problem"),
    limit: int = typer.Option(50, "--limit", min=1, help="Most problems to attempt per phase"),
    max_minutes: float | None = typer.Option(
        None, "--max-minutes", help="Stop starting new fixes after this long (for overnight runs)"
    ),
    test_timeout: float = typer.Option(
        900, "--test-timeout", help="Seconds allowed for one test-suite run"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be fixed, keep no branch"
    ),
    keep_worktree: bool = typer.Option(
        False, "--keep-worktree", help="Leave the sweep worktree for inspection"
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the report as JSON"),
):
    """Check a repo with ruff + pytest and fix what fails, using a local model.

    Works on a new chore/sweep/… branch in its own worktree — your checkout and any
    uncommitted work are never touched, and nothing is pushed. Every fix is re-checked
    before it is committed; review the branch and merge what you want.
    """
    from navig.core.evolution.sweep import (
        DEFAULT_FALLBACK_MODEL,
        DEFAULT_MODEL,
        SweepError,
        SweepOptions,
        run_sweep,
        save_report,
    )
    from navig.platform.paths import invocation_cwd, resolve_user_path

    target = resolve_user_path(path) if path else invocation_cwd()
    opts = SweepOptions(
        model=model or _sweep_config("model", DEFAULT_MODEL),
        fallback_model=None
        if no_fallback
        else (fallback or _sweep_config("fallback_model", DEFAULT_FALLBACK_MODEL)),
        lint=lint,
        tests=tests,
        autofix=autofix,
        retries=retries,
        limit=limit,
        lint_select=select,
        python=python.split() if python else None,
        test_timeout=test_timeout,
        max_minutes=max_minutes,
        keep_worktree=keep_worktree,
        dry_run=dry_run,
    )
    if not json_out:
        ch.header(
            "🧹 navig evolve sweep",
            f"{target}  ·  {opts.model}"
            + (f" → {opts.fallback_model}" if opts.fallback_model else ""),
        )

    def progress(msg: str) -> None:
        from rich.markup import escape

        ch.console.print(f"[dim]{escape(msg)}[/dim]")

    try:
        report = run_sweep(target, opts, progress=None if json_out else progress)
    except SweepError as exc:
        ch.error(f"Sweep could not start: {exc}")
        raise typer.Exit(1) from exc
    save_report(report)

    if json_out:
        import json as _json

        ch.raw_print(_json.dumps(report.to_dict(), indent=2))
        return
    _print_sweep(report)


def _print_sweep(report) -> None:
    from rich.markup import escape

    from navig.console_helper import Table

    glyph = {
        "fixed": "[green]✓ fixed[/green]",
        "failed": "[yellow]✗ not fixed[/yellow]",
        "skipped": "[dim]○ skipped[/dim]",
        "rolled_back": "[red]↺ rolled back[/red]",
    }
    if report.outcomes:
        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Kind", no_wrap=True)
        table.add_column("Where", no_wrap=True)
        table.add_column("Problem", no_wrap=True, max_width=40)
        table.add_column("Result", no_wrap=True)
        table.add_column("Model", no_wrap=True)
        table.add_column("Tries", no_wrap=True, justify="right")
        table.add_column("Time", no_wrap=True, justify="right")
        table.add_column("Why")
        for o in report.outcomes:
            d = o.diagnostic
            problem = d.code if d.kind == "lint" else d.code.rsplit("::", 1)[-1]
            if d.kind == "test":
                where = o.target_file or d.file
            else:
                where = d.where if d.line else d.file  # line 0 = a whole-repo step (ruff --fix)
            table.add_row(
                d.kind,
                escape(where),
                escape(problem),
                glyph.get(o.status, o.status),
                escape(o.model or "—"),
                str(o.attempts or "—"),
                f"{o.seconds:.0f}s" if o.seconds else "—",
                escape((o.reason or "").splitlines()[0][:140] if o.reason else ""),
            )
        ch.console.print(table)
    for note in report.notes:
        ch.warning(note)

    fixed, failed = report.count("fixed"), report.count("failed")
    rolled, skipped = report.count("rolled_back"), report.count("skipped")
    ch.dim(
        f"\n{fixed} fixed · {failed} not fixed · {skipped} skipped · {rolled} rolled back"
        f"  ·  {report.seconds / 60:.1f} min"
    )
    if report.baseline_failures:
        ch.dim(
            f"tests failing: {len(report.baseline_failures)} before → {len(report.final_failures)} after"
        )
    if report.report_path:
        ch.dim(f"report: {report.report_path}")
    if report.kept_branch:
        repo = report.repo
        ch.success(f"Branch ready for review: {report.branch}")
        ch.dim(f"  review  git -C {repo} log --stat {report.base[:10]}..{report.branch}")
        ch.dim(f"  merge   git -C {repo} merge --ff-only {report.branch}")
        ch.dim(f"  drop    git -C {repo} branch -D {report.branch}")
    else:
        ch.dim("Nothing was committed — no branch kept.")


# ─────────────────────────────────────────────────────────────────────────────
# QUANTUM VELOCITY K6 — Auto-Evolutive Profiler Commands
# ─────────────────────────────────────────────────────────────────────────────


@evolution_app.command("status")
def evolve_status(
    days: int = typer.Option(7, "--days", "-d", help="Number of days of history to analyze"),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show performance trends and regression alerts from the auto-profiler."""
    from navig.perf.profiler import (
        _perf_dir,
        detect_regressions,
        load_recent_samples,
        suggest_optimizations,
    )

    samples = load_recent_samples(days=days)

    if json_out:
        import json as _json

        regressions = detect_regressions(samples)
        suggestions = suggest_optimizations(samples)
        ch.raw_print(
            _json.dumps(
                {
                    "samples_loaded": len(samples),
                    "days": days,
                    "regressions": regressions,
                    "suggestions": suggestions,
                },
                indent=2,
            )
        )
        return

    ch.header("🧬 NAVIG Auto-Evolutive Profiler Status")
    ch.dim(f"  Perf data dir : {_perf_dir()}")
    ch.dim(f"  Samples loaded: {len(samples)} (last {days} days)")
    ch.dim("")

    if not samples:
        ch.warning("No profile data yet. NAVIG samples 1-in-100 CLI calls automatically.")
        ch.dim("Run a few commands and come back. The system is watching.")
        return

    regressions = detect_regressions(samples)
    if regressions:
        ch.warning(f"\n⚠️  {len(regressions)} regression(s) detected:")
        for r in regressions:
            ch.error(
                f"  • `navig {r['cmd']}` is [bold]{r['delta_pct']}%[/bold] slower "
                f"({r['old_ms']}ms → {r['new_ms']}ms)"
            )
            if r.get("fn"):
                ch.dim(f"    Top culprit: {r['fn']}")
    else:
        ch.success(f"\n✓ No regressions detected in the last {days} days")

    suggestions = suggest_optimizations(samples)
    if suggestions:
        ch.dim("\n🔥 Hottest call paths (next Shadow Execution candidates):")
        for s in suggestions[:5]:
            ch.dim(f"  {s}")

    ch.dim("\nRun `navig evolve optimize` to get detailed recommendations.")


@evolution_app.command("optimize")
def evolve_optimize(
    days: int = typer.Option(7, "--days", "-d", help="Number of days of history to analyze"),
):
    """Analyze profile data and propose the next optimization target."""
    from navig.ipc_pipe import get_pipe_status
    from navig.perf.profiler import (
        detect_regressions,
        load_recent_samples,
        suggest_optimizations,
    )

    samples = load_recent_samples(days=days)
    suggestions = suggest_optimizations(samples)
    regressions = detect_regressions(samples)
    pipe_status = get_pipe_status()

    ch.header("⚡ NAVIG Quantum Velocity — Optimization Report")
    ch.dim("")

    # ── IPC Pipe Status ──────────────────────────────────────────────────────
    ch.dim("📡 IPC Fast-Path:")
    if pipe_status["promoted"]:
        ch.success(f"  ✓ Named Pipe promoted (pipe address: {pipe_status['address']})")
    else:
        ch.warning(
            f"  ○ Pipe not yet promoted ({pipe_status['shadow_matches_this_session']} / "
            f"{pipe_status['promote_after']} shadow matches this session)"
        )

    ch.dim("")
    ch.dim("🔥 Top optimization candidates (by cumulative CPU time):")
    for s in suggestions:
        if s:
            ch.dim(f"  {s}")
        else:
            ch.dim("")

    if not suggestions or all(not s for s in suggestions):
        ch.success("  ✓ No significant hotspots found in recent profile data")

    if regressions:
        ch.dim("")
        ch.warning(f"⚠️  {len(regressions)} regression(s) require immediate attention.")
        ch.dim("Run `navig evolve status` for details.")

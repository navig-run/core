from pathlib import Path

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

evolution_app = typer.Typer(
    name="evolve",
    help="Auto-evolve skills, workflows, and more",
    no_args_is_help=True,
)


@evolution_app.command("skill")
def evolve_skill(
    goal: str = typer.Argument(..., help="Description of the skill to create"),
    skills_root: Path | None = typer.Option(
        None, "--root", "-r", help="Root directory for skills"
    ),
    retries: int = typer.Option(3, "--retries", "-n", help="Max evolution attempts"),
):
    """Generate and refine a new skill definition (SKILL.md)."""
    from navig.core.evolution.skill import SkillEvolver

    if not skills_root:
        # Default to navig/skills if exists, else ~/.navig/skills
        # For now assume local project skills
        skills_root = Path("skills")  # Relative to CWD

    evolver = SkillEvolver(skills_root)
    evolver.max_retries = retries

    ch.info(f"Evolving skill for: {goal}")
    result = evolver.evolve(goal)

    if result.success:
        ch.success("Skill evolution successful!")
        # Could print path
    else:
        ch.error(f"Skill evolution failed: {result.error}")


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

    if result.success:
        ch.success("Workflow evolution successful!")
    else:
        ch.error(f"Workflow evolution failed: {result.error}")


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

    if result.success:
        ch.success("Pack evolution successful!")
    else:
        ch.error(f"Pack evolution failed: {result.error}")


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

    if result.success:
        ch.success("Script evolution successful!")
    else:
        ch.error(f"Script evolution failed: {result.error}")


@evolution_app.command("fix")
def evolve_fix(
    file_path: Path = typer.Argument(..., help="Path to the file to fix"),
    instruction: str = typer.Argument(
        ..., help="Description of the bug or improvement"
    ),
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

    if result.success:
        ch.success("File update successful!")
    else:
        ch.error(f"Fix failed: {result.error}")


# ─────────────────────────────────────────────────────────────────────────────
# QUANTUM VELOCITY K6 — Auto-Evolutive Profiler Commands
# ─────────────────────────────────────────────────────────────────────────────


@evolution_app.command("status")
def evolve_status(
    days: int = typer.Option(
        7, "--days", "-d", help="Number of days of history to analyze"
    ),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show performance trends and regression alerts from the auto-profiler."""
    from navig.perf.profiler import (
        PERF_DIR,
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
    ch.dim(f"  Perf data dir : {PERF_DIR}")
    ch.dim(f"  Samples loaded: {len(samples)} (last {days} days)")
    ch.dim("")

    if not samples:
        ch.warning(
            "No profile data yet. NAVIG samples 1-in-100 CLI calls automatically."
        )
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
    days: int = typer.Option(
        7, "--days", "-d", help="Number of days of history to analyze"
    ),
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

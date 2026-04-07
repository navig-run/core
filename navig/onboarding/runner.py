from __future__ import annotations

from collections import Counter
import os
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from .engine import EngineConfig, EngineState, OnboardingEngine
from .genesis import load_or_create
from .steps import build_step_registry

if TYPE_CHECKING:
    from rich.console import Console as RichConsole

# Maximum number of step-revisit loops allowed in a single session.
_MAX_REVISIT_DEPTH = 20


def _get_console() -> RichConsole | None:
    """Return a Rich Console writing to stdout, or None if Rich is unavailable."""
    try:
        from rich.console import Console

        return Console()
    except ImportError:
        return None


def should_auto_run_onboarding(argv: Sequence[str] | None = None) -> bool:
    """Return True when first-run onboarding should execute for this invocation."""
    args = list(argv or sys.argv)

    if os.getenv("NAVIG_SKIP_ONBOARDING") == "1":
        return False

    if os.getenv("NAVIG_ONBOARDING_ACTIVE") == "1":
        return False

    if any(v in os.environ for v in ("_NAVIG_COMPLETE", "COMP_WORDS", "_TYPER_COMPLETE")):
        return False

    navig_dir = Path.home() / ".navig"
    if (navig_dir / "onboarding.json").exists():
        return False

    skip_flags = {"-v", "--version", "-h", "--help"}
    if args[1:2] and args[1] in skip_flags:
        return False

    skip_cmds = {"onboard", "quickstart", "service", "update", "version"}
    if any(cmd in args[1:2] for cmd in skip_cmds):
        return False

    return True


def run_engine_onboarding(
    *,
    force: bool = False,
    jump_to_step: str | None = None,
    show_banner: bool = True,
    respect_skip_env: bool = False,
    _revisit_depth: int = 0,
) -> EngineState | None:
    """Run canonical engine onboarding and return final state, or None if skipped."""
    if respect_skip_env and os.getenv("NAVIG_SKIP_ONBOARDING") == "1":
        return None

    navig_dir = Path.home() / ".navig"
    if not force and (navig_dir / "onboarding.json").exists() and not jump_to_step:
        return None

    cfg = EngineConfig(
        navig_dir=navig_dir,
        node_name=socket.gethostname(),
        reset=force,
        jump_to_step=jump_to_step,
    )
    genesis = load_or_create(navig_dir, name=socket.gethostname())
    steps = build_step_registry(cfg, genesis)
    step_tiers = {step.id: getattr(step, "tier", "essential") for step in steps}
    step_total = len(steps)
    started = {"n": 0}

    # Initialise a stdout console once so that ANSI/VT processing is enabled for
    # the entire wizard session — including on Windows where it must be activated
    # per handle.  Reusing the same instance avoids repeated handle probing.
    _con = _get_console()

    def _con_print(text: str) -> None:
        """Write *text* via Rich console when available, falling back to stdout."""
        if _con is not None:
            _con.print(text)
        else:
            sys.stdout.write(text.rstrip("\n") + "\n")
            sys.stdout.flush()

    def _progress(step: object) -> None:
        started["n"] += 1
        title = getattr(step, "title", str(step))
        if jump_to_step:
            # In targeted-jump mode the total-step fraction is misleading
            # (only the target step plus its tail run). Use ordinal only.
            msg = f"  [step {started['n']}] · {title}..."
            if _con is not None:
                _con.print(msg, markup=False)  # markup=False prevents [step N] being treated as a tag
            else:
                sys.stdout.write(msg + "\n")
                sys.stdout.flush()
        else:
            pct = int((started["n"] / max(step_total, 1)) * 100)
            tier = getattr(step, "tier", "essential")
            if _con is not None:
                _con.print(
                    f"  [{started['n']}/{step_total} {pct:>3}%] [dim]·[/dim] {title}"
                    f" [dim]({tier})[/dim]..."
                )
            else:
                sys.stdout.write(f"  [{started['n']}/{step_total} {pct:>3}%] · {title} ({tier})...\n")
                sys.stdout.flush()

    engine = OnboardingEngine(cfg, steps, on_step_start=_progress)

    if show_banner:
        if force:
            _con_print(
                "\n  [bold]Welcome back[/bold] — reconfiguring your existing NAVIG installation."
            )
            _con_print(
                "  Your previous settings will be preserved where not overwritten.\n"
            )
        else:
            _con_print("\n  Welcome to NAVIG — running first-time setup.")
            _con_print(
                "  Set [dim]NAVIG_SKIP_ONBOARDING=1[/dim] to skip automatic setup.\n"
            )

    previous_guard = os.getenv("NAVIG_ONBOARDING_ACTIVE")
    os.environ["NAVIG_ONBOARDING_ACTIVE"] = "1"
    try:
        state = engine.run()
    finally:
        if previous_guard is None:
            os.environ.pop("NAVIG_ONBOARDING_ACTIVE", None)
        else:
            os.environ["NAVIG_ONBOARDING_ACTIVE"] = previous_guard

    # If the review step asked to revisit a specific step, re-run from there.
    # Limit recursion to avoid infinite loops.
    if not state.interrupted_at and _revisit_depth < _MAX_REVISIT_DEPTH:
        review_record = next((s for s in state.steps if s.id == "review"), None)
        revisit_target = (review_record.output or {}).get("jumpTo", "") if review_record else ""
        if revisit_target:
            _con_print(f"\n  Revisiting step: {revisit_target} …\n")
            return run_engine_onboarding(
                force=True,
                jump_to_step=revisit_target,
                show_banner=False,
                respect_skip_env=False,
                _revisit_depth=_revisit_depth + 1,
            )

    if show_banner:
        if state.interrupted_at:
            _con_print("\n  [yellow]Setup paused.[/yellow] Run [bold]navig init[/bold] to resume.\n")
        else:
            _con_print(
                "\n  [green]Setup complete.[/green] Run [bold]navig --help[/bold] to get started.\n"
            )
        _print_verification_dashboard(state, step_tiers, _con)

    return state


def _print_verification_dashboard(
    state: EngineState,
    step_tiers: dict[str, str],
    con: RichConsole | None = None,
) -> None:
    status_counts = Counter(rec.status for rec in state.steps)
    tier_counts = Counter(step_tiers.get(rec.id, "essential") for rec in state.steps)
    total = max(len(state.steps), 1)
    completed = status_counts.get("completed", 0)
    finished_pct = int((completed / total) * 100)

    def _out(text: str) -> None:
        if con is not None:
            con.print(text)
        else:
            sys.stdout.write(text + "\n")

    _out("  [bold]Verification summary[/bold]" if con else "  Verification summary")
    _out("  [dim]───────────────────[/dim]" if con else "  ───────────────────")
    completed_count = status_counts.get("completed", 0)
    skipped_count = status_counts.get("skipped", 0)
    failed_count = status_counts.get("failed", 0)
    if con:
        fail_open = "[red]" if failed_count else "[dim]"
        fail_close = "[/red]" if failed_count else "[/dim]"
        _out(
            f"  Steps:  [green]✔ completed={completed_count}[/green]"
            f"  [dim]• skipped={skipped_count}[/dim]"
            f"  {fail_open}✖ failed={failed_count}{fail_close}"
        )
    else:
        _out(
            f"  Steps: ✔ completed={completed_count}  • skipped={skipped_count}  ✖ failed={failed_count}"
        )
    _out(f"  Completion: {finished_pct}%")
    if state.interrupted_at:
        _out(f"  State: interrupted at {state.interrupted_at}")
    else:
        _out("  State: [green]finished ✔[/green]" if con else "  State: finished ✔")

    _out(
        f"  Tiers: essential={tier_counts.get('essential', 0)}"
        f"  recommended={tier_counts.get('recommended', 0)}"
        f"  optional={tier_counts.get('optional', 0)}"
    )

    # Show recommended next command if any recommended steps are incomplete
    recommended_unfinished = [
        rec for rec in state.steps
        if step_tiers.get(rec.id) == "recommended" and rec.status in ("skipped", "failed", "pending")
    ]
    if recommended_unfinished:
        _out("  Recommended next command:")
        _out("    navig init --reconfigure")

    deferred = _deferred_integration_commands(state, step_tiers)
    if deferred:
        col_width = max(len(cmd) for cmd, _ in deferred)
        _out("  Deferred integrations:")
        for cmd, description in deferred:
            if con:
                _out(f"    [dim]-[/dim] [bold]{cmd:<{col_width}}[/bold]  {description}")
            else:
                _out(f"    - {cmd:<{col_width}}  {description}")
    _out("")


def _deferred_integration_commands(
    state: EngineState,
    step_tiers: dict[str, str],
) -> list[tuple[str, str]]:
    cmd_map = {
        "matrix": ("navig matrix setup", "receive alerts and run commands via Matrix chat"),
        "email": ("navig email setup", "SMTP notifications for workflows and alerts"),
        "social-networks": ("navig social setup", "social network integrations (Twitter/X, etc.)"),
        "telegram-bot": ("navig telegram setup", "receive alerts and run commands via Telegram bot"),
    }
    status_by_id = {rec.id: rec.status for rec in state.steps}

    deferred: list[tuple[str, str]] = []
    for step_id, (cmd, description) in cmd_map.items():
        if step_tiers.get(step_id) != "optional":
            continue
        if status_by_id.get(step_id) in ("skipped", "failed"):
            deferred.append((cmd, description))
    return deferred

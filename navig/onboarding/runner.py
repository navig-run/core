from __future__ import annotations

from collections import Counter
import os
import socket
import sys
from pathlib import Path
from typing import Sequence

from .engine import EngineConfig, EngineState, OnboardingEngine
from .genesis import load_or_create
from .steps import build_step_registry

# Maximum number of step-revisit loops allowed in a single session.
_MAX_REVISIT_DEPTH = 20


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
    skip_if_configured: bool = False,
    _revisit_depth: int = 0,
) -> EngineState | None:
    """Run canonical engine onboarding and return final state, or None if skipped."""
    if respect_skip_env and os.getenv("NAVIG_SKIP_ONBOARDING") == "1":
        return None

    navig_dir = Path.home() / ".navig"
    if (
        skip_if_configured
        and not force
        and (navig_dir / "onboarding.json").exists()
        and not jump_to_step
    ):
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

    def _progress(step: object) -> None:
        started["n"] += 1
        title = getattr(step, "title", str(step))
        if jump_to_step:
            # In targeted-jump mode the total-step fraction is misleading
            # (only the target step plus its tail run). Use ordinal only.
            sys.stdout.write(f"  [step {started['n']}] · {title}...\n")
        else:
            pct = int((started["n"] / max(step_total, 1)) * 100)
            tier = getattr(step, "tier", "essential")
            sys.stdout.write(f"  [{started['n']}/{step_total} {pct:>3}%] · {title} ({tier})...\n")
        sys.stdout.flush()

    engine = OnboardingEngine(cfg, steps, on_step_start=_progress)

    if show_banner:
        if force:
            sys.stdout.write("\n  Welcome back — reconfiguring your existing NAVIG installation.\n")
            sys.stdout.write("  Your previous settings will be preserved where not overwritten.\n\n")
        else:
            sys.stdout.write("\n  Welcome to NAVIG — running first-time setup.\n")
            sys.stdout.write("  Set NAVIG_SKIP_ONBOARDING=1 to skip automatic setup.\n\n")
        sys.stdout.flush()

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
            sys.stdout.write(f"\n  Revisiting step: {revisit_target} …\n\n")
            sys.stdout.flush()
            return run_engine_onboarding(
                force=True,
                jump_to_step=revisit_target,
                show_banner=False,
                respect_skip_env=False,
                _revisit_depth=_revisit_depth + 1,
            )

    if show_banner:
        if state.interrupted_at:
            sys.stdout.write("\n  Setup paused. Run 'navig init' to resume.\n\n")
        else:
            sys.stdout.write("\n  Setup complete. Run 'navig --help' to get started.\n\n")
        _print_verification_dashboard(state, step_tiers)
        sys.stdout.flush()

    return state


def _print_verification_dashboard(state: EngineState, step_tiers: dict[str, str]) -> None:
    status_counts = Counter(rec.status for rec in state.steps)
    tier_counts = Counter(step_tiers.get(rec.id, "essential") for rec in state.steps)
    total = max(len(state.steps), 1)
    completed = status_counts.get("completed", 0)
    finished_pct = int((completed / total) * 100)

    sys.stdout.write("  Verification summary\n")
    sys.stdout.write("  ───────────────────\n")
    sys.stdout.write(
        "  Steps: "
        f"✔ completed={status_counts.get('completed', 0)}  "
        f"• skipped={status_counts.get('skipped', 0)}  "
        f"✖ failed={status_counts.get('failed', 0)}\n"
    )
    sys.stdout.write(f"  Completion: {finished_pct}%\n")
    if state.interrupted_at:
        sys.stdout.write(f"  State: interrupted at {state.interrupted_at}\n")
    else:
        sys.stdout.write("  State: finished ✔\n")

    sys.stdout.write(
        "  Tiers: "
        f"essential={tier_counts.get('essential', 0)}  "
        f"recommended={tier_counts.get('recommended', 0)}  "
        f"optional={tier_counts.get('optional', 0)}\n"
    )

    deferred = _deferred_integration_commands(state, step_tiers)
    if not state.interrupted_at and (status_counts.get("skipped", 0) > 0 or status_counts.get("failed", 0) > 0):
        sys.stdout.write(
            "  Recommended next: navig init --reconfigure  "
            "(finish skipped/failed setup steps)\n"
        )
    if deferred:
        col_width = max(len(cmd) for cmd, _ in deferred)
        sys.stdout.write("  Deferred integrations:\n")
        for cmd, description in deferred:
            sys.stdout.write(f"    - {cmd:<{col_width}}  {description}\n")
    sys.stdout.write("\n")


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

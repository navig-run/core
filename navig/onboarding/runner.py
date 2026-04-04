from __future__ import annotations

from collections import Counter
import os
import socket
import sys
from pathlib import Path
from typing import Sequence

from navig import console_helper as ch
from navig.platform import paths

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

    navig_dir = paths.config_dir()
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

    navig_dir = paths.config_dir()
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
            ch.dim(f"  [step {started['n']}] · {title}...")
        else:
            pct = int((started["n"] / max(step_total, 1)) * 100)
            tier = getattr(step, "tier", "essential")
            ch.dim(f"  [{started['n']}/{step_total} {pct:>3}%] · {title} ({tier})...")

    engine = OnboardingEngine(cfg, steps, on_step_start=_progress)

    if show_banner:
        if force:
            ch.info("Welcome back — reconfiguring your existing NAVIG installation.")
            ch.dim("  Your previous settings will be preserved where not overwritten.")
        else:
            ch.info("Welcome to NAVIG — running first-time setup.")
            ch.dim("  Set NAVIG_SKIP_ONBOARDING=1 to skip automatic setup.")

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
            ch.step(f"Revisiting step: {revisit_target} …")
            return run_engine_onboarding(
                force=True,
                jump_to_step=revisit_target,
                show_banner=False,
                respect_skip_env=False,
                _revisit_depth=_revisit_depth + 1,
            )

    if show_banner:
        if state.interrupted_at:
            ch.warning("Setup paused. Run 'navig init' to resume.")
        else:
            ch.success("Setup complete. Run 'navig --help' to get started.")
        _print_verification_dashboard(state, step_tiers)

    return state


def _print_verification_dashboard(state: EngineState, step_tiers: dict[str, str]) -> None:

    status_counts = Counter(rec.status for rec in state.steps)
    tier_counts = Counter(step_tiers.get(rec.id, "essential") for rec in state.steps)
    total = max(len(state.steps), 1)
    completed = status_counts.get("completed", 0)
    skipped = status_counts.get("skipped", 0)
    failed = status_counts.get("failed", 0)
    finished_pct = int((completed / total) * 100)

    ch.subheader("Verification Summary")

    # Step counts with contextual icons
    parts: list[str] = []
    if completed:
        parts.append(f"[green]✓ {completed} completed[/green]")
    if skipped:
        parts.append(f"[dim]· {skipped} skipped[/dim]")
    if failed:
        parts.append(f"[red]✗ {failed} failed[/red]")
    ch.info(f"Steps: {' '.join(parts)}")

    # Completion percentage with contextual color
    if finished_pct == 100:
        ch.success(f"Completion: {finished_pct}%")
    elif finished_pct >= 50:
        ch.warning(f"Completion: {finished_pct}%")
    else:
        ch.error(f"Completion: {finished_pct}%")

    # State
    if state.interrupted_at:
        ch.warning(f"State: interrupted at {state.interrupted_at}")
    else:
        ch.success("State: finished")

    # Tier breakdown
    ch.dim(
        f"  Tiers: essential={tier_counts.get('essential', 0)}  "
        f"recommended={tier_counts.get('recommended', 0)}  "
        f"optional={tier_counts.get('optional', 0)}"
    )

    # Recommended next action
    deferred = _deferred_integration_commands(state, step_tiers)
    if not state.interrupted_at and (skipped > 0 or failed > 0):
        ch.step("Recommended: navig init --reconfigure  (finish skipped/failed steps)")
    if deferred:
        ch.dim("  Deferred integrations:")
        col_width = max(len(cmd) for cmd, _ in deferred)
        for cmd, description in deferred:
            ch.dim(f"    → {cmd:<{col_width}}  {description}")


def _deferred_integration_commands(
    state: EngineState,
    step_tiers: dict[str, str],
) -> list[tuple[str, str]]:
    cmd_map = {
        "matrix": ("navig matrix setup", "receive alerts and run commands via Matrix chat"),
        "email": ("navig email setup", "SMTP notifications for workflows and alerts"),
        "social-networks": ("navig social setup", "social network integrations (Twitter/X, etc.)"),
        "telegram-bot": ("navig telegram setup", "receive alerts and run commands via Telegram bot"),
        "runtime-secrets": ("navig init --reconfigure", "import API keys into vault"),
        "skills-activation": ("navig init --reconfigure", "activate skill packs"),
    }
    status_by_id = {rec.id: rec.status for rec in state.steps}

    deferred: list[tuple[str, str]] = []
    for step_id, (cmd, description) in cmd_map.items():
        if step_tiers.get(step_id) != "optional":
            continue
        if status_by_id.get(step_id) in ("skipped", "failed"):
            deferred.append((cmd, description))
    return deferred

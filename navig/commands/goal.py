"""
navig goal — standing goals: tell NAVIG an objective once, it keeps working it.

  navig goal add "Keep backups under 80% of the disk" --every 6h
  navig goal list
  navig goal show <id>
  navig goal run <id>        # one cycle now
  navig goal pause <id> / resume <id> / remove <id>

Each cycle is one mission, run unattended under a READ-ONLY grant, that ends with a
device-signed receipt and a progress note on Telegram. The goal stops by itself when
the agent reports it done, when it reaches ``--max-cycles``, or after three failed
cycles in a row. Engine: ``navig.missions.standing_goals``.

Not to be confused with ``navig agent goal`` (the standalone agent's task planner),
which decomposes a request into subtasks once; a standing goal is re-worked on a
schedule until it is met.
"""

from __future__ import annotations

import re
from typing import Any

import typer

from navig import console_helper as ch

goal_app = typer.Typer(
    name="goal",
    help="Standing goals — an objective NAVIG keeps working on a schedule until it is done",
    no_args_is_help=True,
)

_SHORT_EVERY = re.compile(r"^\s*(\d+)\s*(m|min|mins|h|hr|hrs|d)\s*$", re.IGNORECASE)
_UNIT = {"m": "minutes", "min": "minutes", "mins": "minutes", "h": "hours", "hr": "hours",
         "hrs": "hours", "d": "days"}
_STATE_STYLE = {
    "active": "[green]● active[/green]",
    "paused": "[yellow]‖ paused[/yellow]",
    "done": "[cyan]✓ done[/cyan]",
    "stopped": "[red]■ stopped[/red]",
}


def _normalize_every(raw: str) -> str:
    """``6h`` → ``every 6 hours``; anything else (``every 30 minutes``, ``daily at 9am``,
    a cron expression) is passed through for the scheduler's own parser to judge."""
    m = _SHORT_EVERY.match(raw or "")
    if m:
        return f"every {int(m.group(1))} {_UNIT[m.group(2).lower()]}"
    return (raw or "").strip()


def _store():
    from navig.store.board import get_board_store

    return get_board_store()


def _resolve(goal_ref: str) -> dict[str, Any]:
    """A standing goal by full id or unique id prefix; exits 1 otherwise."""
    ref = goal_ref.strip()
    goals = _store().list_pursuits()
    exact = [g for g in goals if g["id"] == ref]
    matches = exact or [g for g in goals if g["id"].startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        ch.error(f"no standing goal matches '{ref}'", "list them with: navig goal list")
    else:
        ch.error(f"'{ref}' matches {len(matches)} goals — use more of the id")
    raise typer.Exit(1)


def _esc(text: Any) -> str:
    from rich.markup import escape

    return escape(str(text if text is not None else ""))


def _public(goal: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "title", "objective", "every", "grant_scope", "run_state", "cycles_run",
            "max_cycles", "fail_streak", "last_run_at", "last_report", "last_receipt_id",
            "created_at")
    return {k: goal.get(k) for k in keys}


@goal_app.command("add")
def goal_add(
    objective: str = typer.Argument(..., help="What you want achieved, in plain words."),
    every: str = typer.Option("every 1 hour", "--every", "-e",
                              help="How often to work on it: 30m, 6h, 1d, 'daily at 9am', or cron."),
    title: str = typer.Option("", "--title", "-t", help="Short name (default: the objective, shortened)."),
    grant: str = typer.Option("readonly", "--grant",
                              help="What each cycle may do unattended. Only 'readonly' exists today."),
    max_cycles: int = typer.Option(24, "--max-cycles", min=1, max=1000,
                                   help="Stop after this many cycles even if not done."),
    as_json: bool = typer.Option(False, "--json", help="Print the created goal as JSON."),
) -> None:
    """Start a standing goal. Its first cycle runs at the first scheduled time."""
    from navig.missions.standing_goals import GRANTS, schedule
    from navig.scheduler.cron_service import CronParser

    objective = objective.strip()
    if not objective:
        ch.error("the objective is empty")
        raise typer.Exit(1)
    if grant not in GRANTS:
        ch.error(f"unknown grant '{grant}'", f"available: {', '.join(GRANTS)}")
        raise typer.Exit(1)
    schedule_text = _normalize_every(every)
    ok, why = CronParser.validate(schedule_text)
    if not ok:
        ch.error(f"can't schedule '{every}': {why}", "try 30m, 6h, 1d or 'daily at 9am'")
        raise typer.Exit(1)

    name = title.strip() or (objective if len(objective) <= 60 else objective[:57].rstrip() + "…")
    store = _store()
    goal = store.create_pursuit(
        name, objective=objective, every=schedule_text, grant_scope=grant, max_cycles=max_cycles
    )
    try:
        job = schedule(goal)
    except Exception as exc:  # noqa: BLE001 — never leave a goal that nothing will ever run
        store.delete_goal(goal["id"])
        ch.error(f"couldn't schedule the goal: {exc}")
        raise typer.Exit(1) from exc

    if as_json:
        ch.emit_json({**_public(goal), "next_run": job.get("next_run")})
        return
    ch.success(f"Standing goal started · {goal['id']}")
    ch.kv("Objective", objective)
    ch.kv("Schedule", schedule_text)
    ch.kv("Next cycle", job.get("next_run") or "—")
    ch.kv("Limit", f"{max_cycles} cycles · read-only")
    ch.dim(f"  run one now: navig goal run {goal['id']}")


@goal_app.command("list")
def goal_list(
    all_goals: bool = typer.Option(False, "--all", "-a", help="Include finished goals."),
    as_json: bool = typer.Option(False, "--json", help="Print as JSON."),
) -> None:
    """Your standing goals and where each one is."""
    from navig.console_helper import Table

    goals = _store().list_pursuits()
    if not all_goals:
        goals = [g for g in goals if g.get("run_state") in ("active", "paused")]
    if as_json:
        ch.emit_json([_public(g) for g in goals])
        return
    if not goals:
        ch.info("No standing goals" + ("" if all_goals else " in progress")
                + " · start one with navig goal add \"<objective>\" --every 6h")
        return

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("ID", no_wrap=True, style="dim")
    table.add_column("Goal")
    table.add_column("State", no_wrap=True)
    table.add_column("Cycles", no_wrap=True, justify="right")
    table.add_column("Every", no_wrap=True)
    table.add_column("Last run", no_wrap=True)
    for g in goals:
        table.add_row(
            g["id"],
            _esc(g["title"]),
            _STATE_STYLE.get(g.get("run_state") or "", _esc(g.get("run_state"))),
            f"{g.get('cycles_run', 0)}/{g.get('max_cycles')}",
            _esc(g.get("every")),
            (g.get("last_run_at") or "—")[:16].replace("T", " "),
        )
    ch.console.print(table)
    active = sum(1 for g in goals if g.get("run_state") == "active")
    ch.dim(f"{active}/{len(goals)} active · details: navig goal show <id>")


@goal_app.command("show")
def goal_show(
    goal_ref: str = typer.Argument(..., help="Goal id (or a unique prefix)."),
    as_json: bool = typer.Option(False, "--json", help="Print as JSON."),
) -> None:
    """Everything about one goal, including its latest report and receipt."""
    from navig.missions.standing_goals import find_job

    goal = _resolve(goal_ref)
    job = None
    try:
        job = find_job(goal["id"]) if goal.get("run_state") == "active" else None
    except Exception:  # noqa: BLE001 — the schedule lookup is informational
        job = None
    if as_json:
        ch.emit_json({**_public(goal), "next_run": (job or {}).get("next_run")})
        return
    ch.header(goal["title"])
    ch.kv("Objective", goal.get("objective"))
    ch.kv("State", goal.get("run_state"))
    ch.kv("Cycles", f"{goal.get('cycles_run', 0)} of {goal.get('max_cycles')}")
    ch.kv("Schedule", goal.get("every"))
    if goal.get("run_state") == "active":
        ch.kv("Next cycle", (job or {}).get("next_run") or "not scheduled — run: navig goal resume")
    ch.kv("Last run", goal.get("last_run_at") or "—")
    if goal.get("last_receipt_id"):
        ch.kv("Receipt", goal["last_receipt_id"])
        ch.dim(f"  verify: navig block verify-receipt {goal['last_receipt_id']}")
    report = (goal.get("last_report") or "").strip()
    if report:
        ch.panel(_esc(report), title="Latest report")


@goal_app.command("run")
def goal_run(
    goal_ref: str = typer.Argument(..., help="Goal id (or a unique prefix)."),
) -> None:
    """Run one cycle now and print what it found (counts toward the cycle limit)."""
    goal = _resolve(goal_ref)
    if goal.get("run_state") != "active":
        ch.error(f"goal is {goal.get('run_state')}", "resume it first: navig goal resume " + goal["id"])
        raise typer.Exit(1)

    ch.step("Working on it — one cycle can take a few minutes…")
    outcome = _run_via_daemon(goal)
    if outcome is None:
        outcome = _run_inline(goal)

    after = _store().get_goal(goal["id"]) or goal
    if after.get("cycles_run", 0) <= goal.get("cycles_run", 0):
        ch.error(f"no cycle ran: {outcome}")
        raise typer.Exit(1)
    report = (after.get("last_report") or "").strip()
    if report:
        ch.panel(_esc(report), title=f"Cycle {after.get('cycles_run')} report")
    state = after.get("run_state")
    if after.get("fail_streak", 0) and state == "active":
        ch.warning(f"this cycle failed ({after['fail_streak']} in a row) · {outcome}")
        raise typer.Exit(1)
    if state == "stopped":
        ch.warning(f"goal stopped · {outcome}")
    else:
        ch.success(outcome)


def _run_via_daemon(goal: dict[str, Any]) -> str | None:
    """Ask the running gateway to run the cycle (its scheduler owns the mission store).

    Returns None when there is no reachable daemon — then the cycle runs in this
    process, which is safe exactly because nothing else is writing that store.
    """
    import json
    import urllib.request

    from navig.missions.standing_goals import CYCLE_TIMEOUT_SECONDS, find_job
    from navig.scheduler import habit_store

    base = habit_store._gateway_url()
    if not base or not habit_store.daemon_reachable():
        return None
    job = find_job(goal["id"])
    if job is None:
        # Active but unscheduled (e.g. the job was deleted by hand). Re-create it rather
        # than falling back to an in-process run, which would write the mission store
        # underneath the running daemon.
        from navig.missions.standing_goals import schedule

        job = schedule(goal)
    req = urllib.request.Request(
        f"{base}/api/deck/schedule/crons/{job['id']}/run",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=CYCLE_TIMEOUT_SECONDS + 60) as resp:  # noqa: S310 — loopback URL from our own handshake file
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"the daemon could not run it: {exc}"
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return "the daemon returned no result"
    return str(data.get("output") or data.get("error") or "done")


def _run_inline(goal: dict[str, Any]) -> str:
    import asyncio
    from types import SimpleNamespace

    from navig.missions.standing_goals import run_goal_cycle

    gateway = None
    try:
        from navig.config import get_config_manager

        gateway = SimpleNamespace(config_manager=get_config_manager())
    except Exception:  # noqa: BLE001 — the executor runs without config too
        gateway = None
    return asyncio.run(run_goal_cycle(gateway, goal["id"]))


@goal_app.command("pause")
def goal_pause(goal_ref: str = typer.Argument(..., help="Goal id (or a unique prefix).")) -> None:
    """Stop scheduling cycles, keeping the goal's history."""
    from navig.missions.standing_goals import unschedule

    goal = _resolve(goal_ref)
    if goal.get("run_state") != "active":
        ch.error(f"goal is {goal.get('run_state')}, not active")
        raise typer.Exit(1)
    _store().set_pursuit_state(goal["id"], "paused")
    unschedule(goal["id"])
    ch.success(f"Paused · {goal['title']}")
    ch.dim(f"  resume with: navig goal resume {goal['id']}")


@goal_app.command("resume")
def goal_resume(goal_ref: str = typer.Argument(..., help="Goal id (or a unique prefix).")) -> None:
    """Start scheduling a paused goal again, from where it left off."""
    from navig.missions.standing_goals import schedule

    goal = _resolve(goal_ref)
    if goal.get("run_state") != "paused":
        hint = "start a new one with: navig goal add" if goal.get("run_state") in ("done", "stopped") else None
        ch.error(f"goal is {goal.get('run_state')}, not paused", hint)
        raise typer.Exit(1)
    resumed = _store().set_pursuit_state(goal["id"], "active") or goal
    job = schedule(resumed)
    ch.success(f"Resumed · {goal['title']}")
    ch.kv("Next cycle", job.get("next_run") or "—")


@goal_app.command("remove")
def goal_remove(
    goal_ref: str = typer.Argument(..., help="Goal id (or a unique prefix)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
) -> None:
    """Delete a goal and its schedule. Its mission receipts are kept."""
    from navig.missions.standing_goals import unschedule

    goal = _resolve(goal_ref)
    if not yes and not typer.confirm(f"Remove standing goal '{goal['title']}'?", default=False):
        ch.info("Kept.")
        return
    unschedule(goal["id"])
    _store().delete_goal(goal["id"])
    ch.success(f"Removed · {goal['title']}")


# ---------------------------------------------------------------------------
# Session goals — the <goal_round> loop inside one NAVIG OS session.
# Distinct from the standing goals above: no cron, no missions — the OS session
# itself injects a round each time it goes idle until the goal completes,
# blocks, or spends its round budget.
# ---------------------------------------------------------------------------

session_app = typer.Typer(
    name="session",
    help="Session goals — a goal one NAVIG OS session works until done",
    no_args_is_help=True,
)
goal_app.add_typer(session_app, name="session")

GOAL_PHASE_STYLE = {
    "active": "[green]● active[/green]",
    "paused": "[yellow]‖ paused[/yellow]",
    "blocked": "[red]■ blocked[/red]",
    "complete": "[cyan]✓ complete[/cyan]",
}


def _os_call(channel: str, *args: Any) -> Any:
    """One RPC to the running NAVIG OS server; honest error + exit 1 otherwise."""
    from navig.cloud.os_rpc import OsRpcError, call

    try:
        return call(channel, *args)
    except OsRpcError as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc


def _goal_enabled() -> bool:
    from navig import config as cfg  # noqa: PLC0415 — lazy: help must stay fast
    from navig.core.coerce import coerce_bool

    try:
        raw = cfg.get("goal.enabled")
    except Exception:  # noqa: BLE001 — unreadable config: the shipped default applies
        raw = None
    return coerce_bool(raw, default=True)


def _default_max_rounds() -> int:
    from navig import config as cfg  # noqa: PLC0415
    from navig.core.coerce import coerce_int

    try:
        raw = cfg.get("goal.max_rounds")
    except Exception:  # noqa: BLE001
        raw = None
    return coerce_int(raw, default=12, minimum=1)


def _resolve_session(ref: str) -> dict[str, Any]:
    """A session by exact id, unique id prefix, or exact name; exits 1 otherwise."""
    sessions = _os_call("sessions:get") or []
    ref = ref.strip()
    exact = [s for s in sessions if s.get("id") == ref]
    matches = exact or [s for s in sessions if str(s.get("id") or "").startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        by_name = [s for s in sessions if s.get("name") == ref]
        if len(by_name) == 1:
            return by_name[0]
        ch.error(f"no session matches '{ref}'", "list them with: navig goal session list")
    else:
        ch.error(f"'{ref}' matches {len(matches)} sessions — use more of the id")
    raise typer.Exit(1)


def _session_goal_or_exit(session: dict[str, Any]) -> dict[str, Any]:
    goal = session.get("goal")
    if not isinstance(goal, dict) or not goal:
        ch.error(
            f"session '{session.get('id')}' has no goal",
            "set one with: navig goal session set <session> \"<objective>\"",
        )
        raise typer.Exit(1)
    return goal


def _goal_public(goal: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "objective", "phase", "roundsStarted", "maxRounds", "revision",
            "blockedReason", "blockedStreak", "completionNote", "createdAt", "updatedAt")
    return {k: goal.get(k) for k in keys}


def _print_goal_row(session: dict[str, Any], goal: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        import json

        payload = _goal_public(goal)
        payload["sessionId"] = session.get("id")
        payload["sessionName"] = session.get("name")
        payload["armed"] = bool(session.get("goalArmed"))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    phase = str(goal.get("phase") or "?")
    style = GOAL_PHASE_STYLE.get(phase, phase)
    ch.kv("Session", f"{session.get('name') or '—'}  [dim]({session.get('id')})[/dim]")
    ch.kv("Objective", _esc(goal.get("objective")))
    ch.kv("Phase", style)
    ch.kv("Rounds", f"{goal.get('roundsStarted', 0)}/{goal.get('maxRounds', '?')}")
    armed = bool(session.get("goalArmed"))
    ch.kv("Loop", "[green]armed[/green]" if armed else "[yellow]disarmed[/yellow] (resume in the UI or with `resume`)")
    if goal.get("blockedReason"):
        ch.kv("Blocked", _esc(goal["blockedReason"]))
    if goal.get("completionNote"):
        ch.kv("Note", _esc(goal["completionNote"]))


@session_app.command("set")
def goal_session_set(
    session: str = typer.Argument(..., help="Session id (or unique prefix / exact name)."),
    objective: str = typer.Argument(..., help="What the session should achieve, in plain words."),
    max_rounds: int = typer.Option(
        None, "--max-rounds", min=1,
        help="Round budget (default: goal.max_rounds — 12).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the resulting goal as JSON."),
) -> None:
    """Give a NAVIG OS session a goal; it works the rounds until done or blocked."""
    if not _goal_enabled():
        ch.error("the session goal loop is disabled", "enable it: navig config set goal.enabled true")
        raise typer.Exit(1)
    target = _resolve_session(session)
    command: dict[str, Any] = {"type": "setGoal", "objective": objective.strip()}
    budget = max_rounds if max_rounds is not None else _default_max_rounds()
    if budget:
        command["maxRounds"] = budget
    result = _os_call("sessions:command", target.get("id"), command)
    goal = (result or {}).get("goal") if isinstance(result, dict) else None
    if not isinstance(goal, dict):
        goal = {**command, "phase": "active", "roundsStarted": 0}
    if as_json:
        _print_goal_row(target, goal, as_json=True)
        return
    ch.success(f"Goal set · {_esc(str(goal.get('objective') or objective)[:80])}")
    ch.kv("Rounds", f"0/{goal.get('maxRounds', budget)}")
    ch.dim(f"  watch with: navig goal session show {target.get('id')}")


@session_app.command("show")
def goal_session_show(
    session: str = typer.Argument(..., help="Session id (or unique prefix / exact name)."),
    as_json: bool = typer.Option(False, "--json", help="Print the goal as JSON."),
) -> None:
    """Show a session's goal: objective, phase, rounds, loop arming."""
    target = _resolve_session(session)
    goal = _session_goal_or_exit(target)
    _print_goal_row(target, goal, as_json=as_json)


@session_app.command("list")
def goal_session_list(
    as_json: bool = typer.Option(False, "--json", help="Print the sessions with goals as JSON."),
) -> None:
    """List every NAVIG OS session that has a goal."""
    sessions = _os_call("sessions:get") or []
    rows = [
        (s, g) for s in sessions
        if isinstance((g := s.get("goal")), dict) and g
    ]
    if as_json:
        import json

        print(json.dumps(
            [{
                "sessionId": s.get("id"), "sessionName": s.get("name"),
                "armed": bool(s.get("goalArmed")), **_goal_public(g),
            } for s, g in rows],
            ensure_ascii=False, indent=2,
        ))
        return
    if not rows:
        ch.info("no session has a goal", 'set one: navig goal session set <session> "<objective>"')
        return
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Session", no_wrap=True)
    table.add_column("Phase", no_wrap=True)
    table.add_column("Rounds", no_wrap=True, justify="right")
    table.add_column("Loop", no_wrap=True)
    table.add_column("Objective")
    for s, g in rows:
        phase = str(g.get("phase") or "?")
        table.add_row(
            str(s.get("name") or s.get("id") or "?"),
            GOAL_PHASE_STYLE.get(phase, phase),
            f"{g.get('roundsStarted', 0)}/{g.get('maxRounds', '?')}",
            "[green]●[/green]" if s.get("goalArmed") else "[dim]○[/dim]",
            _esc(str(g.get("objective") or ""))[:70],
        )
    ch.console.print(table)


@session_app.command("pause")
def goal_session_pause(
    session: str = typer.Argument(..., help="Session id (or unique prefix / exact name)."),
) -> None:
    """Stop injecting rounds; the goal stays on the session, resumable."""
    target = _resolve_session(session)
    _session_goal_or_exit(target)
    result = _os_call("sessions:command", target.get("id"), {"type": "pauseGoal"})
    goal = (result or {}).get("goal") if isinstance(result, dict) else None
    ch.success(f"Paused · {_esc(str((goal or {}).get('objective') or ''))[:80]}")
    ch.dim(f"  resume with: navig goal session resume {target.get('id')}")


@session_app.command("resume")
def goal_session_resume(
    session: str = typer.Argument(..., help="Session id (or unique prefix / exact name)."),
) -> None:
    """Re-arm a paused goal so the session works it again."""
    target = _resolve_session(session)
    _session_goal_or_exit(target)
    # The OS refuses a resume whose round budget is spent; _os_call surfaces that.
    result = _os_call("sessions:command", target.get("id"), {"type": "resumeGoal"})
    goal = (result or {}).get("goal") if isinstance(result, dict) else None
    ch.success(f"Resumed · {_esc(str((goal or {}).get('objective') or ''))[:80]}")
    ch.kv("Rounds", f"{(goal or {}).get('roundsStarted', 0)}/{(goal or {}).get('maxRounds', '?')}")


@session_app.command("complete")
def goal_session_complete(
    session: str = typer.Argument(..., help="Session id (or unique prefix / exact name)."),
    evidence: str = typer.Option(
        ...,
        "--evidence",
        "-e",
        help="What you verified: commands run and their output, files changed, tests observed.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the resulting goal as JSON."),
) -> None:
    """Close an active goal as the operator, WITH evidence (human override).

    Same evidence assessment the model faces: substantive text, not a restatement
    of the objective, and any cited paths must exist on disk. Recorded as
    'closed by operator: …' so the UI, show --json and get_goal can tell it apart
    from a model completion.
    """
    target = _resolve_session(session)
    _session_goal_or_exit(target)
    result = _os_call(
        "sessions:command",
        target.get("id"),
        {"type": "completeGoal", "evidence": evidence.strip()},
    )
    goal = (result or {}).get("goal") if isinstance(result, dict) else None
    if as_json:
        _print_goal_row(target, goal or {}, as_json=True)
        return
    ch.success(f"Goal completed · {_esc(str((goal or {}).get('objective') or ''))[:80]}")
    ch.kv("Evidence", _esc(str((goal or {}).get("completionNote") or evidence))[:200])


@session_app.command("clear")
def goal_session_clear(
    session: str = typer.Argument(..., help="Session id (or unique prefix / exact name)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
) -> None:
    """Remove the goal from a session. Its injected rounds stay in history."""
    target = _resolve_session(session)
    goal = _session_goal_or_exit(target)
    if not yes and not typer.confirm(
        f"Clear the goal of session '{target.get('name') or target.get('id')}'?", default=False
    ):
        ch.info("Kept.")
        return
    _os_call("sessions:command", target.get("id"), {"type": "clearGoal"})
    ch.success(f"Cleared · {_esc(str(goal.get('objective') or ''))[:80]}")

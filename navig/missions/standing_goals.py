"""Standing goals — an objective NAVIG keeps working on a schedule until it is done.

You say what you want once (``navig goal add "keep the backups under 80% of the
disk" --every 6h``) and NAVIG works it one CYCLE at a time:

  cron fires ``NAVIG_GOAL:<id>``  →  claim the cycle (atomic, in the goal's row)
  →  one Mission through the MissionExecutor, AUTO, under the read-only MissionGrant
  →  a signed ExecutionReceipt  →  a ``goal_digest`` notification (deck + Telegram)
  →  the agent's closing ``STATUS: DONE | CONTINUE`` line decides whether it goes on.

A goal ends on its own: DONE when the agent says the objective is met, STOPPED when it
reaches ``max_cycles`` or fails ``FAIL_STREAK_LIMIT`` cycles in a row. Its cron job is
removed at that point, so a finished goal costs nothing.

Why these pieces and not new ones: the goal is a ``board_goal`` row (so the deck's Goals
view shows it), the schedule is an ordinary cron job (persistence, skip-on-crash and
reload for free), and every cycle is an ordinary Mission (so it inherits the approval
floor, the verifier, the receipt and the Deck's live view). There is deliberately NO
approval prompt per cycle: an unattended loop that pages the operator is the
84-missions/80-timed-out failure — the read-only grant is what makes AUTO safe here.

The goal's own LLM work happens inside the MissionExecutor's agent loop
(``navig/agent/``); this module only builds the prompt and reads the closing line.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: Cron command prefix the scheduler dispatches here (next to NAVIG_HABIT_REMINDER).
GOAL_COMMAND_PREFIX = "NAVIG_GOAL:"
#: Cron job name prefix — one job per goal, found and removed by this name.
CRON_NAME_PREFIX = "goal:"
#: Mission capability for a goal cycle.
CAPABILITY = "goal"
#: Consecutive failed cycles that stop a goal.
FAIL_STREAK_LIMIT = 3
#: A claimed cycle older than this belonged to a run that died; it may be taken over.
CLAIM_STALE_AFTER = timedelta(minutes=45)
#: Cron timeout for one cycle — above the executor's default mission timeout (600s),
#: so the mission's own timeout (which leaves a receipt) fires first.
CYCLE_TIMEOUT_SECONDS = 900
#: The only grant a goal can hold today. Wider grants are a deliberate future decision.
GRANTS = ("readonly",)

_STATUS_RE = re.compile(
    r"^[\s>*_`#-]*STATUS[\s*_`]*:[\s*_`]*(DONE|CONTINUE)\b[\s*_`.]*$",
    re.IGNORECASE | re.MULTILINE,
)


def cron_name(goal_id: str) -> str:
    return f"{CRON_NAME_PREFIX}{goal_id}"


def cron_command(goal_id: str) -> str:
    return f"{GOAL_COMMAND_PREFIX}{goal_id}"


def _iso(dt: datetime) -> str:
    # Same shape as store.base._utcnow, so the claim's string comparison is ordered.
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ── Prompt + verdict ────────────────────────────────────────────────────────


def build_prompt(goal: dict[str, Any], cycle: int) -> str:
    previous = (goal.get("last_report") or "").strip()
    history = (
        f"Your report from the previous cycle:\n{previous}"
        if previous
        else "This is the first cycle; there is no previous report."
    )
    return (
        "You are working on a STANDING GOAL for the operator, unattended, one cycle at "
        "a time.\n\n"
        f"Objective: {goal.get('objective') or goal.get('title')}\n"
        f"This is cycle {cycle} of at most {goal.get('max_cycles')}.\n\n"
        f"{history}\n\n"
        "You hold a READ-ONLY grant: any command that changes state will be refused, so "
        "do not attempt one and do not retry a refused command. If meeting the objective "
        "needs a change you cannot make, say exactly what the operator should do.\n\n"
        "Finish with a short report — what you checked, what you found, and what changed "
        "since the previous cycle — then a last line that is exactly one of:\n"
        "STATUS: DONE      (the objective is met, or nothing further is possible)\n"
        "STATUS: CONTINUE  (more cycles are needed)"
    )


def parse_status(text: str) -> tuple[bool, str]:
    """(done, report). The LAST status line wins; the report is the text without it.

    No status line reads as CONTINUE: a goal that forgot to say it was done costs one
    more cycle, while one wrongly read as done stops working silently.
    """
    text = text or ""
    matches = list(_STATUS_RE.finditer(text))
    if not matches:
        return False, text.strip()
    last = matches[-1]
    done = last.group(1).upper() == "DONE"
    report = (text[: last.start()] + text[last.end():]).strip()
    return done, report


def next_state(goal: dict[str, Any], *, succeeded: bool, done: bool) -> tuple[str, str]:
    """(run_state, why) after a cycle, given the goal as it was BEFORE the cycle."""
    from navig.store.board import PURSUIT_ACTIVE, PURSUIT_DONE, PURSUIT_STOPPED

    cycles = int(goal.get("cycles_run") or 0) + 1
    if succeeded and done:
        return PURSUIT_DONE, "the objective is met"
    if not succeeded and int(goal.get("fail_streak") or 0) + 1 >= FAIL_STREAK_LIMIT:
        return PURSUIT_STOPPED, f"{FAIL_STREAK_LIMIT} cycles in a row failed"
    if cycles >= int(goal.get("max_cycles") or 0):
        return PURSUIT_STOPPED, f"reached its {goal.get('max_cycles')}-cycle limit"
    if not succeeded:
        streak = int(goal.get("fail_streak") or 0) + 1
        return PURSUIT_ACTIVE, f"this cycle failed ({streak} of {FAIL_STREAK_LIMIT} in a row); retrying next cycle"
    return PURSUIT_ACTIVE, "continuing"


# ── Scheduling (cron job per goal) ──────────────────────────────────────────


def schedule(goal: dict[str, Any]) -> dict[str, Any]:
    """Create (or replace) the goal's cron job in the LIVE scheduler store."""
    from navig.scheduler import habit_store

    return habit_store.create_job(
        name=cron_name(goal["id"]),
        schedule=goal["every"],
        command=cron_command(goal["id"]),
        timeout_seconds=CYCLE_TIMEOUT_SECONDS,
        replace=True,
        space=goal.get("space"),
    )


def unschedule(goal_id: str) -> int:
    from navig.scheduler import habit_store

    return habit_store.delete_jobs(cron_name(goal_id))


def find_job(goal_id: str) -> dict[str, Any] | None:
    from navig.scheduler import habit_store

    name = cron_name(goal_id)
    return next((j for j in habit_store.list_jobs() if j.get("name") == name), None)


# ── One cycle ───────────────────────────────────────────────────────────────


def _executor_for(gateway: Any):
    live = getattr(gateway, "mission_executor", None)
    if live is not None:
        return live
    from navig.missions.executor import MissionExecutor

    return MissionExecutor(gateway)


async def run_goal_cycle(
    gateway: Any,
    goal_id: str,
    *,
    store: Any = None,
    executor: Any = None,
    notify: bool = True,
) -> str:
    """Run ONE cycle of a standing goal. Returns a one-line outcome for the cron log.

    Never raises for a failed cycle — that is recorded on the goal (and counts toward
    the failure cap). Raising would make cron retry in five minutes, i.e. run extra
    cycles the operator never scheduled.
    """
    from navig.contracts.mission import Mission, MissionStatus
    from navig.store.board import PURSUIT_ACTIVE, get_board_store

    store = store or get_board_store()
    goal = store.get_goal(goal_id)
    if goal is None or not goal.get("objective"):
        unschedule(goal_id)
        return f"skipped: standing goal {goal_id} no longer exists (its schedule was removed)"
    if goal.get("run_state") != PURSUIT_ACTIVE:
        return f"skipped: goal is {goal.get('run_state')}"

    now = datetime.now(UTC)
    if not store.claim_pursuit_cycle(goal_id, now=_iso(now), stale_before=_iso(now - CLAIM_STALE_AFTER)):
        return "skipped: a cycle of this goal is already running"

    cycle = int(goal.get("cycles_run") or 0) + 1
    receipt_id: str | None = None
    try:
        ex = executor or _executor_for(gateway)
        mission = Mission(
            title=f"Goal: {goal['title']} (cycle {cycle})",
            capability=CAPABILITY,
            payload={"message": build_prompt(goal, cycle)},
            metadata={"autonomy": "auto", "goal_id": goal_id, "cycle": cycle},
            tags=["goal"],
        )
        mission = await ex.run_to_completion(mission)
        succeeded = mission.status == MissionStatus.SUCCEEDED
        if succeeded:
            done, report = parse_status(str(mission.result or ""))
        else:
            done, report = False, f"cycle {mission.status.value}: {mission.error or 'no detail'}"
        receipts = ex.store.list_receipts(mission_id=mission.mission_id, limit=1)
        receipt_id = receipts[0].receipt_id if receipts else None
    except Exception as exc:  # noqa: BLE001 — a crashed cycle is a failed cycle, not a cron retry
        logger.warning("standing goal %s cycle %s crashed: %s", goal_id, cycle, exc)
        succeeded, done, report = False, False, f"cycle crashed: {exc}"

    run_state, why = next_state(goal, succeeded=succeeded, done=done)
    finished = store.finish_pursuit_cycle(
        goal_id,
        succeeded=succeeded,
        report=report[:4000],
        receipt_id=receipt_id,
        run_state=run_state,
        finished_at=_iso(datetime.now(UTC)),
    ) or goal
    final_state = finished.get("run_state") or run_state
    if final_state != PURSUIT_ACTIVE:
        unschedule(goal_id)

    if notify:
        await _digest(finished, cycle=cycle, state=final_state, why=why, report=report, receipt_id=receipt_id)
    return f"cycle {cycle}: {final_state} — {why}" + (f" · receipt {receipt_id}" if receipt_id else "")


async def _digest(
    goal: dict[str, Any], *, cycle: int, state: str, why: str, report: str, receipt_id: str | None
) -> None:
    """One ``goal_digest`` per cycle (deck + Telegram by default). Best-effort."""
    try:
        from navig.notify.router import dispatch

        label = {"active": "continuing", "done": "done", "stopped": "stopped", "paused": "paused"}
        title = f"{goal.get('title')} — cycle {cycle}/{goal.get('max_cycles')}: {label.get(state, state)}"
        body = (report or "(no report)").strip()[:1500]
        if state != "active":
            body += f"\n\nThis goal is {state}: {why}."
        if receipt_id:
            body += f"\nReceipt: {receipt_id}"
        await dispatch(
            "goal_digest",
            title,
            body,
            priority="normal",
            data={"source": "navig", "goal_id": goal.get("id"), "receipt_id": receipt_id, "state": state},
        )
    except Exception:  # noqa: BLE001 — a report must never fail the cycle
        logger.debug("goal_digest dispatch failed for %s", goal.get("id"), exc_info=True)

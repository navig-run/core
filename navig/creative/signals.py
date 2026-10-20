"""Proactive opportunity signals — detectors over authorized history, then scan.

Detectors are pure functions over records the caller (or the default readers)
provides, so tests seed data instead of touching the operator's real stores:

- repeated manual operations      → ``detect_repeated_commands`` (≥5 / 7 d)
- recurring failures              → ``detect_recurring_failures`` (≥3 / 7 d)
- repeated multi-tool sequences   → ``detect_repeated_sequences`` (≥3 / 14 d)
- blocked / stopped goals         → ``detect_blocked_goals`` (streak or stopped)
- broken capabilities             → ``detect_broken_capabilities`` (index state)

``run_scan`` composes: read → evidence candidates → ``decide()`` → ``propose()``
→ throttled notify dispatch → ``touch_scan`` (the pacing heartbeat, updated even
on a no-op). Mode gate: Standard returns a documented skip without reading the
history at all — capability *discovery* stays available interactively, but the
PROACTIVE path only runs under Creative (owner mandate).

Scope: operation history + goal state ONLY. No recall / session chat content
(privacy — deferred by design). No LLM anywhere (guard-tested).
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from navig.creative.decide import (
    FAIL_CMD_MIN,
    REPEAT_CMD_MIN,
    SEQUENCE_MIN,
    Evidence,
    IndexView,
    decide,
)

logger = logging.getLogger(__name__)

WINDOW_COMMANDS_D = 7
WINDOW_SEQUENCES_D = 14
SEQUENCE_GAP_S = 1800  # steps closer than this belong to one run
GOAL_FAIL_STREAK_MIN = 3
OPERATIONS_SCAN_LIMIT = 500

__all__ = [
    "detect_repeated_commands",
    "detect_recurring_failures",
    "detect_repeated_sequences",
    "detect_blocked_goals",
    "detect_broken_capabilities",
    "collect_evidence",
    "run_scan",
]


# ── pure detectors ───────────────────────────────────────────────────────────


def _norm(command: str) -> str:
    return " ".join((command or "").split())


def _parse_ts(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def _in_window(ts: str | None, days: int, *, now: float) -> bool:
    parsed = _parse_ts(ts) if ts else None
    if parsed is None:
        return False
    return (now - parsed) <= days * 86400


def detect_repeated_commands(
    records: list[Any],
    *,
    days: int = WINDOW_COMMANDS_D,
    min_count: int = REPEAT_CMD_MIN,
    now: float | None = None,
) -> list[tuple[str, int]]:
    """[(normalized command, count)] for commands run ≥ *min_count* in *days*."""
    now = time.time() if now is None else now
    counts: Counter[str] = Counter()
    for rec in records:
        if not _in_window(getattr(rec, "timestamp", None), days, now=now):
            continue
        cmd = _norm(getattr(rec, "command", "") or "")
        if not cmd:
            continue
        counts[cmd] += 1
    return sorted(
        ((cmd, n) for cmd, n in counts.items() if n >= min_count),
        key=lambda row: (-row[1], row[0]),
    )


def detect_recurring_failures(
    records: list[Any],
    *,
    days: int = WINDOW_COMMANDS_D,
    min_count: int = FAIL_CMD_MIN,
    now: float | None = None,
) -> list[tuple[str, int]]:
    """[(command, failure count)] for commands FAILED ≥ *min_count* in *days*."""
    now = time.time() if now is None else now
    counts: Counter[str] = Counter()
    for rec in records:
        if not _in_window(getattr(rec, "timestamp", None), days, now=now):
            continue
        status = getattr(rec, "status", None)
        status_value = getattr(status, "value", status)
        if str(status_value).lower() not in ("failed", "error"):
            continue
        cmd = _norm(getattr(rec, "command", "") or "")
        if not cmd:
            continue
        counts[cmd] += 1
    return sorted(
        ((cmd, n) for cmd, n in counts.items() if n >= min_count),
        key=lambda row: (-row[1], row[0]),
    )


def detect_repeated_sequences(
    records: list[Any],
    *,
    days: int = WINDOW_SEQUENCES_D,
    min_count: int = SEQUENCE_MIN,
    gap_s: int = SEQUENCE_GAP_S,
    now: float | None = None,
) -> list[tuple[tuple[str, ...], int]]:
    """[(command sequence, occurrences)] for 2–3 step runs repeated in *days*.

    Records are time-ordered ascending; steps closer than *gap_s* form one run.
    Runs of 2–3 steps are counted as whole tuples; longer runs contribute their
    first 3 steps (deterministic, no combinatorial window explosion).
    """
    now = time.time() if now is None else now
    dated: list[tuple[float, str]] = []
    for rec in records:
        ts = _parse_ts(getattr(rec, "timestamp", "") or "")
        cmd = _norm(getattr(rec, "command", "") or "")
        if ts is None or not cmd:
            continue
        if (now - ts) > days * 86400:
            continue
        dated.append((ts, cmd))
    dated.sort(key=lambda row: row[0])

    runs: list[tuple[str, ...]] = []
    current: list[str] = []
    last_ts: float | None = None
    for ts, cmd in dated:
        if last_ts is not None and (ts - last_ts) > gap_s:
            if len(current) >= 2:
                runs.append(tuple(current[:3]))
            current = []
        current.append(cmd)
        last_ts = ts
    if len(current) >= 2:
        runs.append(tuple(current[:3]))

    counts: Counter[tuple[str, ...]] = Counter(runs)
    return sorted(
        ((seq, n) for seq, n in counts.items() if n >= min_count),
        key=lambda row: (-row[1], row[0]),
    )


def detect_blocked_goals(goals: list[dict[str, Any]]) -> list[str]:
    """Human-readable notes for stopped goals or goals on a failing streak.

    *goals* rows come from ``get_board_store().list_pursuits()`` (injected in
    tests — the live board.db is never touched by unit tests).
    """
    notes: list[str] = []
    for goal in goals:
        objective = str(goal.get("objective") or goal.get("id") or "").strip()
        if not objective:
            continue
        streak = int(goal.get("fail_streak") or 0)
        state = str(goal.get("run_state") or "")
        if state == "stopped":
            notes.append(f"{objective} (goal stopped)")
        elif streak >= GOAL_FAIL_STREAK_MIN:
            notes.append(f"{objective} (failing {streak}× in a row)")
    return notes


def detect_broken_capabilities(index: IndexView) -> list[dict[str, Any]]:
    """Index entries whose observed runtime state is broken."""
    broken: list[dict[str, Any]] = []
    for entry in index.find("", limit=10_000):  # empty query pages the catalog
        state = str(((entry.get("observed") or {}).get("state")) or "")
        if state in ("broken", "error"):
            broken.append(entry)
    return broken


# ── readers (production defaults; tests inject instead) ──────────────────────


def _read_operations(limit: int = OPERATIONS_SCAN_LIMIT) -> list[Any]:
    try:
        from navig.operation_recorder import OperationRecorder

        recorder = OperationRecorder()
        since = (
            datetime.now(timezone.utc) - timedelta(days=WINDOW_SEQUENCES_D)
        ).isoformat()
        return list(recorder.iter_operations(limit=limit, since=since, reverse=True))
    except Exception as exc:  # noqa: BLE001 - no history is a quiet no-op
        logger.debug("operation history unavailable for scan: %s", exc)
        return []


def _read_goal_signals() -> list[str]:
    try:
        from navig.store.board import get_board_store

        return detect_blocked_goals(get_board_store().list_pursuits())
    except Exception as exc:  # noqa: BLE001 - goal store unreachable → skip signal
        logger.debug("goal signals unavailable for scan: %s", exc)
        return []


# ── evidence assembly ────────────────────────────────────────────────────────


def collect_evidence(
    *,
    records: list[Any] | None = None,
    goals: list[dict[str, Any]] | None = None,
    index: IndexView | None = None,
    now: float | None = None,
) -> tuple[Evidence, list[dict[str, Any]]]:
    """Build the aggregate ``Evidence`` plus per-candidate problem statements.

    Each candidate is one friction point worth its own ``decide()`` call — a
    repeated command, a failure pattern, a sequence, a blocked goal — so one
    loud signal cannot mask another.
    """
    now = time.time() if now is None else now
    if records is None:
        records = _read_operations()
    view = index if index is not None else IndexView.live()

    repeated = detect_repeated_commands(records, now=now)
    failures = detect_recurring_failures(records, now=now)
    sequences = detect_repeated_sequences(records, now=now)
    if goals is None:
        blocked = _read_goal_signals()
    else:
        blocked = detect_blocked_goals(goals)

    evidence = Evidence(
        repeated_commands=repeated,
        failing_commands=failures,
        sequences=sequences,
        blocked_goals=blocked,
    )

    candidates: list[dict[str, Any]] = []
    for cmd, n in repeated:
        candidates.append(
            {
                "kind": "repeated_command",
                "goal": cmd,
                "problem": f"`{cmd}` was run {n}× in {WINDOW_COMMANDS_D} days",
                "evidence": [
                    {"kind": "repeated_command", "detail": cmd, "count": n,
                     "window": f"{WINDOW_COMMANDS_D}d"}
                ],
                "signal_evidence": Evidence(repeated_commands=[(cmd, n)]),
            }
        )
    for cmd, n in failures:
        candidates.append(
            {
                "kind": "failure",
                "goal": cmd,
                "problem": f"`{cmd}` failed {n}× in {WINDOW_COMMANDS_D} days",
                "evidence": [
                    {"kind": "failure", "detail": cmd, "count": n,
                     "window": f"{WINDOW_COMMANDS_D}d"}
                ],
                "signal_evidence": Evidence(failing_commands=[(cmd, n)]),
            }
        )
    for seq, n in sequences:
        joined = " → ".join(seq)
        candidates.append(
            {
                "kind": "sequence",
                "goal": joined,
                "problem": f"Sequence run {n}× in {WINDOW_SEQUENCES_D} days: {joined}",
                "evidence": [
                    {"kind": "sequence", "detail": joined, "count": n,
                     "window": f"{WINDOW_SEQUENCES_D}d"}
                ],
                "signal_evidence": Evidence(sequences=[(seq, n)]),
            }
        )
    for note in blocked:
        candidates.append(
            {
                "kind": "blocked_goal",
                "goal": note,
                "problem": f"Blocked goal: {note}",
                "evidence": [{"kind": "blocked_goal", "detail": note, "count": 1,
                              "window": "current"}],
                "signal_evidence": Evidence(blocked_goals=[note]),
            }
        )
    for entry in detect_broken_capabilities(view):
        eid = str(entry.get("id") or "")
        candidates.append(
            {
                "kind": "broken_capability",
                "goal": str(entry.get("name") or eid),
                "problem": f"Capability `{eid}` reports broken",
                "evidence": [{"kind": "broken_capability", "detail": eid, "count": 1,
                              "window": "current"}],
                "signal_evidence": Evidence(requested=[str(entry.get("name") or eid)]),
            }
        )
    return evidence, candidates


# ── scan orchestration ───────────────────────────────────────────────────────


def run_scan(
    *,
    mode: str | None = None,
    records: list[Any] | None = None,
    goals: list[dict[str, Any]] | None = None,
    index: IndexView | None = None,
    dispatch: Callable[..., Any] | None = None,
    now: float | None = None,
    touch: bool = True,
) -> dict[str, Any]:
    """One proactive pass. Returns a summary; a no-op is a first-class result.

    *mode* ``None`` reads the live agent mode; anything other than ``creative``
    skips WITHOUT reading history (Standard scans nothing proactively).
    *dispatch* defaults to the real notify router; a custom callable receives
    the proposal dict (tests capture it instead of paging anyone).
    """
    now = time.time() if now is None else now
    if mode is None:
        from navig.creative.mode import get_agent_mode

        mode = get_agent_mode()
    summary: dict[str, Any] = {
        "mode": mode,
        "candidates": 0,
        "created": [],
        "duplicates": 0,
        "rate_limited": 0,
        "dispatched": [],
        "noop": 0,
        "skipped": mode != "creative",
    }
    if mode != "creative":
        # Pacing heartbeat still records the run (so cadence stays honest).
        if touch:
            from navig.creative.proposals import touch_scan

            touch_scan(now=now)
        return summary

    view = index if index is not None else IndexView.live()
    _evidence, candidates = collect_evidence(
        records=records, goals=goals, index=view, now=now
    )
    summary["candidates"] = len(candidates)

    from navig.creative import proposals as prop
    from navig.creative.proposals import from_decision

    if dispatch is None:
        dispatch = _default_dispatch

    for cand in candidates:
        decision = decide(
            cand["goal"],
            evidence=cand["signal_evidence"],
            index=view,
            config={},
        )
        if decision.is_noop:
            summary["noop"] += 1
            continue
        proposal = from_decision(
            decision,
            source=f"detector:{cand['kind']}",
            problem=cand["problem"],
            evidence=cand["evidence"],
            goal=cand["goal"],
        )
        created, reason = prop.propose(proposal, now=now)
        if not created:
            if reason.startswith("duplicate:"):
                summary["duplicates"] += 1
            else:
                summary["rate_limited"] += 1
            continue
        summary["created"].append(proposal["id"])
        # Announce once per proposal id (cross-process, restart-safe); a held
        # claim means another surface already announced it.
        if prop.claim_announce(proposal["id"], now=now):
            if _safe_dispatch(dispatch, proposal):
                summary["dispatched"].append(proposal["id"])

    if touch:
        prop.touch_scan(now=now)
    return summary


def _default_dispatch(proposal: dict[str, Any]) -> None:
    from navig.notify.router import dispatch

    problem = str(proposal.get("problem") or "Suggestion")
    verdict = str(proposal.get("verdict") or "")
    body = f"{problem}\n\n{proposal.get('decision', {}).get('reason', '')}".strip()
    dispatch(
        "creative_proposal",
        title=f"💡 {problem}"[:120],
        body=body,
        priority=2,
        data={
            "proposal_id": proposal.get("id"),
            "verdict": verdict,
            "surface": "creative",
        },
    )


def _safe_dispatch(dispatch: Callable[..., Any], proposal: dict[str, Any]) -> bool:
    """Announce; a failed dispatch gives the claim back. True when delivered."""
    try:
        dispatch(proposal)
        return True
    except Exception as exc:  # noqa: BLE001 - a failed announce never kills the scan
        logger.warning("creative proposal dispatch failed: %s", exc)
        from navig.creative.proposals import release_announce

        release_announce(str(proposal.get("id") or ""))
        return False

"""Proposal store — stable ids, permanent lifecycle dedupe, rate caps, scan pace.

A proposal is one improvement NAVIG surfaced: the card data (mission §5), the
decision behind it, and its lifecycle (open → accepted → building → built /
failed, or dismissed). Persisted to ``data_dir()/creative/proposals.json`` —
atomic write, a few dozen rows, no SQLite ceremony (law 8 binds raw sqlite in
commands, not a JSON store; precedent: alert_ledger).

Dedupe design reconciles the three existing namespaces deliberately:
- **Lifecycle (ours, permanent):** content-addressed ids. A dismissed or built
  proposal is NEVER re-proposed — the ``board_todo_dismissed`` rule ("a
  dismissed suggestion that comes back is how a helpful assistant becomes a nag").
- **Announce throttle:** ``notify.dedupe.claim("creative:<id>")`` — cross-process,
  restart-safe, for the notification dispatch only.
- **``AlertLedger`` is deliberately NOT used** — its 14-day retention would
  resurrect old ideas; our store keeps them suppressed forever.

Rate caps: ≤ ``MAX_OPEN`` proposals open at once, ≤ ``MAX_NEW_PER_DAY`` created
per day. Quiet hours / master toggle come free from the notify router.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.platform.paths import data_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_OPEN = 5
MAX_NEW_PER_DAY = 2

#: Announce cooldown per proposal id (the dispatch may be repeated only after
#: this window; the ROW itself is never re-created once terminal).
ANNOUNCE_COOLDOWN_S = 24 * 3600

STATUS_OPEN = "open"
STATUS_ACCEPTED = "accepted"
STATUS_BUILDING = "building"
STATUS_BUILT = "built"
STATUS_FAILED = "failed"
STATUS_DISMISSED = "dismissed"
TERMINAL_STATUSES = (STATUS_BUILT, STATUS_DISMISSED)

__all__ = [
    "SCHEMA_VERSION",
    "MAX_OPEN",
    "MAX_NEW_PER_DAY",
    "ANNOUNCE_COOLDOWN_S",
    "proposals_path",
    "stable_id",
    "load",
    "save",
    "list_proposals",
    "get_proposal",
    "propose",
    "set_status",
    "claim_announce",
    "touch_scan",
    "last_scan_at",
    "from_decision",
    "evidence_hash",
]


def proposals_path() -> Path:
    return data_dir() / "creative" / "proposals.json"


def evidence_hash(evidence: list[dict[str, Any]]) -> str:
    """Stable hash of the evidence rows (order-insensitive)."""
    parts = sorted(
        f"{row.get('kind')}:{row.get('detail')}:{row.get('count')}" for row in evidence
    )
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def stable_id(kind: str, goal: str, evidence: list[dict[str, Any]]) -> str:
    """Content-addressed proposal id — same friction, same id, across scans."""
    raw = f"{kind}|{(goal or '').strip().lower()}|{evidence_hash(evidence)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ── envelope persistence ─────────────────────────────────────────────────────


def _empty() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "last_scan_at": None,
        "new_today": {"date": _today(), "count": 0},
        "proposals": [],
    }


def _today() -> str:
    """The operator's LOCAL calendar day — the daily cap answers "which day?",
    and a UTC day is a different day for hours each night (house rule:
    keep UTC for instants — created_at/updated_at below — local for days)."""
    from datetime import date

    return date.today().isoformat()


def load() -> dict[str, Any]:
    """Read the store; a bad file is a miss with a logged warning (never a wipe)."""
    path = proposals_path()
    if not path.exists():
        return _empty()
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("proposals"), list):
            if data.get("schema_version") != SCHEMA_VERSION:
                logger.warning("proposals.json schema mismatch — starting empty (kept on disk)")
                return _empty()
            data.setdefault("new_today", {"date": _today(), "count": 0})
            data.setdefault("last_scan_at", None)
            return data
    except Exception as exc:  # noqa: BLE001 - corrupt store must not crash a scan
        logger.warning("proposals.json unreadable, treating as empty: %s", exc)
    return _empty()


def save(env: dict[str, Any]) -> None:
    path = proposals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from navig.core.json_io import atomic_write_json

    atomic_write_json(env, path)


# ── queries ──────────────────────────────────────────────────────────────────


def list_proposals(*, status: str | None = None) -> list[dict[str, Any]]:
    rows = load().get("proposals", [])
    if status is None:
        return list(rows)
    return [p for p in rows if p.get("status") == status]


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    for p in load().get("proposals", []):
        if p.get("id") == proposal_id:
            return p
    return None


def known_id(proposal_id: str) -> bool:
    """True for ANY existing row — dismissed/built included (never re-propose)."""
    return get_proposal(proposal_id) is not None


# ── mutation ─────────────────────────────────────────────────────────────────


def _rate_problem(env: dict[str, Any]) -> str | None:
    open_count = sum(1 for p in env.get("proposals", []) if p.get("status") == STATUS_OPEN)
    if open_count >= MAX_OPEN:
        return f"open-proposal cap reached ({MAX_OPEN})"
    new = env.get("new_today") or {}
    if new.get("date") == _today() and int(new.get("count", 0)) >= MAX_NEW_PER_DAY:
        return f"daily proposal cap reached ({MAX_NEW_PER_DAY})"
    return None


def propose(
    proposal: dict[str, Any],
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """Insert a proposal if it is new AND caps allow. ``(created, reason)``.

    A known id (any status) returns ``(False, "duplicate:…")`` without touching
    the existing row — the permanent lifecycle dedupe. Rate problems return
    ``(False, "rate:…")``. The caller decides whether a refusal is a no-op.
    """
    pid = proposal.get("id")
    if not pid:
        raise ValueError("proposal.id is required")
    env = load()
    if any(p.get("id") == pid for p in env.get("proposals", [])):
        return False, f"duplicate:{pid}"

    problem = _rate_problem(env)
    if problem is not None:
        return False, f"rate:{problem}"

    row = dict(proposal)
    row.setdefault("status", STATUS_OPEN)
    row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    row.setdefault("updated_at", row["created_at"])
    env["proposals"].append(row)

    today = _today()
    new = env.get("new_today") or {}
    count = int(new.get("count", 0)) + 1 if new.get("date") == today else 1
    env["new_today"] = {"date": today, "count": count}
    save(env)
    return True, "created"


def set_status(
    proposal_id: str,
    status: str,
    *,
    error: str | None = None,
    version_ref: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Transition a proposal; returns the updated row (or None if absent)."""
    env = load()
    for row in env.get("proposals", []):
        if row.get("id") == proposal_id:
            row["status"] = status
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            if error is not None:
                row["error"] = error
            if version_ref is not None:
                row["version_ref"] = version_ref
            if status == STATUS_BUILT:
                row.pop("error", None)
            save(env)
            return row
    return None


def claim_announce(proposal_id: str, *, now: float | None = None) -> bool:
    """Cross-process announce throttle for this proposal's dispatch."""
    from navig.notify.dedupe import claim

    return claim(f"creative:{proposal_id}", ANNOUNCE_COOLDOWN_S, now=now)


def release_announce(proposal_id: str) -> None:
    """Give the claim back when the dispatch never reached a channel."""
    from navig.notify.dedupe import release

    release(f"creative:{proposal_id}")


def touch_scan(now: float | None = None) -> None:
    """Record that a scan ran — even a no-op scan (its pacing heartbeat)."""
    env = load()
    env["last_scan_at"] = now if now is not None else time.time()
    save(env)


def last_scan_at() -> float | None:
    value = load().get("last_scan_at")
    return float(value) if isinstance(value, (int, float)) else None


# ── construction from a Decision ─────────────────────────────────────────────


def from_decision(
    decision: Any,
    *,
    source: str,
    problem: str,
    evidence: list[dict[str, Any]],
    solution: str | None = None,
    goal: str = "",
    risk: str | None = None,
) -> dict[str, Any]:
    """Build the card payload from a ``Decision`` (pure — no persistence here)."""
    from navig.creative.decide import Decision

    if not isinstance(decision, Decision):  # pragma: no cover - contract guard
        raise TypeError("from_decision expects a Decision")
    impact_risk = risk or _default_risk(decision)
    return {
        "id": stable_id(decision.verdict, goal or problem, evidence),
        "source": source,
        "goal": goal,
        "problem": problem,
        "evidence": evidence,
        "verdict": decision.verdict,
        "decision": {
            "verdict": decision.verdict,
            "reason": decision.reason,
            "matched": [asdict(m) for m in decision.matched],
            "reused_components": list(decision.reused_components),
            "genuine_gap": decision.genuine_gap,
            "plan": list(decision.plan),
            "benefit": decision.benefit,
            "complexity": decision.complexity,
            "verification": decision.verification,
            "assumptions": list(decision.assumptions),
        },
        "solution": solution or _default_solution(decision),
        "reuse_components": list(decision.reused_components),
        "expected_benefit": decision.benefit,
        "impact": {
            "risk": impact_risk,
            "reversible": decision.verdict in ("create", "compose", "configure"),
        },
        "artifact": None if decision.artifact is None else asdict(decision.artifact),
        "assumptions": list(decision.assumptions),
        "status": STATUS_OPEN,
        "version_ref": None,
    }


def _default_solution(decision: Any) -> str:
    if decision.verdict == "create" and decision.artifact is not None:
        return (
            f"Build the `{decision.artifact.slug}` skill wrapping "
            f"{len(decision.artifact.commands)} proven command(s), validate, activate."
        )
    if decision.plan:
        return decision.plan[0]
    return decision.reason


def _default_risk(decision: Any) -> str:
    """Risk from the DECISION KIND, through the blocks vocabulary where real."""
    if decision.verdict in ("reuse", "noop"):
        return "safe"
    if decision.verdict in ("configure", "compose"):
        return "moderate"
    # create/extend: declare the artifact's capability risk through the same
    # policy Blocks use (risk from declared capabilities, never self-asserted).
    try:
        from navig.blocks.policy import capability_risk

        if decision.artifact is not None:
            return str(capability_risk(["filesystem:write:skills"]))
    except Exception:  # noqa: BLE001 - policy unavailable → conservative default
        pass
    return "moderate"

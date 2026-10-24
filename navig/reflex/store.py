"""Commit-plan persistence — the committed token lives in the existing RuntimeStore.

There is **no new database**: the plan/commit token is a row in ``~/.navig/runtime.db``
(``commit_plans``, schema v5, additive migration). That is the mission's "no third
token store" rule made concrete: ``navig-voice`` keeps its JSON fallback for the
plugin's own path, and this table is the core contract every surface shares.

Claims are atomic. ``claim()`` is a single conditional ``UPDATE … WHERE state =
'pending' AND expires_at > now`` under ``BEGIN IMMEDIATE`` (via ``BaseStore._write``),
so two processes racing the same token produce exactly one ``fresh=True``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from navig.reflex.errors import PlanError, PlanExpired, PlanUnknown
from navig.reflex.plans import (
    KEEP_FINISHED_SECONDS,
    PLAN_TTL_SECONDS,
    CommitPlan,
    new_token,
)
from navig.store.base import _to_utc_iso, _utcnow
from navig.store.runtime import RuntimeStore, get_runtime_store

logger = logging.getLogger(__name__)

_COLUMNS = (
    "token, created_at, expires_at, state, tool, profile, destructive,"
    " plan_json, result_json, executed_at"
)


def _now_iso(clock: Callable[[], datetime] | None = None) -> str:
    if clock is None:
        return _utcnow()
    return _to_utc_iso(clock())


class CommitPlanStore:
    """Reads/writes ``commit_plans`` rows; owns the token lifecycle."""

    def __init__(
        self,
        store: RuntimeStore | None = None,
        *,
        ttl_seconds: int = PLAN_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = store
        self._ttl = int(ttl_seconds)
        self._clock = clock

    @property
    def store(self) -> RuntimeStore:
        """The RuntimeStore, opened on first use (a plain ``navig ask`` opens no DB)."""
        if self._runtime is None:
            self._runtime = get_runtime_store()
        return self._runtime

    def now(self) -> str:
        return _now_iso(self._clock)

    def create(self, plan: CommitPlan) -> CommitPlan:
        """Persist *plan*; the caller passes a plan built with :func:`new_token`."""
        if not plan.token:
            plan.token = new_token()
        now = self.now()
        plan.created_at = now
        if not plan.expires_at:
            plan.expires_at = self._expiry(now)
        self._purge(now)
        self.store._write(
            "INSERT OR REPLACE INTO commit_plans"
            " (token, created_at, expires_at, state, tool, profile, destructive,"
            "  plan_json, result_json, executed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plan.token,
                plan.created_at,
                plan.expires_at,
                plan.state,
                plan.tool,
                plan.profile,
                1 if plan.destructive else 0,
                json.dumps(plan.as_dict(), ensure_ascii=False),
                None,
                plan.executed_at,
            ),
        )
        return plan

    def get(self, token: str) -> CommitPlan | None:
        wanted = str(token or "").strip()
        if not wanted:
            return None
        row = self.store._read_one(
            f"SELECT {_COLUMNS} FROM commit_plans WHERE token = ?", (wanted,)
        )
        return self._plan_from_row(row) if row is not None else None

    def claim(self, token: str) -> tuple[CommitPlan, bool]:
        """Reserve *token*. ``fresh=False`` means it already ran (or is running).

        Raises :class:`PlanUnknown` for a token that was never minted and
        :class:`PlanExpired` for one whose 300 s window closed before this claim.
        """
        wanted = str(token or "").strip()
        if not wanted:
            raise PlanUnknown(
                "no commit token given", "mint one with: navig ask \"<request>\" --plan"
            )
        now = self.now()
        cursor = self.store._write(
            "UPDATE commit_plans SET state = 'committed', executed_at = ?"
            " WHERE token = ? AND state = 'pending' AND expires_at > ?",
            (now, wanted, now),
        )
        if cursor.rowcount == 1:
            plan = self.get(wanted)
            if plan is None:  # pragma: no cover — the row cannot vanish inside one lock
                raise PlanUnknown("commit token vanished", "mint a new plan with --plan")
            plan.state = "committed"
            plan.executed_at = now
            return plan, True

        row = self.store._read_one(
            f"SELECT {_COLUMNS} FROM commit_plans WHERE token = ?", (wanted,)
        )
        if row is None:
            raise PlanUnknown(
                f"unknown commit token {wanted!r}",
                "mint one with: navig ask \"<request>\" --plan",
            )
        plan = self._plan_from_row(row)
        if plan.state == "pending":
            raise PlanExpired(
                f"commit token {wanted!r} expired at {plan.expires_at}",
                "run --plan again, then --commit the new token",
            )
        return plan, False

    def finish(
        self,
        token: str,
        result: dict[str, Any],
        *,
        ok: bool = True,
    ) -> CommitPlan:
        """Store the outcome so a retried ``--commit`` replays it instead of re-running."""
        now = self.now()
        self.store._write(
            "UPDATE commit_plans SET state = ?, result_json = ?,"
            " executed_at = COALESCE(executed_at, ?) WHERE token = ?",
            ("done" if ok else "failed", json.dumps(result, ensure_ascii=False), now, str(token)),
        )
        plan = self.get(token)
        if plan is None:
            raise PlanUnknown(f"unknown commit token {token!r}", "mint a new plan with --plan")
        return plan

    def pending(self) -> list[CommitPlan]:
        """Every unexpired pending plan (newest first) — for diagnostics."""
        now = self.now()
        rows = self.store._read_all(
            f"SELECT {_COLUMNS} FROM commit_plans"
            " WHERE state = 'pending' AND expires_at > ? ORDER BY created_at DESC",
            (now,),
        )
        return [self._plan_from_row(row) for row in rows]

    # ── internals ────────────────────────────────────────────────────────────

    def _expiry(self, now: str) -> str:
        if self._clock is None:
            moment = datetime.now(timezone.utc) + timedelta(seconds=self._ttl)
        else:
            moment = self._clock() + timedelta(seconds=self._ttl)
        return _to_utc_iso(moment)

    def _purge(self, now: str) -> None:
        """Drop expired pending plans and long-finished rows; never touching live ones."""
        cutoff_dt = (
            datetime.now(timezone.utc)
            if self._clock is None
            else self._clock()
        ) - timedelta(seconds=KEEP_FINISHED_SECONDS)
        cutoff = _to_utc_iso(cutoff_dt)
        try:
            self.store._write(
                "DELETE FROM commit_plans WHERE (state = 'pending' AND expires_at <= ?)"
                " OR (state IN ('done', 'failed') AND COALESCE(executed_at, created_at) <= ?)",
                (now, cutoff),
            )
        except Exception as exc:  # noqa: BLE001 — pruning must never fail a fresh plan
            logger.warning("commit_plans purge skipped: %s", exc)

    @staticmethod
    def _plan_from_row(row: Any) -> CommitPlan:
        raw = row["plan_json"]
        if not raw:
            raise PlanError("commit plan row has no payload", "mint a new plan with --plan")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise PlanError(
                "commit plan row is unreadable", "mint a new plan with --plan"
            ) from exc
        plan = CommitPlan.from_dict(payload)
        plan.state = str(row["state"] or plan.state)
        plan.expires_at = str(row["expires_at"] or plan.expires_at)
        result_raw = row["result_json"]
        if result_raw:
            try:
                stored = json.loads(result_raw)
            except (TypeError, ValueError):
                stored = None
            if isinstance(stored, dict):
                plan.result = stored
        plan.executed_at = row["executed_at"]
        return plan


__all__ = ["CommitPlanStore"]

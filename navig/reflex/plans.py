"""One action's plan and its idempotency key.

A ``CommitPlan`` is the frozen record of *what would happen*: the tool, the typed
slots, the resolved targets, the blast radius, the profile that produced it, the
exact CLI argv, and the token. It is created by :meth:`ReflexRouter.plan` (which
mutates nothing on any device) and consumed exactly once by
:meth:`ReflexRouter.commit`.

Token semantics follow the shipped voice plugin's ``PlanStore``: ``plan-<hex>``,
a 300 s TTL, states ``pending|committed|done|failed``, and the stored Answer is
returned on replay so a retried ``--commit`` is a no-op with the same result.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: How long a minted plan may be committed — the same window the voice plugin uses.
PLAN_TTL_SECONDS = 300

#: States persisted in ``runtime.db``'s ``commit_plans`` table.
PLAN_STATES: tuple[str, ...] = ("pending", "committed", "done", "failed")

#: Finished plans are kept this long so a replay can still return the stored result.
KEEP_FINISHED_SECONDS = 86_400


def new_token() -> str:
    """A fresh commit token (``plan-`` + 10 hex chars, the voice plugin's shape)."""
    return f"plan-{secrets.token_hex(5)}"


def utc_now_iso() -> str:
    """Canonical UTC ISO-8601 (``…Z``), the shape every RuntimeStore TTL column uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class CommitPlan:
    """A persisted plan: the tool, its target(s), the command(s), and the token."""

    token: str
    tool: str
    command: list[str]
    profile: str = "fast"
    risk: str = "low"
    read_only: bool = False
    destructive: bool = False
    slots: dict[str, Any] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)
    blast_radius: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    expires_at: str = ""
    state: str = "pending"
    result: dict[str, Any] | None = None
    executed_at: str | None = None

    def expired(self, now: str | None = None) -> bool:
        """Is this plan past its TTL? Compared as the canonical string the store writes."""
        return (now or utc_now_iso()) > self.expires_at

    @property
    def next_command(self) -> str:
        """The exact command that finishes this plan (used in hints)."""
        return f'navig ask --commit {self.token}'

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "tool": self.tool,
            "command": list(self.command),
            "profile": self.profile,
            "risk": self.risk,
            "read_only": self.read_only,
            "destructive": self.destructive,
            "slots": dict(self.slots),
            "targets": [dict(item) for item in self.targets],
            "blast_radius": dict(self.blast_radius),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "state": self.state,
            "result": self.result,
            "executed_at": self.executed_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CommitPlan":
        return cls(
            token=str(payload.get("token") or ""),
            tool=str(payload.get("tool") or ""),
            command=[str(item) for item in payload.get("command") or []],
            profile=str(payload.get("profile") or "fast"),
            risk=str(payload.get("risk") or "low"),
            read_only=bool(payload.get("read_only")),
            destructive=bool(payload.get("destructive")),
            slots=dict(payload.get("slots") or {}),
            targets=[dict(item) for item in payload.get("targets") or [] if isinstance(item, dict)],
            blast_radius=dict(payload.get("blast_radius") or {}),
            created_at=str(payload.get("created_at") or ""),
            expires_at=str(payload.get("expires_at") or ""),
            state=str(payload.get("state") or "pending"),
            result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
            executed_at=payload.get("executed_at"),
        )


__all__ = [
    "CommitPlan",
    "KEEP_FINISHED_SECONDS",
    "PLAN_STATES",
    "PLAN_TTL_SECONDS",
    "new_token",
    "utc_now_iso",
]

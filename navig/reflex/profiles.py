"""Routing profiles — the approval presets, as a name → policy table.

Profiles are **not** routing-quality weights. The shape is a name → policy table:
rows are the **mutation class** of the candidate (read-only / one target / a batch),
columns are the profile, and every cell is ``auto`` or ``plan-only``. There is no
cost, latency or quality number anywhere — NAVIG has no per-tool price or latency
telemetry, and risk is a **gate**, not a weight.

The table maps onto the approval vocabulary that already ships:

* rows come from the candidate's contract (read-only / one target / a batch);
* the profile selects an existing ``navig.tools.approval.ApprovalPolicy`` member
  (``confirm_all`` for ``safe``/``campaign``, ``confirm_destructive`` for ``fast``);
* ``approval.security_level = high`` is a stricter override: a single-target
  mutation becomes plan-only in every profile, including ``fast``.

A cell can only ever *add* plan-only — a strict approval decision never loosens.
"""

from __future__ import annotations

import logging

from navig.reflex.errors import ReflexError
from navig.reflex.manifest import RISK_TO_SAFETY

logger = logging.getLogger(__name__)

#: The three profiles the plan froze; ``fast`` is the default.
PROFILE_NAMES: tuple[str, ...] = ("safe", "fast", "campaign")

DEFAULT_PROFILE = "fast"

PROFILE_SUMMARY: dict[str, str] = {
    "safe": "plan every mutation; finish it with an explicit --commit",
    "fast": "auto-commit one reversible action; plan destructive batches",
    "campaign": "plan every mutation (the campaign lane owns unattended runs)",
}

#: Rows of the policy table — derived from the candidate, never from the tool's mood.
MUTATION_CLASSES: tuple[str, ...] = ("read", "single", "destructive")

#: cell value: auto = plan+commit now; plan_only = needs an explicit --commit.
AUTO = "auto"
PLAN_ONLY = "plan_only"

#: profile → cell for each mutation class. The whole routing policy, in one place.
POLICY_TABLE: dict[str, dict[str, str]] = {
    "safe": {"read": AUTO, "single": PLAN_ONLY, "destructive": PLAN_ONLY},
    "fast": {"read": AUTO, "single": AUTO, "destructive": PLAN_ONLY},
    "campaign": {"read": AUTO, "single": PLAN_ONLY, "destructive": PLAN_ONLY},
}

#: profile → existing ApprovalPolicy member (used only as a stricter override).
_PROFILE_POLICY: dict[str, str] = {
    "safe": "confirm_all",
    "fast": "confirm_destructive",
    "campaign": "confirm_all",
}

#: Risks that are plan-only in EVERY profile: a high-risk tool is gated like a batch.
_NEVER_AUTO: frozenset[str] = frozenset({"high", "destructive"})


def resolve_profile(value: object) -> str:
    """Validate a profile name; unknown values are a usage error, never a silent default.

    ``None`` means "the operator's security posture decides": it resolves through
    :mod:`navig.security.posture` so the one switch moves the reflex profile too.
    An explicit ``--profile`` still wins for that invocation.
    """
    if value is None:
        try:
            from navig.security import posture  # noqa: PLC0415

            return posture.reflex_profile()
        except Exception:  # noqa: BLE001 — a posture read must never break routing
            return DEFAULT_PROFILE
    text = str(value).strip().lower()
    if text not in PROFILE_NAMES:
        raise ReflexError(
            f"unknown profile {text!r}",
            f"use one of: {', '.join(PROFILE_NAMES)}",
        )
    return text


def configured_security_level() -> str:
    """``approval.security_level`` from a fresh config read (default ``medium``).

    A failed read must not become a permissive posture: the honest default is the
    shipped one, ``medium``, which is what ``coerce_security_level`` returns for
    anything unrecognised.
    """
    try:
        from navig.approval.policies import ApprovalPolicy  # noqa: PLC0415

        return str(ApprovalPolicy.from_config(_global_config()).security_level)
    except Exception as exc:  # noqa: BLE001 — a config problem must not break routing
        logger.debug("approval.security_level unreadable (%s); using medium", exc)
        return "medium"


def _global_config() -> dict:
    from navig.config import ConfigManager  # noqa: PLC0415

    return ConfigManager().global_config or {}


def _approval_policy(profile: str):
    """The existing ``ApprovalPolicy`` member this profile maps onto (lazy import)."""
    from navig.tools.approval import ApprovalPolicy  # noqa: PLC0415

    return ApprovalPolicy(_PROFILE_POLICY.get(profile, "confirm_destructive"))


def mutation_class(*, read_only: bool, risk: str, destructive: bool) -> str:
    """Pick the policy-table row for one candidate (fail closed on a high risk)."""
    if read_only:
        return "read"
    if destructive or str(risk) in _NEVER_AUTO:
        return "destructive"
    return "single"


def needs_explicit_commit(
    profile: str,
    *,
    read_only: bool,
    risk: str,
    destructive: bool,
    tool_name: str,
    security_level: str = "medium",
) -> bool:
    """True when the action may not run without ``--commit`` (nothing mutates here)."""
    row = mutation_class(read_only=read_only, risk=risk, destructive=destructive)
    cell = POLICY_TABLE.get(resolve_profile(profile), POLICY_TABLE[DEFAULT_PROFILE])[row]
    if cell == PLAN_ONLY:
        return True
    if row == "single" and str(security_level).strip().lower() == "high":
        return True
    if row != "single":
        return False
    try:
        from navig.tools.approval import needs_approval  # noqa: PLC0415

        return bool(
            needs_approval(
                tool_name,
                safety_level=RISK_TO_SAFETY.get(risk, "safe"),
                policy=_approval_policy(profile),
            )
        )
    except Exception as exc:  # noqa: BLE001 — a broken gate must fail CLOSED (plan-only)
        logger.debug("approval lookup failed (%s); planning the action instead", exc)
        return True


def describe(profile: str) -> dict[str, object]:
    """Machine-readable profile summary (used by ``--json`` and docs)."""
    name = resolve_profile(profile)
    row = POLICY_TABLE[name]
    return {
        "name": name,
        "summary": PROFILE_SUMMARY[name],
        "mutation_classes": dict(row),
        "auto_commit_read_only": row["read"] == AUTO,
        "auto_commit_single_mutation": row["single"] == AUTO,
        "auto_commit_destructive": row["destructive"] == AUTO,
    }


__all__ = [
    "AUTO",
    "DEFAULT_PROFILE",
    "MUTATION_CLASSES",
    "PLAN_ONLY",
    "POLICY_TABLE",
    "PROFILE_NAMES",
    "PROFILE_SUMMARY",
    "configured_security_level",
    "describe",
    "mutation_class",
    "needs_explicit_commit",
    "resolve_profile",
]

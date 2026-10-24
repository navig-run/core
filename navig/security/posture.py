"""One security posture for every level system in NAVIG.

The tree grew several level vocabularies for the same question — *how much is
trusted?*:

* ``approval.security_level`` — ``high`` / ``medium`` / ``low`` (the router-firewall
  knob: reads, timeouts, and the shell family);
* reflex **risk** levels — ``low`` / ``medium`` / ``high`` / ``destructive`` on each
  tool manifest (a per-action gate, not a posture);
* reflex **profiles** — ``safe`` / ``fast`` / ``campaign`` (auto vs plan-only per
  mutation class);
* tools **ApprovalPolicy** — ``yolo`` / ``confirm_destructive`` / ``confirm_all``
  (which tool calls the chat agent must confirm);
* MCP tool annotations — ``readOnlyHint`` / ``destructiveHint`` (a per-tool hint the
  approval gate reads).

This module is the **one switch**: the operator sets one level and the derived
vocabularies follow from this single table. It deliberately does not invent a fifth
vocabulary — ``strict`` / ``balanced`` / ``permissive`` are the aliases
``approval.policies`` already accepts for ``high`` / ``medium`` / ``low``.

Per-action risk stays per-action: it is declared by the tool itself and cannot be
"switched lower" by a posture. A posture only decides the *defaults* — the
``high`` level still makes single-target reflex mutations plan-only, and
``approval.enabled: false`` remains the master kill-switch outside every level.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "LEVEL_SUMMARY_COPY",
    "POSTURES",
    "approval_policy",
    "describe",
    "level",
    "posture",
    "reflex_profile",
]

#: Operator-facing posture names, strictest → loosest. They are the alias spellings
#: ``navig.approval.policies`` already normalises to high/medium/low.
POSTURES: tuple[str, ...] = ("strict", "balanced", "permissive")

_LEVEL_TO_POSTURE: dict[str, str] = {"high": "strict", "medium": "balanced", "low": "permissive"}

#: The reflex routing profile each level implies. ``safe`` plans every mutation,
#: ``campaign`` plans every mutation too but is the unattended lane's name, and
#: ``fast`` auto-commits one reversible action (still audited).
_PROFILE_FOR_LEVEL: dict[str, str] = {"high": "safe", "medium": "fast", "low": "fast"}

#: The chat agent's default approval policy per level. ``low`` deliberately stays on
#: ``confirm_destructive`` — the level's shell exemption is implemented by the level
#: classifier, NOT by disabling confirmation for db/cdp/connector tools.
_POLICY_FOR_LEVEL: dict[str, str] = {
    "high": "confirm_all",
    "medium": "confirm_destructive",
    "low": "confirm_destructive",
}

#: One line per level, copied from ``approval.policies.LEVEL_SUMMARY`` at import time so
#: the status surfaces cannot describe a different level than the one being applied.
LEVEL_SUMMARY_COPY: dict[str, str] = {}


def _configured_level() -> str:
    """``approval.security_level`` from a fresh config read, default ``medium``."""
    try:
        from navig.approval.policies import DEFAULT_SECURITY_LEVEL, parse_security_level
        from navig.config import ConfigManager

        raw = ((ConfigManager().global_config or {}).get("approval") or {}).get(
            "security_level"
        )
        return parse_security_level(raw) or DEFAULT_SECURITY_LEVEL
    except Exception:  # noqa: BLE001 — an unreadable config keeps the shipped default
        logger.debug("posture: security_level unreadable; using medium", exc_info=True)
        return "medium"


def level(override: str | None = None) -> str:
    """The canonical level (``high``/``medium``/``low``) for *override* or the config."""
    if override is None:
        return _configured_level()
    try:
        from navig.approval.policies import parse_security_level

        parsed = parse_security_level(override)
    except Exception:  # noqa: BLE001
        parsed = None
    return parsed or _configured_level()


def posture(override: str | None = None) -> str:
    """The operator-facing posture name that *override* (or the config) means."""
    return _LEVEL_TO_POSTURE.get(level(override), _LEVEL_TO_POSTURE["medium"])


def reflex_profile(override: str | None = None) -> str:
    """The reflex routing profile implied by the level."""
    return _PROFILE_FOR_LEVEL.get(level(override), _PROFILE_FOR_LEVEL["medium"])


def approval_policy(override: str | None = None) -> str:
    """The chat agent's default ``ApprovalPolicy`` value implied by the level."""
    return _POLICY_FOR_LEVEL.get(level(override), _POLICY_FOR_LEVEL["medium"])


def describe(override: str | None = None) -> dict[str, Any]:
    """Everything one posture switches, for status surfaces and tests."""
    active = level(override)
    reading = level()
    summary: dict[str, str] = {}
    try:
        from navig.approval.policies import LEVEL_SUMMARY

        summary = dict(LEVEL_SUMMARY)
    except Exception:  # noqa: BLE001
        pass
    bypass: bool | None = None
    try:
        from navig.tools.approval import _auto_approve_read_only

        bypass = _auto_approve_read_only()
    except Exception:  # noqa: BLE001
        pass
    return {
        "posture": posture(active),
        "level": active,
        "configured_level": reading,
        "reflex_profile": reflex_profile(active),
        "approval_policy": approval_policy(active),
        "read_only_bypass": bypass,
        "summary": summary.get(active, ""),
    }

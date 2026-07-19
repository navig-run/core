"""Process-level guard that HARD-BLOCKS outward/irreversible browser actions.

``navig do`` turns this on when the user did NOT pass ``--yes``. It lets the AI
agent prepare (navigate, read, fill, compose a draft) but blocks it from finally
**sending, submitting, publishing, paying, or deleting** — a real tool-layer
interlock, not just a prompt the model can ignore.

Levels: ``off`` (no gate) · ``safe`` (block outward actions) · ``dry_run``
(block outward actions; the caller also narrates-only). The guard is a module
global (with a lock) so the agent's separate browser-loop thread sees it.
"""

from __future__ import annotations

import re
import threading

__all__ = ["set_level", "get_level", "is_active", "is_outward_label", "blocked_reason"]

_lock = threading.Lock()
_level = "off"  # off | safe | dry_run

# Control/label text meaning an outward, hard-to-undo action. Deliberately
# word-bounded to avoid catching benign labels (e.g. "Send feedback" still
# matches "send" — intentionally conservative: better to block a rare benign
# click than to let a real send through).
_OUTWARD = re.compile(
    r"\b(send|submit|publish|post|tweet|share|pay|buy|order|checkout|"
    r"place\s+order|delete|remove|confirm|book|subscribe|transfer|withdraw)\b",
    re.IGNORECASE,
)


def set_level(level: str) -> None:
    global _level
    with _lock:
        _level = level if level in ("off", "safe", "dry_run") else "off"


def get_level() -> str:
    with _lock:
        return _level


def is_active() -> bool:
    return get_level() != "off"


def is_outward_label(text: str) -> bool:
    return bool(text and _OUTWARD.search(text))


def blocked_reason(label: str) -> str | None:
    """Return a refusal message if *label* is an outward action while gated, else None."""
    if is_active() and is_outward_label(label):
        clean = " ".join((label or "").split())[:60]
        return (
            f"BLOCKED ({get_level()} mode): '{clean}' looks like an outward action "
            f"(send/submit/publish/pay/delete). The user must re-run with --yes to allow it. "
            f"Do not attempt to bypass this; report what you prepared instead."
        )
    return None

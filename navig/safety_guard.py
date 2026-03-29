"""
Safety Guard — Destructive action confirmation for uncensored models.

When an uncensored model generates potentially destructive commands,
require explicit human confirmation before execution.

Usage:
    from navig.safety_guard import require_human_confirmation_if_destructive

    if not require_human_confirmation_if_destructive(is_uncensored=True, planned_action="rm -rf /"):
        print("User denied — aborting.")
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("navig.safety_guard")

# ─────────────────────────────────────────────────────────────
# Destructive Patterns
# ─────────────────────────────────────────────────────────────

DESTRUCTIVE_PATTERNS = re.compile(
    r"(?:"
    r"rm\s+(-[rfRF]+\s+|--recursive|--force)"
    r"|"
    r"rmdir\s+"
    r"|"
    r"DROP\s+(TABLE|DATABASE|SCHEMA|INDEX)"
    r"|"
    r"TRUNCATE\s+(TABLE)?"
    r"|"
    r"DELETE\s+FROM\s+\w+\s*(;|$|WHERE\s+1)"
    r"|"
    r"systemctl\s+(stop|disable|mask)\s+"
    r"|"
    r"service\s+\w+\s+stop"
    r"|"
    r"kill\s+-9"
    r"|"
    r"killall\s+"
    r"|"
    r"pkill\s+-9"
    r"|"
    r"format\s+[A-Za-z]:"
    r"|"
    r"mkfs\."
    r"|"
    r"dd\s+if="
    r"|"
    r"fdisk\s+"
    r"|"
    r"parted\s+"
    r"|"
    r"wipefs\s+"
    r"|"
    r"shred\s+"
    r"|"
    r"chmod\s+-R\s+000"
    r"|"
    r"chown\s+-R\s+root"
    r"|"
    r"iptables\s+-F"
    r"|"
    r"ufw\s+disable"
    r"|"
    r"reboot\b"
    r"|"
    r"shutdown\b"
    r"|"
    r"poweroff\b"
    r"|"
    r"init\s+0"
    r"|"
    r"halt\b"
    r"|"
    r":(){ :\|:& };:"
    r"|"
    r">\s*/dev/sd"
    r"|"
    r"curl\s+.*\|\s*(?:bash|sh)"
    r"|"
    r"wget\s+.*\|\s*(?:bash|sh)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Additional patterns for mild concern (logged but not blocked)
RISKY_PATTERNS = re.compile(
    r"(?:"
    r"sudo\s+"
    r"|"
    r"apt\s+(remove|purge)"
    r"|"
    r"pip\s+uninstall"
    r"|"
    r"npm\s+uninstall"
    r"|"
    r"docker\s+(rm|rmi|prune)"
    r"|"
    r"git\s+(reset\s+--hard|clean\s+-fd|push\s+.*--force)"
    r")",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────
# Guard Function
# ─────────────────────────────────────────────────────────────


def require_human_confirmation_if_destructive(
    is_uncensored: bool,
    planned_action: str,
    auto_approve: bool = False,
    context: str | None = None,
) -> bool:
    """
    Check if a planned action is destructive and require confirmation.

    This guard is called from the execution layer, NOT from the router.

    Args:
        is_uncensored: Whether the action came from an uncensored model.
        planned_action: The command/action string to evaluate.
        auto_approve: Skip confirmation (for non-interactive contexts).
        context: Optional context for the log.

    Returns:
        True if the action is safe to proceed, False if user denied.
    """
    # Check for destructive patterns for uncensored-model gating.
    match = DESTRUCTIVE_PATTERNS.search(planned_action)

    # Censored mode: this guard is intentionally bypassed for backward
    # compatibility. Risk-based confirmation is handled by should_confirm().
    if not is_uncensored:
        logger.debug("Guard bypass (censored mode): %s", _truncate(planned_action))
        return True

    if not match:
        # Uncensored model, not destructive — log risky patterns
        risky = RISKY_PATTERNS.search(planned_action)
        if risky:
            logger.info(
                "Guard NOTICE (risky, uncensored): pattern='%s' action='%s'",
                risky.group(),
                _truncate(planned_action),
            )
        else:
            logger.debug("Guard pass (no destructive pattern): %s", _truncate(planned_action))
        return True

    # Destructive action detected — always escalate regardless of model type
    logger.warning(
        "Guard TRIGGERED: destructive pattern='%s' action='%s' context='%s' uncensored=%s",
        match.group(),
        _truncate(planned_action),
        context or "none",
        is_uncensored,
    )

    if auto_approve:
        logger.warning("Guard auto-approved (non-interactive mode)")
        return True

    # Require human confirmation
    return _prompt_confirmation(planned_action, match.group())


def is_destructive(action: str) -> bool:
    """Check if an action matches destructive patterns (without prompting)."""
    return bool(DESTRUCTIVE_PATTERNS.search(action))


def is_risky(action: str) -> bool:
    """Check if an action matches risky patterns."""
    return bool(RISKY_PATTERNS.search(action)) or is_destructive(action)


def classify_action_risk(action: str) -> str:
    """
    Classify an action's risk level.

    Returns: 'safe' | 'risky' | 'destructive'
    """
    if DESTRUCTIVE_PATTERNS.search(action):
        return "destructive"
    if RISKY_PATTERNS.search(action):
        return "risky"
    return "safe"


# ─────────────────────────────────────────────────────────────
# Confirmation Prompt
# ─────────────────────────────────────────────────────────────


def _prompt_confirmation(action: str, pattern: str) -> bool:
    """Prompt the user for YES confirmation on destructive actions."""
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()

        console.print(
            Panel(
                f"[bold red]⚠️  DESTRUCTIVE ACTION DETECTED[/bold red]\n\n"
                f"[bold]Pattern:[/bold] {pattern}\n"
                f"[bold]Action:[/bold]\n{_truncate(action, 200)}\n\n"
                f"[yellow]This action was generated by an uncensored model.\n"
                f"Type [bold]YES[/bold] to confirm execution.[/yellow]",
                border_style="red",
                title="Safety Guard",
            )
        )
    except ImportError:
        print("\n⚠️  DESTRUCTIVE ACTION DETECTED")
        print(f"Pattern: {pattern}")
        print(f"Action: {_truncate(action, 200)}")
        print("\nThis action was from an uncensored model.")
        print("Type YES to confirm execution.\n")

    try:
        response = input("Confirm (YES/no): ").strip()
        approved = response == "YES"
        logger.info(
            "Guard confirmation: user=%s action='%s'",
            "APPROVED" if approved else "DENIED",
            _truncate(action),
        )
        return approved
    except (EOFError, KeyboardInterrupt, OSError):
        logger.info("Guard confirmation: user=CANCELLED (interrupt)")
        return False


def _truncate(s: str, maxlen: int = 100) -> str:
    """Truncate a string for logging."""
    if len(s) <= maxlen:
        return s
    return s[:maxlen] + "..."


# ─────────────────────────────────────────────────────────────
# Config-aware decision helper
# ─────────────────────────────────────────────────────────────


def should_confirm(
    action: str,
    confirmation_level: str = "standard",
    auto_confirm_safe: bool = False,
) -> bool:
    """
    Determine whether a given action needs human confirmation based on
    the ``ConfirmationLevel`` from ``ExecutionConfig``.

    Combines risk classification with config-driven confirmation policy.

    Args:
        action: The command/action string.
        confirmation_level: One of ``critical``, ``standard``, ``verbose``.
        auto_confirm_safe: If True, skip confirmation for safe actions.

    Returns:
        True if human confirmation should be requested.

    Semantics:
        - **critical**: Only confirm destructive actions.
        - **standard**: Confirm destructive + risky actions.
        - **verbose**: Confirm everything (including safe).
    """
    risk = classify_action_risk(action)

    if risk == "safe":
        if auto_confirm_safe or confirmation_level in ("critical", "standard"):
            return False
        # verbose → confirm even safe actions
        return confirmation_level == "verbose"

    if risk == "risky":
        if confirmation_level == "critical":
            return False
        # standard + verbose → confirm risky
        return True

    # risk == "destructive" → always confirm
    return True

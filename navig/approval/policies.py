"""Approval policies and command classification."""

import fnmatch
from dataclasses import dataclass, field
from enum import Enum

from navig.core.coerce import coerce_bool, coerce_int


class ApprovalLevel(Enum):
    """Command approval levels."""

    SAFE = "safe"  # Auto-approve
    CONFIRM = "confirm"  # Ask user, timeout = approve
    DANGEROUS = "dangerous"  # Ask user, timeout = deny
    NEVER = "never"  # Always deny


class ApprovalStatus(Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


# Default patterns for command classification
DEFAULT_SAFE_PATTERNS = [
    "host list",
    "app list",
    "db list",
    "file list",
    "wiki *",
    "help *",
    "status",
    "gateway status",
    "heartbeat status",
    "cron list",
]

DEFAULT_CONFIRM_PATTERNS = [
    "file remove *",
    "host remove *",
    "app remove *",
    "db restore *",
    "backup *",
    "ssh exec *",
]

DEFAULT_DANGEROUS_PATTERNS = [
    "run rm *",
    "run shutdown*",
    "run reboot*",
    "db drop *",
    "* rm -rf *",
]

DEFAULT_NEVER_PATTERNS = [
    "run rm -rf /",
    "run rm -rf /*",
    "DROP DATABASE *",
]


# Actions that auto-approve will accept when auto_evolve_enabled is True.
# Mirrors the VS Code navig-bridge WHITELIST constant — keep in sync.
DEFAULT_AUTO_EVOLVE_WHITELIST: list[str] = [
    "fix",
    "skill.patch",
    "workflow.update",
    "run",
    "file.write",
]


@dataclass
class ApprovalPolicy:
    """Policy configuration for approvals."""

    enabled: bool = True
    # How long a human has to answer. 120 s was the old value and it predates
    # approvals ever REACHING a human: nothing sent them to Telegram, so the
    # window only ever governed how fast the request auto-denied itself
    # (55 expiries on one install, every one `channel=mission`, all denied).
    #
    # Now that they arrive as a Telegram prompt, this is the binding constraint.
    # Measured on that install the moment delivery started working: the operator's
    # answer landed 7 MINUTES after the ask — "answered too late (already
    # EXPIRED) — the inline decision was NOT applied". A notification has to be
    # noticed, opened and read before it can be tapped; two minutes is a terminal
    # timeout, not a human one.
    #
    # Waiting longer is the safe direction: `default_action` is "deny" and
    # DANGEROUS always denies regardless, so a longer window only delays an
    # auto-deny — it never widens what can be approved without an answer.
    timeout_seconds: int = 900
    default_action: str = "deny"

    safe_patterns: list[str] = field(default_factory=lambda: DEFAULT_SAFE_PATTERNS.copy())
    confirm_patterns: list[str] = field(default_factory=lambda: DEFAULT_CONFIRM_PATTERNS.copy())
    dangerous_patterns: list[str] = field(default_factory=lambda: DEFAULT_DANGEROUS_PATTERNS.copy())
    never_patterns: list[str] = field(default_factory=lambda: DEFAULT_NEVER_PATTERNS.copy())

    # Per-channel settings
    auto_approve_users: list[str] = field(default_factory=list)

    # ── Auto-Evolve / Auto-Approve ───────────────────────────────────────────
    # When True, CONFIRM-level commands in auto_evolve_whitelist are approved
    # without user interaction.  DANGEROUS and NEVER are never auto-approved.
    # Gate: audit_log must be live (is_auto_evolve_allowed() checks this).
    auto_evolve_enabled: bool = False
    auto_evolve_whitelist: list[str] = field(
        default_factory=lambda: DEFAULT_AUTO_EVOLVE_WHITELIST.copy()
    )

    @classmethod
    def default(cls) -> "ApprovalPolicy":
        """Create default policy with standard patterns."""
        return cls()

    @property
    def patterns(self) -> dict:
        """Get patterns as dict for display."""
        return {
            "safe": self.safe_patterns,
            "confirm": self.confirm_patterns,
            "dangerous": self.dangerous_patterns,
            "never": self.never_patterns,
        }

    def classify(self, command: str) -> ApprovalLevel:
        """Alias for classify_command for compatibility."""
        return self.classify_command(command)

    @classmethod
    def from_config(cls, config: dict) -> "ApprovalPolicy":
        """Load policy from config dict."""
        approval_cfg = config.get("approval", {})
        levels = approval_cfg.get("levels", {})
        channels = approval_cfg.get("channels", {})
        auto_evolve_cfg = approval_cfg.get("auto_evolve", {})

        return cls(
            enabled=coerce_bool(approval_cfg.get("enabled", True), default=True),
            # coerce_int, not a bare get: `navig config set` stores raw STRINGS, and
            # an int used raw does not misbehave quietly — `timedelta(seconds="600")`
            # and `asyncio.wait_for(timeout="600")` both raise TypeError. Setting the
            # one knob that lengthens this window used to disable approvals outright.
            timeout_seconds=coerce_int(
                approval_cfg.get("timeout_seconds", 900), 900, minimum=1
            ),
            default_action=approval_cfg.get("default_action", "deny"),
            safe_patterns=levels.get("safe", DEFAULT_SAFE_PATTERNS.copy()),
            confirm_patterns=levels.get("confirm", DEFAULT_CONFIRM_PATTERNS.copy()),
            dangerous_patterns=levels.get("dangerous", DEFAULT_DANGEROUS_PATTERNS.copy()),
            never_patterns=levels.get("never", DEFAULT_NEVER_PATTERNS.copy()),
            auto_approve_users=channels.get("auto_approve_users", []),
            auto_evolve_enabled=coerce_bool(auto_evolve_cfg.get("enabled", False), default=False),
            auto_evolve_whitelist=auto_evolve_cfg.get(
                "whitelist", DEFAULT_AUTO_EVOLVE_WHITELIST.copy()
            ),
        )

    def is_auto_evolve_allowed(self, command: str, audit_log_live: bool) -> bool:
        """
        Return True if auto-evolve should approve *command* without prompting.

        Rules:
        1. auto_evolve_enabled must be True.
        2. audit_log must be live — silent approvals without a trace are forbidden.
        3. Command level must not be DANGEROUS or NEVER.
        4. command must match at least one pattern in auto_evolve_whitelist.
        """
        if not self.auto_evolve_enabled:
            return False
        if not audit_log_live:
            return False
        level = self.classify_command(command)
        if level in (ApprovalLevel.DANGEROUS, ApprovalLevel.NEVER):
            return False
        cmd_lower = command.lower().strip()
        return any(fnmatch.fnmatch(cmd_lower, pat.lower()) for pat in self.auto_evolve_whitelist)

    def classify_command(self, command: str) -> ApprovalLevel:
        """Classify a command into an approval level."""
        command_lower = command.lower().strip()

        # Check patterns in order of severity
        for pattern in self.never_patterns:
            if fnmatch.fnmatch(command_lower, pattern.lower()):
                return ApprovalLevel.NEVER

        for pattern in self.dangerous_patterns:
            if fnmatch.fnmatch(command_lower, pattern.lower()):
                return ApprovalLevel.DANGEROUS

        for pattern in self.confirm_patterns:
            if fnmatch.fnmatch(command_lower, pattern.lower()):
                return ApprovalLevel.CONFIRM

        for pattern in self.safe_patterns:
            if fnmatch.fnmatch(command_lower, pattern.lower()):
                return ApprovalLevel.SAFE

        # Default to CONFIRM for unlisted commands
        return ApprovalLevel.CONFIRM

    def is_user_auto_approved(self, user_id: str) -> bool:
        """Check if user has auto-approve privileges."""
        return user_id in self.auto_approve_users

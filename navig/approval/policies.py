"""Approval policies and command classification."""

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class ApprovalLevel(Enum):
    """Command approval levels."""
    SAFE = "safe"           # Auto-approve
    CONFIRM = "confirm"     # Ask user, timeout = approve
    DANGEROUS = "dangerous" # Ask user, timeout = deny
    NEVER = "never"         # Always deny


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
DEFAULT_AUTO_EVOLVE_WHITELIST: List[str] = [
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
    timeout_seconds: int = 120
    default_action: str = "deny"

    safe_patterns: List[str] = field(default_factory=lambda: DEFAULT_SAFE_PATTERNS.copy())
    confirm_patterns: List[str] = field(default_factory=lambda: DEFAULT_CONFIRM_PATTERNS.copy())
    dangerous_patterns: List[str] = field(default_factory=lambda: DEFAULT_DANGEROUS_PATTERNS.copy())
    never_patterns: List[str] = field(default_factory=lambda: DEFAULT_NEVER_PATTERNS.copy())

    # Per-channel settings
    auto_approve_users: List[str] = field(default_factory=list)

    # ── Auto-Evolve / Auto-Approve ───────────────────────────────────────────
    # When True, CONFIRM-level commands in auto_evolve_whitelist are approved
    # without user interaction.  DANGEROUS and NEVER are never auto-approved.
    # Gate: audit_log must be live (is_auto_evolve_allowed() checks this).
    auto_evolve_enabled: bool = False
    auto_evolve_whitelist: List[str] = field(
        default_factory=lambda: DEFAULT_AUTO_EVOLVE_WHITELIST.copy()
    )

    @classmethod
    def default(cls) -> 'ApprovalPolicy':
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
    def from_config(cls, config: dict) -> 'ApprovalPolicy':
        """Load policy from config dict."""
        approval_cfg = config.get('approval', {})
        levels = approval_cfg.get('levels', {})
        channels = approval_cfg.get('channels', {})
        auto_evolve_cfg = approval_cfg.get('auto_evolve', {})

        return cls(
            enabled=approval_cfg.get('enabled', True),
            timeout_seconds=approval_cfg.get('timeout_seconds', 120),
            default_action=approval_cfg.get('default_action', 'deny'),
            safe_patterns=levels.get('safe', DEFAULT_SAFE_PATTERNS.copy()),
            confirm_patterns=levels.get('confirm', DEFAULT_CONFIRM_PATTERNS.copy()),
            dangerous_patterns=levels.get('dangerous', DEFAULT_DANGEROUS_PATTERNS.copy()),
            never_patterns=levels.get('never', DEFAULT_NEVER_PATTERNS.copy()),
            auto_approve_users=channels.get('auto_approve_users', []),
            auto_evolve_enabled=auto_evolve_cfg.get('enabled', False),
            auto_evolve_whitelist=auto_evolve_cfg.get(
                'whitelist', DEFAULT_AUTO_EVOLVE_WHITELIST.copy()
            ),
        )

    def is_auto_evolve_allowed(
        self, command: str, audit_log_live: bool
    ) -> bool:
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
        return any(
            fnmatch.fnmatch(cmd_lower, pat.lower())
            for pat in self.auto_evolve_whitelist
        )

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

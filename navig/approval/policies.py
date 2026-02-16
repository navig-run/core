"""Approval policies and command classification."""

from enum import Enum
from dataclasses import dataclass, field
from typing import List
import fnmatch


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
        
        return cls(
            enabled=approval_cfg.get('enabled', True),
            timeout_seconds=approval_cfg.get('timeout_seconds', 120),
            default_action=approval_cfg.get('default_action', 'deny'),
            safe_patterns=levels.get('safe', DEFAULT_SAFE_PATTERNS.copy()),
            confirm_patterns=levels.get('confirm', DEFAULT_CONFIRM_PATTERNS.copy()),
            dangerous_patterns=levels.get('dangerous', DEFAULT_DANGEROUS_PATTERNS.copy()),
            never_patterns=levels.get('never', DEFAULT_NEVER_PATTERNS.copy()),
            auto_approve_users=channels.get('auto_approve_users', []),
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

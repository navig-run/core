"""Approval policies and command classification."""

import fnmatch
import logging
from dataclasses import dataclass, field
from enum import Enum

from navig.core.coerce import coerce_bool, coerce_int

logger = logging.getLogger(__name__)


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

# ── Security levels — the router-firewall knob (owner ask 2026-10-15) ────────
#
# One knob, three levels, like the "Security Level: High / Medium / Low" switch
# on a consumer router firewall — instead of the seven partially-overlapping
# approval keys an operator had to reason about alone:
#
#   high    nothing is trusted: even a proven read-only shell command pages,
#           and ANY approval timeout denies (fail-closed, regardless of
#           default_action)
#   medium  the designed default: proven reads run unprompted (audited),
#           writes page, dangerous always denies on timeout
#   low     the terminal is exempt: bash_exec/run_command/navig_run never
#           page (reads AND writes, still audited); every OTHER gated tool
#           and gateway action (db_query, cdp_eval, connector writes,
#           missions, shutdown) still pages
#
# `approval.enabled: false` remains the master kill-switch OUTSIDE these
# levels (the "unplug it" position). Explicit granular keys still win over
# the level — security_level sets the DERIVED defaults, not an override.

#: Canonical level values (what `approval.security_level` may hold).
SECURITY_LEVELS: tuple[str, ...] = ("high", "medium", "low")

#: Spellings people reach for from the router analogy or from prose —
#: accepted and normalized, never stored.
_SECURITY_LEVEL_ALIASES: dict[str, str] = {
    "strict": "high",
    "balanced": "medium",
    "permissive": "low",
}

#: The default when the key is absent — the behaviour the code shipped with.
DEFAULT_SECURITY_LEVEL = "medium"

#: One line per level — what an operator reads before choosing. The single source
#: for `navig approve status`, the gateway's `/approval/policy` and the docs, so the
#: three can't describe different levels.
LEVEL_SUMMARY: dict[str, str] = {
    "high": "nothing trusted — even proven reads ask; no answer = deny",
    "medium": "balanced — proven reads run unasked, writes ask",
    "low": "terminal free — shell never asks; db / cdp / connectors / missions still do",
}

#: `low` exempts exactly the shell family the chat gate treats as one
#: (`navig.tools.approval._SHELL_TOOLS`); parity is pinned by
#: ``tests/approval/test_security_levels.py``.
SHELL_TOOL_NAMES: frozenset[str] = frozenset({"bash_exec", "run_command", "navig_run"})
SHELL_TOOL_PATTERNS: tuple[str, ...] = tuple(
    f"tool {name}*" for name in sorted(SHELL_TOOL_NAMES)
)


def parse_security_level(raw: object) -> str | None:
    """The canonical level for *raw*, or ``None`` when it is not one — STRICT.

    For input surfaces (`navig approve level <x>`): a typo must be refused, not
    quietly stored as some other level. :func:`coerce_security_level` is the
    lenient twin for values already on disk, where refusing would mean refusing
    to boot.
    """
    if raw is None:
        return None
    text = _SECURITY_LEVEL_ALIASES.get(str(raw).strip().lower(), str(raw).strip().lower())
    return text if text in SECURITY_LEVELS else None


def coerce_security_level(raw: object) -> str:
    """Normalize a configured level to a canonical value. Never raises.

    A typo must not kill the gateway at boot (`from_config` runs there), and
    it must not fail open either: an unknown value lands on ``medium`` — the
    documented default whose only privilege is running PROVEN reads, always
    audited — and says so in the log. Failing a typo toward ``high`` would
    trade a visible misconfiguration for surprise prompt-spam; failing toward
    ``low`` would hand a typo the shell. ``medium`` is the honest middle.
    """
    if raw is None or not str(raw).strip():
        return DEFAULT_SECURITY_LEVEL
    parsed = parse_security_level(raw)
    if parsed is not None:
        return parsed
    logger.warning(
        "approval.security_level=%r is not one of %s — using %r",
        raw,
        "/".join(SECURITY_LEVELS),
        DEFAULT_SECURITY_LEVEL,
    )
    return DEFAULT_SECURITY_LEVEL


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
    #: The router-firewall knob — one of SECURITY_LEVELS. Sets the derived
    #: defaults (safe patterns under `low`, timeout posture under `high`);
    #: explicit granular keys still win over it (see from_config).
    security_level: str = DEFAULT_SECURITY_LEVEL
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

    def posture(self, *, read_bypass: bool) -> dict:
        """The operator-facing summary of what this policy does — JSON-safe.

        *read_bypass* is passed in rather than computed here: it is read LIVE from
        config on every chat tool call (`tools.approval._auto_approve_read_only`),
        while the rest of this policy was frozen when the gateway booted — the two
        have different lifetimes, and the caller knows which process it is in.
        """
        return {
            "enabled": self.enabled,
            "security_level": self.security_level,
            "summary": LEVEL_SUMMARY.get(self.security_level, ""),
            "read_bypass": bool(read_bypass),
            "shell_exempt": self.security_level == "low",
            "fail_closed_timeout": self.fail_closed_timeout,
            "timeout_seconds": self.timeout_seconds,
            "default_action": self.default_action,
        }

    @property
    def fail_closed_timeout(self) -> bool:
        """`high`: ANY approval timeout denies, regardless of default_action.

        The router-High position: an unanswered question is a blocked packet.
        Under `medium`/`low` the timeout follows `default_action` (dangerous
        always denies regardless).
        """
        return self.security_level == "high"

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
        """Load policy from config dict.

        `approval.security_level` (high/medium/low) derives the defaults —
        `low` appends the shell-tool safe patterns; explicit
        `approval.levels.safe` entries are kept, never replaced, and the
        caller's list is copied before anything is appended (a config dict
        must never be mutated by a load).
        """
        approval_cfg = config.get("approval", {})
        levels = approval_cfg.get("levels", {})
        channels = approval_cfg.get("channels", {})
        auto_evolve_cfg = approval_cfg.get("auto_evolve", {})

        security_level = coerce_security_level(approval_cfg.get("security_level"))
        safe_patterns = list(levels.get("safe", DEFAULT_SAFE_PATTERNS.copy()))
        if security_level == "low":
            safe_patterns.extend(
                p for p in SHELL_TOOL_PATTERNS if p not in safe_patterns
            )

        return cls(
            enabled=coerce_bool(approval_cfg.get("enabled", True), default=True),
            security_level=security_level,
            # coerce_int, not a bare get: `navig config set` stores raw STRINGS, and
            # an int used raw does not misbehave quietly — `timedelta(seconds="600")`
            # and `asyncio.wait_for(timeout="600")` both raise TypeError. Setting the
            # one knob that lengthens this window used to disable approvals outright.
            timeout_seconds=coerce_int(
                approval_cfg.get("timeout_seconds", 900), 900, minimum=1
            ),
            default_action=approval_cfg.get("default_action", "deny"),
            safe_patterns=safe_patterns,
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

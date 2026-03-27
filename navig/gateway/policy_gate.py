"""
NAVIG Gateway — Policy Gate

Enforces per-action authorization rules before command execution:
  ALLOW            → proceed immediately
  REQUIRE_APPROVAL → queue for human review (integrates with approval system)
  DENY             → reject outright

Policy is loaded from gateway.policy in navig config. Example config:

  gateway:
    policy:
      default: allow
      rules:
        - pattern: "system.restart"   action: require_approval
        - pattern: "system.shutdown"  action: deny
        - pattern: "db.*"             action: require_approval
        - pattern: "run.*"            action: allow
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class PolicyRule:
    """A single pattern-based policy rule."""

    pattern: str  # fnmatch pattern, e.g. "db.*" or "run.restart"
    decision: PolicyDecision


@dataclass
class PolicyConfig:
    """Full policy configuration."""

    default: PolicyDecision = PolicyDecision.ALLOW
    rules: list[PolicyRule] = field(default_factory=list)


@dataclass
class PolicyResult:
    """Decision returned by PolicyGate.check()."""

    decision: PolicyDecision
    action: str
    matched_rule: str | None = None  # pattern that matched, or None for default

    @property
    def is_allowed(self) -> bool:
        return self.decision == PolicyDecision.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision == PolicyDecision.REQUIRE_APPROVAL

    @property
    def is_denied(self) -> bool:
        return self.decision == PolicyDecision.DENY


class PolicyGate:
    """
    Central policy enforcer for the NAVIG gateway.

    Usage::

        gate = PolicyGate.from_config(cfg)
        result = gate.check("db.query", actor="telegram:user123")
        if result.is_denied:
            raise PermissionError(...)
        elif result.needs_approval:
            approval_manager.request(...)

    """

    # Built-in hardened rules that can never be overridden by user config
    _HARD_DENY: list[str] = [
        "system.delete_all",
        "*.drop_all",
    ]

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self._config = config or PolicyConfig()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, raw_gateway_cfg: dict | None = None) -> PolicyGate:
        """Build a PolicyGate from the ``gateway`` section of navig config."""
        if not raw_gateway_cfg:
            return cls()

        policy_cfg = raw_gateway_cfg.get("policy", {})
        if not policy_cfg:
            return cls()

        default_str = policy_cfg.get("default", "allow").lower()
        try:
            default = PolicyDecision(default_str)
        except ValueError:
            logger.warning("Unknown policy default '%s', using 'allow'", default_str)
            default = PolicyDecision.ALLOW

        rules: list[PolicyRule] = []
        for raw_rule in policy_cfg.get("rules", []):
            pattern = raw_rule.get("pattern", "").strip()
            act_str = raw_rule.get("action", "allow").lower()
            if not pattern:
                continue
            try:
                decision = PolicyDecision(act_str)
            except ValueError:
                logger.warning(
                    "Unknown rule action '%s' for pattern '%s', skipping",
                    act_str,
                    pattern,
                )
                continue
            rules.append(PolicyRule(pattern=pattern, decision=decision))

        config = PolicyConfig(default=default, rules=rules)
        return cls(config)

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(self, action: str, actor: str | None = None) -> PolicyResult:
        """
        Evaluate policy for the given action slug.

        :param action: Dot-notation action identifier, e.g. ``"db.query"``
        :param actor:  Optional actor identifier for logging (e.g. ``"telegram:user123"``)
        :returns: PolicyResult with decision and matched rule.
        """
        # Hard-deny patterns — immutable
        for hard_pattern in self._HARD_DENY:
            if fnmatch.fnmatch(action, hard_pattern):
                logger.warning(
                    "PolicyGate HARD_DENY: action=%s actor=%s pattern=%s",
                    action,
                    actor,
                    hard_pattern,
                )
                return PolicyResult(
                    decision=PolicyDecision.DENY,
                    action=action,
                    matched_rule=f"hard:{hard_pattern}",
                )

        # User-configured rules (first match wins)
        for rule in self._config.rules:
            if fnmatch.fnmatch(action, rule.pattern):
                logger.debug(
                    "PolicyGate %s: action=%s actor=%s matched_rule=%s",
                    rule.decision.value,
                    action,
                    actor,
                    rule.pattern,
                )
                return PolicyResult(
                    decision=rule.decision,
                    action=action,
                    matched_rule=rule.pattern,
                )

        # Default
        logger.debug(
            "PolicyGate %s (default): action=%s actor=%s",
            self._config.default.value,
            action,
            actor,
        )
        return PolicyResult(
            decision=self._config.default,
            action=action,
            matched_rule=None,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Return a human-readable policy summary."""
        return {
            "default": self._config.default.value,
            "rules": [
                {"pattern": r.pattern, "action": r.decision.value} for r in self._config.rules
            ],
            "hard_deny_patterns": self._HARD_DENY,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PolicyGate(default={self._config.default.value!r}, rules={len(self._config.rules)})"
        )

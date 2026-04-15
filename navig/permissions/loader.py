"""
Permission rule loader — reads rule definitions from settings YAML files and
evaluates them against tool calls.

File locations (evaluated in order; project rules appended after global):

    ~/.navig/settings.yaml      — global rules
    .navig/settings.yaml        — project-local rules (override/extend)

YAML schema::

    permissions:
      enabled: true          # omit or set false to disable all rules
      rules:
        - allow: "Bash(git commit:*)"
        - allow: "pytest:*"
        - deny: "Bash(rm -rf /*)"
        - deny: "Bash(curl *|bash:*)"

Shadow detection: if a broader ``allow: "*"`` rule is followed by a narrower
``deny: "Bash(...)"`` rule, the deny rule can never match because allow already
permits everything.  The loader emits a ``logger.warning`` in this case.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .rule_parser import parse_rule_spec
from .rules import PermissionDecision, PermissionRule, RuleAction

logger = logging.getLogger("navig.permissions.loader")

_SETTINGS_FILENAME = "settings.yaml"


class PermissionRuleLoader:
    """Loads and evaluates structured permission rules."""

    def __init__(
        self,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        self._global_dir = global_dir or Path.home() / ".navig"
        self._project_dir = project_dir or Path(".navig")
        self._rules: list[PermissionRule] = []
        self._enabled: bool = True

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self) -> None:
        """(Re)load rules from both settings files."""
        self._rules = []

        for path, source in (
            (self._global_dir / _SETTINGS_FILENAME, "global"),
            (self._project_dir / _SETTINGS_FILENAME, "project"),
        ):
            if path.exists():
                self._load_file(path, source)

        self._detect_shadows()
        logger.debug(
            "permissions.loader: loaded %d rules (enabled=%s)",
            len(self._rules),
            self._enabled,
        )

    def _load_file(self, path: Path, source: str) -> None:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            return

        try:
            raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("permissions.loader: failed to read %s: %s", path, exc)
            return

        perms = raw.get("permissions", {})
        if not isinstance(perms, dict):
            return

        enabled = perms.get("enabled", True)
        if not enabled:
            self._enabled = False
            return

        for entry in perms.get("rules", []):
            if not isinstance(entry, dict):
                continue
            for action_str in ("allow", "deny"):
                if action_str in entry:
                    rule = parse_rule_spec(action_str, str(entry[action_str]), source=source)
                    if rule is not None:
                        self._rules.append(rule)
                    break

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------

    def evaluate(self, tool: str, input_text: str) -> PermissionDecision:
        """Evaluate all rules against (tool, input_text) — first match wins.

        The evaluation order follows the file load order (global first,
        project second) and within each file the definitions order.
        The first matching rule's action is authoritative.

        Returns a ``PermissionDecision`` with ``denied=False`` when:
        - rules are disabled
        - no rule matches (default: allow)
        - the first matching rule is ``allow``
        """
        if not self._enabled:
            return PermissionDecision(denied=False)

        for rule in self._rules:
            if rule.matches(tool, input_text):
                if rule.action == RuleAction.DENY:
                    reason = (
                        f"Blocked by permission rule [{rule.source}]: "
                        f"{rule.action.value} {rule.tool}({rule.pattern})"
                    )
                    logger.info(
                        "permissions: DENY tool=%s input=%s rule=%s(%s)",
                        tool,
                        input_text[:120],
                        rule.tool,
                        rule.pattern,
                    )
                    return PermissionDecision(denied=True, reason=reason, matching_rule=rule)
                # Explicit allow — short-circuit
                logger.debug(
                    "permissions: ALLOW tool=%s pattern=%s", tool, rule.pattern
                )
                return PermissionDecision(denied=False, matching_rule=rule)

        # No rule matched — default allow
        return PermissionDecision(denied=False)

    # ------------------------------------------------------------------
    # Shadow detection
    # ------------------------------------------------------------------

    def _detect_shadows(self) -> None:
        """Warn when a broader rule makes a narrower rule unreachable."""
        for i, earlier in enumerate(self._rules):
            if earlier.tool == "*" and earlier.pattern == "*":
                for later in self._rules[i + 1:]:
                    if later.action != earlier.action:
                        logger.warning(
                            "permissions.loader: rule '%s %s(%s)' is shadowed by "
                            "earlier wildcard rule '%s *(*)'  — it can never match",
                            later.action.value,
                            later.tool,
                            later.pattern,
                            earlier.action.value,
                        )

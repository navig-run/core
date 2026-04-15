"""
navig.permissions — Structured allow/deny rule system for tool calls.

Ported and adapted from Claude Code's TypeScript permission rule subsystem
(``PermissionRule.ts``, ``permissionRuleParser.ts``,
``shadowedRuleDetection.ts``).

This package augments ``navig.safety_guard`` with declarative rules loaded
from ``.navig/settings.yaml`` (project) and ``~/.navig/settings.yaml``
(global).  The existing regex-based guard is preserved unchanged; structured
rules are evaluated *first* and take precedence.

Rule syntax examples (in settings.yaml)::

    permissions:
      rules:
        - allow: "Bash(git commit:*)"
        - allow: "Bash(pytest:*)"
        - deny: "Bash(rm -rf /tmp/build/*)"
        - deny: "BashTool(curl *|bash:*)"

Public API::

    from navig.permissions import check_permission, PermissionDecision

    decision = check_permission(tool="bash", input_text="rm -rf /")
    if decision.denied:
        raise PermissionError(decision.reason)
"""

from __future__ import annotations

from .loader import PermissionRuleLoader
from .rules import PermissionDecision, PermissionRule, RuleAction

__all__ = [
    "PermissionDecision",
    "PermissionRule",
    "PermissionRuleLoader",
    "RuleAction",
    "check_permission",
]

_loader: PermissionRuleLoader | None = None


def check_permission(tool: str, input_text: str) -> PermissionDecision:
    """Evaluate all loaded permission rules against *tool* / *input_text*.

    Returns a ``PermissionDecision`` with ``denied=False`` when the call is
    allowed (or when no matching rule exists).  Never raises.
    """
    global _loader
    if _loader is None:
        _loader = PermissionRuleLoader()
        _loader.load()
    return _loader.evaluate(tool, input_text)


def reload_rules() -> None:
    """Force a reload of all permission rule files (call after config change)."""
    global _loader
    _loader = PermissionRuleLoader()
    _loader.load()

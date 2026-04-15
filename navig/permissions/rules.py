"""
Permission rule dataclasses and matching logic.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum


class RuleAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionRule:
    """One structured permission rule.

    Attributes:
        action:      ``allow`` or ``deny``.
        tool:        Tool name to match (case-insensitive, ``*`` matches all).
        pattern:     Glob pattern matched against the full command/input string.
        source:      Where the rule came from (``global`` / ``project``).
        description: Optional human note.
    """

    action: RuleAction
    tool: str           # e.g. "bash", "BashTool", "*"
    pattern: str        # glob, e.g. "rm -rf /tmp/*"
    source: str = ""
    description: str = ""

    def matches(self, tool: str, input_text: str) -> bool:
        """Return True if this rule applies to *tool* / *input_text*."""
        tool_match = (
            self.tool == "*"
            or self.tool.lower() == tool.lower()
            # Accept "Bash" matching "BashTool" and vice-versa via prefix
            or tool.lower().startswith(self.tool.lower())
            or self.tool.lower().startswith(tool.lower())
        )
        if not tool_match:
            return False
        if not self.pattern or self.pattern == "*":
            return True
        # Try glob first  …
        if fnmatch.fnmatch(input_text, self.pattern):
            return True
        # … then substring (rule pattern is a prefix of the input)
        return bool(re.search(re.escape(self.pattern.rstrip("*")), input_text, re.IGNORECASE))


@dataclass
class PermissionDecision:
    """Result of evaluating the full rule set."""

    denied: bool = False
    reason: str = ""
    matching_rule: PermissionRule | None = None

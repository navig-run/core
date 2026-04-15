"""
Rule spec parser — converts ``"Bash(rm -rf /tmp:*)"`` into a ``PermissionRule``.

Spec syntax::

    <action>: "<ToolName>(<pattern>)"

Or in the short YAML form::

    allow: "Bash(git commit:*)"
    deny:  "BashTool(rm -rf /tmp/*)"

Where ``<pattern>`` is the glob matched against the tool's input string.
The colon separator inside parentheses is optional; it was used by Claude Code
to separate the command from argument patterns but is treated here as part of
the glob string after expanding ``*`` wildcards.

Examples::

    parse_rule_spec("allow", "Bash(git commit:*)")
    → PermissionRule(action=ALLOW, tool="bash", pattern="git commit:*")

    parse_rule_spec("deny", "BashTool(rm -rf /tmp/*)")
    → PermissionRule(action=DENY, tool="bash", pattern="rm -rf /tmp/*")

    parse_rule_spec("allow", "*")
    → PermissionRule(action=ALLOW, tool="*", pattern="*")
"""

from __future__ import annotations

import re

from .rules import PermissionRule, RuleAction

# e.g. "Bash(rm -rf /tmp/*)" → groups: tool="Bash", pattern="rm -rf /tmp/*"
_SPEC_RE = re.compile(r"^(?P<tool>[A-Za-z_*][A-Za-z0-9_*]*)\((?P<pattern>[^)]+)\)$")


def parse_rule_spec(action_str: str, spec: str, source: str = "") -> PermissionRule | None:
    """Parse a rule spec string into a ``PermissionRule``.

    Args:
        action_str: ``"allow"`` or ``"deny"`` (case-insensitive).
        spec:       The rule specification string.
        source:     Origin label (``"global"`` / ``"project"``).

    Returns:
        A ``PermissionRule`` or ``None`` when the spec is unparseable.
    """
    action_str = action_str.strip().lower()
    if action_str not in ("allow", "deny"):
        return None
    action = RuleAction(action_str)

    spec = (spec or "").strip()
    if not spec:
        return None

    # Plain glob with no tool qualifier → apply to all tools
    if spec == "*":
        return PermissionRule(action=action, tool="*", pattern="*", source=source)

    m = _SPEC_RE.match(spec)
    if m:
        raw_tool = m.group("tool")
        pattern = m.group("pattern").strip()
        # Normalise "BashTool" → "bash", keep everything else as lowercase
        tool = _normalise_tool(raw_tool)
        return PermissionRule(action=action, tool=tool, pattern=pattern, source=source)

    # Fallback: treat whole string as a glob pattern matching all tools
    return PermissionRule(action=action, tool="*", pattern=spec, source=source)


def _normalise_tool(raw: str) -> str:
    """Lowercase and strip common suffixes for consistent matching."""
    lower = raw.lower()
    if lower.endswith("tool"):
        lower = lower[: -len("tool")]
    return lower or "*"

"""Typed errors for the NAVIG Reflex.

Every error carries the exact next command the operator can run — the same
"actionable hint" rule the voice plugin's ``PlanError`` follows. Nothing here
imports a model, a network client, or a plugin; the router is deterministic and
offline by construction.
"""

from __future__ import annotations


class ReflexError(RuntimeError):
    """Base class: something the router refuses, with an actionable hint."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        return {"error": self.message, "hint": self.hint}


class ManifestError(ReflexError):
    """A tool manifest is invalid, or binds to a command that does not exist."""


class PlanError(ReflexError):
    """Base class for commit-token failures."""


class PlanUnknown(PlanError):
    """The token was never minted (or the database was replaced)."""


class PlanExpired(PlanError):
    """The token existed but its 300 s window closed — mint a new one."""


class TargetUnavailable(ReflexError):
    """No endpoint/resolver is available, so a device cannot be resolved."""


class CommandFailed(ReflexError):
    """The bound CLI command ran but reported failure."""

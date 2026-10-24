"""Typed errors for the Unified Endpoints layer.

Every failure a caller can act on carries a ``hint``: the CLI prints it under the
message, and ``--json`` puts it in the Answer ``data`` so a script or an agent
can follow the same next step instead of guessing.
"""

from __future__ import annotations


class EndpointError(RuntimeError):
    """Base error: a message plus a next step the caller can take."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict[str, str]:
        return {"error": self.message, "hint": self.hint}


class HomeUnavailable(EndpointError):
    """The shipped ``navig-home`` plugin could not be reached or answered badly.

    The projection reads the plugin's CLI (never its YAML), so this is the one
    failure that means "the inventory is not readable from here" — a missing
    plugin, an unreadable config dir or a crashed child. It is always actionable:
    install the plugin, or run the same command by hand to see the real error.
    """


class EndpointNotFound(EndpointError):
    """No endpoint with that id exists in the live projection."""


class EndpointDirectiveError(EndpointError):
    """The request names a directive (or value) the endpoint cannot accept."""

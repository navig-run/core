"""Route one MCP tool call to the machine that owns its data.

``navig.mcp.capability`` answers WHERE a family lives; this module is the handler shape that
asks it on every call. Memory learned this first (``navig.mcp.tools.memory._routed``) and the
pattern is identical everywhere: the local implementation stays the authority when this
machine is the Brain or was never linked, a refused route returns the refusal, and a Brain
that cannot answer produces a structured error instead of a second local write.

It lives here - beside ``_args.py``, the other bundle-shared helper - so the next Brain-owned
family does not copy fifteen lines of try/except and drift on which failure means "the call
did not run".

NOT the same thing as ``navig.mcp.routing``, which is the compact 15-tool surface over the
catalogue. This decides WHICH MACHINE runs a call; that one decides WHICH TOOL a caller talks
to.
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["routed"]


def routed(
    tool: str,
    local: Callable[..., Any],
    forward: Callable[[Any, dict[str, Any]], Any],
) -> Callable[..., Any]:
    """The handler a client actually calls: route first, then run here or on the Brain."""

    def run(server: Any, args: dict[str, Any]) -> Any:
        from navig.brain.client import BrainUnavailable
        from navig.mcp import capability

        route = capability.resolve(tool)
        if route.is_refused:
            return route.refusal
        if route.is_brain and route.client is not None:
            try:
                return forward(route.client, args or {})
            except BrainUnavailable as exc:
                return capability.failure_payload(exc, route.capability)
        return local(server, args)

    # The Brain executes the real function; this wrapper is the client's half. Recorded so a
    # reader can tell the two apart - tests and ``navig doctor`` both rely on it.
    run.__wrapped__ = local  # type: ignore[attr-defined]
    return run

"""navig.mcp.registration — the admission decision for a new MCP server.

Registering a **stdio** MCP server means *"run this binary, with these arguments, as me,
and keep it running"* — strictly more powerful than ``bash_exec``, which the agent cannot
invoke without asking. So registration is held for the operator.

**This module exists because there is more than one door.** ``POST /mcp/connect`` grew an
interlock; ``POST /api/deck/mcp/servers`` did not, and it is the *more* dangerous of the
two — it writes the server into ``config.yaml``, and ``GatewayServer`` auto-connects
everything under ``mcp.servers`` at boot, so an unapproved command survives a restart. A
guard on one path is not a guard on the capability. Every caller that can cause an MCP
server to be run must come through here.

Transport-agnostic on purpose: it returns a decision, not an HTTP response, because the
two routes speak different error envelopes (``json_error_response`` vs
``{"ok": False, "error": …}``). Rendering is the caller's job; deciding is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegistrationRefused:
    """A registration the operator did not approve.

    ``detail`` is written for a human reading an error toast: it names what would have
    run, because "not approved" alone does not tell them what they are approving.
    """

    name: str
    target: str
    detail: str
    code: str = "approval_denied"


def registration_target(
    command: Any = None,
    args: Any = None,
    url: Any = None,
) -> tuple[list[str], str]:
    """Normalise a registration into ``(argv, human_target)``.

    ``command`` arrives as a string from one door and occasionally as a list from the
    other, and ``args`` is appended by both — so the flattening lives here rather than
    being re-derived (differently) per route.
    """
    if isinstance(command, list):
        argv = [str(a) for a in command]
    elif command:
        argv = [str(command)]
    else:
        argv = []
    argv += [str(a) for a in (args or [])]
    return argv, (" ".join(argv) if argv else str(url or ""))


async def authorize_registration(
    *,
    name: str,
    command: Any = None,
    args: Any = None,
    url: Any = None,
    transport: str = "stdio",
) -> RegistrationRefused | None:
    """Hold an MCP server registration for the operator.

    Returns ``None`` when the registration may proceed, or a
    :class:`RegistrationRefused` the caller renders in its own error shape.

    Fails **closed**: if the approval gate itself raises, the registration is refused. An
    interlock that degrades to "allow" on error is not an interlock.
    """
    from navig.tools.approval import ApprovalDecision, get_approval_gate
    from navig.tools.untrusted_text import code_span, plain_inline

    name = str(name or "")
    argv, target = registration_target(command, args, url)

    if transport == "stdio" and not argv:
        # Nothing to execute. The registering code rejects this on its own merits, and
        # asking the operator to approve running "" teaches them to click through.
        return None

    description = (
        f"Register MCP server {plain_inline(name)} ({plain_inline(transport)})\n\n"
        f"This will run: {code_span(target, 400)}\n\n"
        "A stdio server runs as a child process of the NAVIG gateway, with the "
        "operator's privileges, and stays running."
    )

    try:
        decision = await get_approval_gate().check(
            tool_name="mcp_server_register",
            safety_level="dangerous",
            parameters={
                "name": name,
                "transport": transport,
                "command": argv,
                "url": url,
            },
            reason=f"Register MCP server {name!r}",
            context={"description": description},
        )
    except Exception as exc:  # noqa: BLE001 — interlock broke ⇒ refuse, never proceed
        logger.error("MCP register interlock failed for %r — denying: %s", name, exc)
        return RegistrationRefused(
            name=name,
            target=target,
            detail="approval gate error — failing closed",
        )

    if decision != ApprovalDecision.APPROVED:
        return RegistrationRefused(
            name=name,
            target=target,
            detail=(
                f"Registering {name!r} would run {target!r} on this machine and was "
                "not approved."
            ),
        )
    return None

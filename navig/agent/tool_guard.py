"""navig.agent.tool_guard — the risk check behind NAVIG OS's Guarded permission mode.

Guarded mode runs an agent's tool calls like Execute, except that a call which is not
read-only is first described to the decision model (Clef, :mod:`navig.agent.decide`),
which answers three yes/no questions: is it hard to undo, does it reach outside the
project, does it reach other people or services. Any "probably yes" turns the call into
an ordinary permission prompt in the OS.

The check can only ADD a prompt; it never approves anything. So whenever it cannot give
an answer — Clef off, this use off or in shadow mode, no credential, a timeout, an
unexpected reply — :func:`judge_tool_call` returns ``None`` and the OS asks the user,
with the reason "could not be checked". Fail closed, by design.

What is sent: the tool name, the command or (clipped) arguments, and the project
directory, run through ``decide.redact`` when ``decide.redact`` is on (the default).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from navig.agent import decide

logger = logging.getLogger(__name__)

USE_ID = "guard_tool_call"

#: A risk counts when Clef's probability of "yes" reaches this. Lower than the 0.8 the
#: upstream design used: running a likely-irreversible call unasked is the expensive
#: mistake, a needless prompt the cheap one, and Clef-flash is a small model.
RISK_THRESHOLD = 0.5

#: Arguments longer than this are clipped before they leave the machine.
MAX_ARGUMENTS_CHARS = 4000

QUESTIONS: tuple[decide.Question, ...] = (
    decide.yesno(
        "irreversible",
        "Would running this tool call be hard or impossible to undo? For example: it deletes or "
        "overwrites files or data, force-pushes or rewrites history, drops a database table, "
        "spends money, or sends something that cannot be unsent.",
        yes="it destroys, overwrites, sends or spends something that cannot simply be undone",
        no="it only reads, or makes a change that is easy to reverse",
    ),
    decide.yesno(
        "outside_workspace",
        "Does this tool call read or change anything outside the project directory named in the "
        "state (other folders, the home directory, system settings, other machines)?",
        yes="it touches files, settings or machines outside the project directory",
        no="it stays inside the project directory",
    ),
    decide.yesno(
        "external",
        "Does this tool call reach other people or outside services in a way that changes "
        "something there? For example: sending a message or email, posting, publishing, "
        "deploying, opening a pull request, or calling a remote API that writes.",
        yes="it sends, posts, publishes, deploys or writes to a remote service",
        no="it is local, or only reads from a remote service",
    ),
)

RISK_IDS: tuple[str, ...] = tuple(q.id for q in QUESTIONS)


def describe_call(call: Mapping[str, Any]) -> str:
    """The text Clef judges (redaction happens later, in ``decide.build_body``)."""
    lines = [f"Tool: {str(call.get('tool') or 'unknown')[:200]}"]
    command = call.get("command")
    if isinstance(command, str) and command.strip():
        lines.append(f"Command: {command[:MAX_ARGUMENTS_CHARS]}")
    arguments = call.get("arguments")
    if arguments:
        try:
            text = json.dumps(arguments, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(arguments)
        if len(text) > MAX_ARGUMENTS_CHARS:
            text = text[:MAX_ARGUMENTS_CHARS] + " …(clipped)"
        lines.append(f"Arguments: {text}")
    project = call.get("project_directory")
    if isinstance(project, str) and project.strip():
        lines.append(f"Project directory: {project[:500]}")
    return "\n".join(lines)


async def judge_tool_call(call: Mapping[str, Any]) -> dict[str, Any] | None:
    """Ask Clef about one tool call. Never raises.

    Returns ``{"risks": [...], "probabilities": {id: p_yes}, "latency_ms": n}`` — an empty
    ``risks`` list means "go ahead" — or ``None`` when there is no usable answer, which the
    caller must treat as "ask the user".
    """
    try:
        out = await decide.aoutcome(describe_call(call), QUESTIONS, use=USE_ID)
    except Exception:  # noqa: BLE001 — the contract: never raise into the gateway
        logger.warning("tool_guard: decision model failed", exc_info=True)
        return None
    if out is None or not out.live:
        # Off, shadow mode or no answer: a shadow answer must not decide a live call.
        return None
    probabilities: dict[str, float] = {}
    for qid in RISK_IDS:
        decision = out.answers.get(qid)
        if decision is None or "yes" not in decision.probs:
            return None  # a partial answer is not an answer
        probabilities[qid] = round(float(decision.probs["yes"]), 4)
    risks = [qid for qid in RISK_IDS if probabilities[qid] >= RISK_THRESHOLD]
    return {"risks": risks, "probabilities": probabilities, "latency_ms": out.latency_ms}

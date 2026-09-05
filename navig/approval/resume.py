"""Re-enter a turn when an approval arrives after that turn has already ended.

#1062 made a late answer *visible*. This makes it **actionable**: the operator taps Approve
two minutes (or two hours) later and the action they authorised actually happens, with the
reply delivered to the channel that asked.

**Why re-entry rather than resume.** The turn's own state lives in `run_agentic` — 1312
lines, 35 `await` points, 130 locals — inside a coroutine stack frame that cannot be
serialized. Checkpointing it would mean rewriting the product's most critical loop. So this
does not continue the original turn; it starts a **new** one, seeded with the conversation
the session store already holds and told that the pending tool call is now approved. The
agent re-derives its next step. The user-visible outcome is the same — their action
completes and they get an answer — and `run_agentic` is untouched.

Three safety properties, because this executes a privileged action outside the turn that
requested it:

* **Single-use.** The record is *taken* (popped) before anything runs, so one approval can
  never produce two executions — even if the operator double-taps, or two channels deliver
  the same callback, or a retry lands.
* **Approved only.** A denial resumes nothing. There is no path from "denied" to execution.
* **Bounded age.** An approval answered long after the fact is reported but NOT executed.
  Tapping Approve on a week-old notification must not deploy to production; the operator is
  told to re-run deliberately instead.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.approval.resume")

#: Beyond this, a late approval is reported but not executed. An hour is long enough to
#: cover "I was in a meeting" and short enough that the world has probably not moved on.
#: Override with `approval.resume_max_age_seconds`.
_DEFAULT_MAX_AGE_S = 3600.0


def _store_path() -> Path:
    from navig.platform import paths

    return paths.config_dir() / "approvals" / "resumable.json"


def _load() -> dict[str, Any]:
    from navig.core.json_io import load_json_safe

    data = load_json_safe(_store_path(), default={})
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    from navig.core.json_io import atomic_write_json

    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # (data, path) — data FIRST. Reversed, this writes the path as the document and every
    # read returns empty, which the callers' best-effort handlers would then hide.
    atomic_write_json(data, path)


def max_age_seconds() -> float:
    """How stale a late approval may be and still execute."""
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().get("approval.resume_max_age_seconds", _DEFAULT_MAX_AGE_S)
        return float(raw)
    except Exception:  # noqa: BLE001 — a config read must not break the gate
        return _DEFAULT_MAX_AGE_S


def record(
    request_id: str,
    *,
    session_key: str,
    channel: str,
    user_id: str,
    tool_name: str,
    parameters: dict[str, Any] | None = None,
) -> None:
    """Remember enough to re-enter a turn for *request_id*. Never raises.

    Deliberately does NOT copy the conversation history: the session store already owns it,
    and a second copy would be one that can go stale between the ask and the answer.
    """
    try:
        data = _load()
        data[request_id] = {
            "session_key": session_key,
            "channel": channel,
            "user_id": user_id,
            "tool_name": tool_name,
            "parameters": parameters or {},
            "asked_at": time.time(),
        }
        _save(data)
    except Exception:  # noqa: BLE001 — bookkeeping must never break an approval
        pass


def take(request_id: str) -> dict[str, Any] | None:
    """Pop the record for *request_id* — the single-use guarantee lives here.

    Taking BEFORE execution (rather than deleting after) is what makes a double-tap safe:
    the second caller finds nothing and does nothing, instead of running the tool twice.
    """
    try:
        data = _load()
        entry = data.pop(request_id, None)
        if entry is not None:
            _save(data)
        return entry
    except Exception:  # noqa: BLE001
        return None


def list_resumable() -> dict[str, Any]:
    """Every record still awaiting a late answer — the enumerating read for status views."""
    try:
        return _load()
    except Exception:  # noqa: BLE001
        return {}


def peek(request_id: str) -> dict[str, Any] | None:
    """Read a record WITHOUT consuming it.

    Separate from :func:`take` on purpose: the caller that merely wants to know "was this
    ever asked?" must not consume the single-use token that `resume_after_approval` relies
    on for its exactly-once guarantee.
    """
    try:
        return _load().get(request_id)
    except Exception:  # noqa: BLE001
        return None


def discard(request_id: str) -> None:
    """Drop a record without acting on it (the turn ended normally, or was denied)."""
    take(request_id)


def is_too_old(entry: dict[str, Any], *, now: float | None = None) -> bool:
    asked = float(entry.get("asked_at") or 0.0)
    return ((now if now is not None else time.time()) - asked) > max_age_seconds()


async def resume_after_approval(
    request_id: str,
    *,
    run_turn: Any = None,
    deliver: Any = None,
) -> bool:
    """Re-enter a turn for an approval that arrived late. Returns True if it ran.

    *run_turn* and *deliver* are injectable so the behaviour can be tested without an LLM
    or a live channel; production passes neither.
    """
    entry = take(request_id)          # single-use: pop first, act second
    if entry is None:
        return False

    if is_too_old(entry):
        logger.warning(
            "Approval %s was answered %.0fs after it was asked — too old to resume "
            "automatically; the operator must re-run it",
            request_id,
            time.time() - float(entry.get("asked_at") or 0.0),
        )
        await _notify(
            deliver,
            "Approval arrived too late",
            f"'{entry.get('tool_name')}' was approved after the request went stale. "
            "Nothing was run — re-issue the request if you still want it.",
        )
        return False

    runner = run_turn or _default_run_turn
    try:
        reply = await runner(entry)
    except Exception:  # noqa: BLE001 — a resume failure must not escape into the caller
        logger.exception("Approval %s: resume failed", request_id)
        await _notify(
            deliver,
            "Could not finish the approved action",
            f"'{entry.get('tool_name')}' was approved, but re-running it failed. "
            "Check the logs and re-issue if needed.",
        )
        return False

    await _notify(
        deliver,
        "Finished the action you approved",
        str(reply or "").strip() or f"'{entry.get('tool_name')}' completed.",
    )
    return True


async def _notify(deliver: Any, title: str, body: str) -> None:
    """Deliver through the same channel-agnostic seam the incident producer uses."""
    if deliver is not None:
        await deliver(title, body)
        return
    try:
        from navig.notify import dispatch

        await dispatch("approval_resume", title, body, data={"source": "navig"})
    except Exception:  # noqa: BLE001 — a delivery failure must not lose the result
        logger.warning("approval resume: could not deliver result: %s", body[:200])


async def _default_run_turn(entry: dict[str, Any]) -> str:
    """Start a fresh turn seeded with the conversation, told the call is approved.

    The history comes from the session store rather than the record, so the agent sees the
    conversation as it stands now — including anything said while the approval was pending.
    """
    from navig.agent.conv.agent import ConversationalAgent

    tool = entry.get("tool_name") or "the requested action"
    session_key = str(entry.get("session_key") or "")

    # ⚠ Verified against the real signatures, not assumed. `ConversationalAgent.__init__`
    # takes no `session_id`, and `chat()` takes no `session_key` — only `run_agentic` does
    # (`session_key: str = ""`), and `chat()` is a thin dispatcher onto it. Writing the
    # obvious-looking `ConversationalAgent(session_id=…).chat(msg, session_key=…)` would
    # have raised TypeError straight into this module's own except handler: the exact
    # defect class the call-arg gate exists for.
    agent = ConversationalAgent()
    return await agent.run_agentic(
        f"The approval you were waiting for has been granted: {tool} is now authorised. "
        "Carry out that action and report the result.",
        session_key=session_key,
    )

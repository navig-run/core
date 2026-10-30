"""Why did the daemon (re)start? — answered on the supervisor's boot line.

The supervisor logged ``=== NAVIG Daemon starting (pid=…) ===`` and nothing else, so a
day of restarts read as seven identical lines. The only restart that recorded a caller
(``/api/daemon/restart`` → ``actor=…``) wrote it into the OLD process's log, which the
restart then killed — and the other paths recorded nothing at all. Every restart also
opens a "Brain offline" gap in the Telegram deck, so "who keeps restarting the brain?"
had no answer short of guessing.

Every navig path that stops the daemon on purpose now leaves an **intent** file just
before it does (:func:`record`); the next boot consumes it (:func:`consume`) and names
the cause. A boot with NO intent is itself the answer: nothing in navig asked for it —
see :func:`boot_cause`, which also uses the clean-shutdown marker to tell a daemon that
stopped cleanly from one that was killed hard or crashed.

Intents chain: the restart API records itself, then hands off to the scheduled task,
which runs ``navig service restart``, which records again — the boot line shows the
whole path (``api (actor=…) → service restart``) rather than only the last hop.

Everything here is best-effort and never raises: a diagnostic must not be able to stop
a restart. Nothing secret is stored — the first few argv tokens only (a command line can
carry a token), the parent process NAME, and an 8-char prefix of the agent session id.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# An intent older than this belongs to a restart that never happened (the stop failed,
# the box rebooted instead) and must not be blamed for a later, unrelated boot.
INTENT_MAX_AGE_S = 300

_INTENT = "restart_intent.json"
_CLEAN = "clean_exit.json"
# Left when a previous daemon is known to have ended WITHOUT an orderly shutdown —
# its stale pid file was reaped (a status check or the next boot's running-guard
# deletes that file BEFORE the boot line can see it). Consumed by the next boot.
_DIED = "previous_died.json"


def _dir() -> Path:
    # The supervisor's resolver, not service_manager's: it redirects under pytest, so a
    # test can never write into the operator's real daemon dir.
    from navig.daemon.supervisor import _daemon_dir

    return _daemon_dir()


def _actor() -> dict[str, Any]:
    info: dict[str, Any] = {"pid": os.getpid()}
    # First tokens only — enough to read "navig service restart", never a flag value.
    info["argv"] = " ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(sys.argv[:4]))
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if sid:
        info["agent_session"] = sid[:8]
    try:
        import psutil

        parent = psutil.Process(os.getppid())
        info["parent"] = parent.name()
        grand = parent.parent()
        if grand is not None:
            info["grandparent"] = grand.name()
    except Exception:  # noqa: BLE001 — psutil missing or a parent already gone
        pass
    return info


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing or unreadable: no record
        return None


def record(source: str, **detail: Any) -> None:
    """Note that *source* is about to stop the daemon on purpose. Never raises."""
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / _INTENT
        hop = {"source": source, "at": time.time(), **_actor(), **{k: v for k, v in detail.items() if v}}
        prior = _read(path)
        chain = []
        if prior and time.time() - float(prior.get("at", 0)) <= INTENT_MAX_AGE_S:
            chain = list(prior.get("chain") or [])
        chain.append(hop)
        path.write_text(json.dumps({"at": hop["at"], "chain": chain}), encoding="utf-8")
    except Exception:  # noqa: BLE001 — a diagnostic must never block a restart
        pass


def mark_previous_died(pid: int, reason: str) -> None:
    """Note that daemon *pid* ended with no clean shutdown. Never raises."""
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / _DIED).write_text(
            json.dumps({"at": time.time(), "pid": pid, "reason": reason}), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 — a diagnostic must never block a status check
        pass


def mark_clean_exit() -> None:
    """Called by the supervisor's orderly shutdown. Never raises."""
    try:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / _CLEAN).write_text(json.dumps({"at": time.time(), "pid": os.getpid()}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _consume(name: str) -> dict[str, Any] | None:
    try:
        path = _dir() / name
        data = _read(path)
        path.unlink(missing_ok=True)
        return data
    except Exception:  # noqa: BLE001
        return None


def _hop_text(hop: dict[str, Any]) -> str:
    who = [f"{k}={hop[k]}" for k in ("actor", "agent_session", "parent", "grandparent") if hop.get(k)]
    return f"{hop.get('source', '?')}" + (f" ({', '.join(who)})" if who else "")


def boot_cause(*, had_previous_pid: bool) -> str:
    """One line naming why this boot happened. Consumes the intent + clean-exit marker."""
    intent = _consume(_INTENT)
    clean = _consume(_CLEAN)
    died = _consume(_DIED)
    if intent and time.time() - float(intent.get("at", 0)) <= INTENT_MAX_AGE_S:
        return "requested: " + " → ".join(_hop_text(h) for h in intent.get("chain") or [])
    if clean and not died:
        return "no restart requested; previous daemon stopped cleanly (logon task, service start, or manual)"
    if had_previous_pid or died:
        return (
            "UNEXPLAINED — previous daemon ended with no clean shutdown and no navig "
            "restart request (killed from outside navig, or crashed)"
        )
    return "first start (no previous daemon recorded)"

"""Session/owner registry — which live session holds which role, card and worktree.

The persistence between a session's identity (a Claude session id) and the roles
a coordination protocol assigns it. Spec: T-036 / NAV-016 — stored at
``config_dir()/registry/agents.json``, so a future mesh discovery that
implements NAV-016 merges into THIS file instead of forking it.

Design constraints, chosen to reuse what already proves itself here:

* **No new lock.** The registry answers "who owns what" for *assignment*
  decisions; the atomic card claim (``BoardStore.claim_card``) is the lock.
* **Liveness without a daemon.** Entries carry ``last_seen``; staleness
  (> ``STALE_AFTER_MINUTES``) is advisory on read, the same way
  ``.dev/agent.lock``'s TTL is — nobody is killed for going quiet, and an
  orchestrator reclaims abandoned work explicitly.
* **Two identity paths, in order.** ``CLAUDE_CODE_SESSION_ID`` (what the
  agent-lock hooks use) matches first; a pid-ancestry match on the process
  that carried the session id is the fallback, so a shell whose environment
  lost the variable still resolves to its own claim.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The four coordination roles. Formation ``schema-grid`` renders these into
#: ``.claude/agents``; the card CLI refuses stage moves outside each role's matrix.
ROLES: tuple[str, ...] = ("orchestrator", "backend-worker", "frontend-worker", "reviewer")

#: An entry older than this is shown as ``stale`` (advisory — reclaim by hand).
STALE_AFTER_MINUTES = 30

_REGISTRY_VERSION = 1


def registry_path() -> Path:
    """Where the registry lives: ``~/.navig/registry/agents.json`` (NAV-016)."""
    from navig.platform.paths import config_dir  # noqa: PLC0415

    return config_dir() / "registry" / "agents.json"


def load() -> dict[str, Any]:
    """Read the registry; a missing or torn file reads as empty, never as an error."""
    path = registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": _REGISTRY_VERSION, "agents": []}
    if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
        return {"version": _REGISTRY_VERSION, "agents": []}
    return data


def _save(data: dict[str, Any]) -> None:
    """Write atomically (temp file in the same dir + os.replace) — a reader in
    another session never sees a half-written registry."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def current_session_id() -> str:
    """The Claude session id of the caller: env first (what agent_lock uses),
    then the nearest ancestor process started with ``--session-id``/``--resume``."""
    env = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if env:
        return env.lower()
    try:
        import psutil  # noqa: PLC0415

        from navig.commands.processes import session_id_of  # noqa: PLC0415

        proc = psutil.Process()
        for p in (proc, *proc.parents()):
            try:
                sid = session_id_of(p.cmdline())
            except (psutil.Error, OSError):
                continue
            if sid:
                return sid
    except Exception:  # noqa: BLE001 - registry must never take the CLI down
        pass
    return ""


def _session_process_pid(session_id: str) -> int | None:
    """The pid of the process that carries this session id — the ancestry anchor
    used when a later shell has lost the environment variable."""
    if not session_id:
        return None
    try:
        import psutil  # noqa: PLC0415

        from navig.commands.processes import session_id_of  # noqa: PLC0415

        for p in psutil.process_iter(["pid"]):
            try:
                if session_id_of(p.cmdline()) == session_id:
                    return int(p.pid)
            except (psutil.Error, OSError):
                continue
    except Exception:  # noqa: BLE001
        pass
    return None


def _ancestor_pids() -> set[int]:
    try:
        import psutil  # noqa: PLC0415

        proc = psutil.Process()
        out: set[int] = set()
        cur = proc
        while cur is not None and cur.pid not in out:
            out.add(int(cur.pid))
            cur = cur.parent()
        return out
    except Exception:  # noqa: BLE001
        return set()


def find_mine() -> dict[str, Any] | None:
    """The active registry entry belonging to the calling session, if any.

    Session id match wins; pid-ancestry over all active entries is the fallback.
    """
    agents = [a for a in load().get("agents", []) if a.get("status") == "active"]
    if not agents:
        return None
    sid = current_session_id()
    if sid:
        for entry in agents:
            if entry.get("session_id") == sid:
                return entry
    pids = _ancestor_pids()
    if pids:
        for entry in agents:
            pid = entry.get("pid")
            if isinstance(pid, int) and pid in pids:
                return entry
    return None


def claim(
    role: str,
    *,
    session_id: str | None = None,
    card: str | None = None,
    worktree: str | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Create or refresh this session's entry (one entry per session — a second
    claim with new worktree/card coordinates is an update, never a duplicate)."""
    from navig.core.dict_utils import now_iso  # noqa: PLC0415

    sid = (session_id or current_session_id() or "").lower()
    if not sid:
        raise ValueError(
            "no session id: pass --session, or claim from inside a Claude session"
        )
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r} (roles: {', '.join(ROLES)})")

    now = now_iso()
    data = load()
    for entry in data["agents"]:
        if entry.get("session_id") == sid:
            entry.update(
                {
                    "role": role,
                    "card": card if card is not None else entry.get("card"),
                    "worktree": worktree if worktree is not None else entry.get("worktree"),
                    "branch": branch if branch is not None else entry.get("branch"),
                    "status": "active",
                    "last_seen": now,
                    "pid": _session_process_pid(sid) or entry.get("pid"),
                }
            )
            _save(data)
            return entry

    entry = {
        "session_id": sid,
        "role": role,
        "card": card,
        "worktree": worktree,
        "branch": branch,
        "status": "active",
        "claimed_at": now,
        "last_seen": now,
        "pid": _session_process_pid(sid),
    }
    data["agents"].append(entry)
    _save(data)
    return entry


def release(session_id: str | None = None) -> dict[str, Any] | None:
    """Mark this session's entry released (kept in the file as history)."""
    from navig.core.dict_utils import now_iso  # noqa: PLC0415

    sid = (session_id or current_session_id() or "").lower()
    if not sid:
        mine = find_mine()
        sid = str(mine.get("session_id", "")) if mine else ""
    if not sid:
        return None
    data = load()
    for entry in data["agents"]:
        if entry.get("session_id") == sid:
            entry["status"] = "released"
            entry["last_seen"] = now_iso()
            _save(data)
            return entry
    return None


def set_card(session_id: str, card: str | None) -> bool:
    """Record which board card *session_id* holds (``None`` clears it).

    ``navig card claim``/``release`` call this so ``navig mesh agents list`` and the
    board agree on who holds what. True when the session has an entry.
    """
    from navig.core.dict_utils import now_iso  # noqa: PLC0415

    sid = (session_id or "").lower()
    data = load()
    for entry in data["agents"]:
        if entry.get("session_id") == sid:
            entry["card"] = card
            entry["last_seen"] = now_iso()
            _save(data)
            return True
    return False


def clear_card(card: str) -> int:
    """Clear *card* from every entry that names it — a forced release frees a card
    another session held. Returns how many entries changed."""
    data = load()
    changed = 0
    for entry in data["agents"]:
        if entry.get("card") == card:
            entry["card"] = None
            changed += 1
    if changed:
        _save(data)
    return changed


def _age_seconds(last_seen: str | None) -> int | None:
    if not last_seen:
        return None
    try:
        when = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - when).total_seconds()))


def list_entries(*, active_only: bool = False) -> list[dict[str, Any]]:
    """Every entry with ``age_seconds`` and an advisory ``stale`` flag."""
    out: list[dict[str, Any]] = []
    for entry in load().get("agents", []):
        if active_only and entry.get("status") != "active":
            continue
        age = _age_seconds(entry.get("last_seen"))
        row = dict(entry)
        row["age_seconds"] = age
        row["stale"] = age is None or age > STALE_AFTER_MINUTES * 60
        out.append(row)
    return out

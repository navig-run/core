"""Per-host advisory lock — stop two agent sessions mutating the same server at once.

Why this exists
---------------
On 2026-08-24 two Claude sessions operated on ``cybesis-vps`` simultaneously with no
interlock. One was fixing two down websites; the other was decommissioning the host's
legacy web stack. Inside one hour the second session ran a full ``apt upgrade``, purged
apache2 + php8.3 + HestiaCP, rewrote root's crontab and **rebooted the box** — while the
first session was in the middle of recreating containers. Nothing was lost, but only by
luck: a package purge landing between "stop old container" and "start new one" would have
left a production site down with nobody realising why.

``navig repo`` already solves exactly this for git checkouts (``.dev/agent.lock``). This
is the same idea for the other thing agents mutate: **remote hosts**. Schema and TTL
semantics are deliberately kept parallel to :mod:`navig.commands.repo` so the two feel
like one mechanism.

Session identity — read this before trusting the lock
----------------------------------------------------
A lock is only as good as its notion of "who am I". Every ``navig`` invocation is a fresh
process, usually from a fresh shell, so process identity is useless: consecutive calls
from the *same* agent would look like different sessions (self-blocking), while two
*different* agents on the same machine would look identical (no protection at all).

So identity must be supplied. In precedence order:

1. ``NAVIG_SESSION_ID`` — explicit, and the correct answer for agents.
2. ``CLAUDE_SESSION_ID`` / ``CLAUDE_CODE_SESSION_ID`` — set by some Claude Code versions.
3. A terminal-scoped id (``WT_SESSION`` on Windows, the controlling tty on POSIX).
4. ``user@machine`` — a last resort that is **honest but weak**: two agents sharing one
   account collapse to one identity, which is precisely the case this module exists to
   catch.

:func:`identity_quality` reports which tier was used so callers can warn instead of
implying a protection that is not there. Agents should export ``NAVIG_SESSION_ID``.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import socket
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Keep in sync with navig.commands.repo.LOCK_TTL_MINUTES — one mental model for both locks.
LOCK_TTL_MINUTES = 60

#: ``NAVIG_HOST_LOCK`` accepts these. ``block`` refuses to run while another live session
#: holds the host; ``warn`` prints and continues; ``off`` disables the check entirely.
MODES = ("block", "warn", "off")
DEFAULT_MODE = "block"


# ── identity ─────────────────────────────────────────────────────────────────


def _tty_token() -> str | None:
    """A per-terminal token, when the platform offers one."""
    wt = os.environ.get("WT_SESSION")  # Windows Terminal
    if wt:
        return f"wt:{wt}"
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is not None and stream.isatty():
                name = os.ttyname(stream.fileno())  # type: ignore[attr-defined]
                if name:
                    return f"tty:{name}"
        except (OSError, AttributeError, ValueError):
            continue
    return None


def session_id() -> str:
    """Best-effort identity for the calling session. See module docstring."""
    for var in ("NAVIG_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        val = os.environ.get(var)
        if val and val.strip():
            return val.strip()
    tty = _tty_token()
    if tty:
        return tty
    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover - exotic environments
        user = "unknown"
    return f"{user}@{platform.node() or socket.gethostname()}"


def identity_quality() -> str:
    """``"explicit"`` | ``"terminal"`` | ``"weak"`` — how trustworthy :func:`session_id` is."""
    for var in ("NAVIG_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        val = os.environ.get(var)
        if val and val.strip():
            return "explicit"
    return "terminal" if _tty_token() else "weak"


def mode() -> str:
    """Enforcement mode from ``NAVIG_HOST_LOCK`` (default ``block``)."""
    raw = (os.environ.get("NAVIG_HOST_LOCK") or DEFAULT_MODE).strip().lower()
    return raw if raw in MODES else DEFAULT_MODE


# ── storage ──────────────────────────────────────────────────────────────────


def lock_dir(base_dir: Path | None = None) -> Path:
    """Directory holding host locks — ``config_dir()/locks``.

    Resolved through :func:`navig.platform.paths.config_dir` rather than
    ``Path.home()/".navig"`` so a custom ``NAVIG_CONFIG_DIR`` is honoured. Hardcoding home
    would split-brain the lock: one session writing to the configured dir while another
    reads the real home means neither sees the other — the precise failure this module
    exists to prevent.
    """
    if base_dir is None:
        override = os.environ.get("NAVIG_HOST_LOCK_DIR")
        if override:
            return Path(override)
        from navig.platform.paths import config_dir

        base_dir = config_dir()
    return Path(base_dir) / "locks"


def _safe_name(host: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in host) or "_"


def lock_path(host: str, base_dir: Path | None = None) -> Path:
    return lock_dir(base_dir) / f"host-{_safe_name(host)}.lock"


def read_lock(host: str, base_dir: Path | None = None) -> dict | None:
    """Parsed lock, or ``None`` when absent/corrupt (a corrupt lock must never block)."""
    try:
        return json.loads(lock_path(host, base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ── state ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LockState:
    state: str  # "free" | "mine" | "held" | "stale"
    age_minutes: float | None = None
    session: str | None = None
    user: str | None = None
    machine: str | None = None
    command: str | None = None
    claimed_at: str | None = None

    @property
    def blocking(self) -> bool:
        """Another session holds this host and the lock has not expired."""
        return self.state == "held"


def lock_state(
    lock: dict | None,
    *,
    me: str | None = None,
    now: datetime | None = None,
) -> LockState:
    """Classify a lock relative to the calling session."""
    if not lock:
        return LockState(state="free")
    me = me or session_id()
    now = now or datetime.now(timezone.utc)

    meta = {
        "session": str(lock.get("session_id") or "?"),
        "user": lock.get("user"),
        "machine": lock.get("machine"),
        "command": lock.get("command"),
        "claimed_at": lock.get("claimed_at"),
    }

    try:
        updated = datetime.fromisoformat(str(lock.get("updated_at", "")))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = (now - updated).total_seconds() / 60
    except ValueError:
        # Unparseable timestamp: treat as stale rather than jamming the host forever.
        return LockState(state="stale", age_minutes=None, **meta)

    if meta["session"] == me:
        return LockState(state="mine", age_minutes=round(age, 1), **meta)
    if age > LOCK_TTL_MINUTES:
        return LockState(state="stale", age_minutes=round(age, 1), **meta)
    return LockState(state="held", age_minutes=round(age, 1), **meta)


# ── mutation ─────────────────────────────────────────────────────────────────


def _atomic_write(path: Path, payload: dict) -> None:
    """Write via a temp file + replace so a crash can't leave a half-written lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".lock-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def claim(
    host: str,
    command: str | None = None,
    *,
    base_dir: Path | None = None,
    me: str | None = None,
) -> dict:
    """Claim or refresh the lock for ``host``. Returns the written payload.

    Refreshing preserves the original ``claimed_at`` so "held for 40 minutes" stays true
    across many commands in one session.
    """
    me = me or session_id()
    now = datetime.now(timezone.utc).isoformat()
    existing = read_lock(host, base_dir)
    claimed_at = now
    if existing and str(existing.get("session_id")) == me:
        claimed_at = str(existing.get("claimed_at") or now)

    try:
        user = getpass.getuser()
    except Exception:  # pragma: no cover
        user = "unknown"

    payload = {
        "host": host,
        "session_id": me,
        "identity_quality": identity_quality(),
        "user": user,
        "machine": platform.node() or socket.gethostname(),
        "pid": os.getpid(),
        "command": (command or "")[:200] or None,
        "claimed_at": claimed_at,
        "updated_at": now,
        "nonce": uuid.uuid4().hex[:8],
    }
    _atomic_write(lock_path(host, base_dir), payload)
    return payload


def release(
    host: str,
    *,
    force: bool = False,
    base_dir: Path | None = None,
    me: str | None = None,
) -> tuple[bool, str]:
    """Release the lock. Returns ``(released, reason)``.

    Refuses to drop a *live* lock owned by someone else unless ``force`` — stealing one
    is how you clobber the work it was protecting.
    """
    path = lock_path(host, base_dir)
    lock = read_lock(host, base_dir)
    if not lock:
        return True, "no lock present"

    st = lock_state(lock, me=me)
    if st.state == "held" and not force:
        return False, (
            f"held by session {st.session} ({st.user}@{st.machine}), "
            f"active {st.age_minutes}m ago — pass --force only if that session is dead"
        )
    try:
        path.unlink()
    except FileNotFoundError:
        return True, "no lock present"
    except OSError as exc:
        return False, f"could not remove lock: {exc}"
    return True, "released" if st.state != "stale" else "released (was stale)"


def describe(st: LockState, host: str) -> str:
    """Human-readable one-liner for a conflicting lock."""
    return (
        f"host '{host}' is being operated by another session\n"
        f"  session : {st.session}\n"
        f"  who     : {st.user}@{st.machine}\n"
        f"  since   : {st.claimed_at} (active {st.age_minutes}m ago)\n"
        f"  command : {st.command or '?'}"
    )


# ── enforcement ──────────────────────────────────────────────────────────────


def guard(host: str, command: str | None = None, *, base_dir: Path | None = None) -> LockState:
    """Claim ``host`` for this session, refusing to proceed if another session holds it.

    Called by mutating operations (``navig run``, remote file writes). Returns the
    pre-existing :class:`LockState` so callers can report what happened.

    Honours ``NAVIG_HOST_LOCK``: ``block`` (default) raises ``typer.Exit(2)`` on
    conflict, ``warn`` prints and continues, ``off`` skips entirely. ``off`` is also
    implied for the local host, where there is no remote to protect.
    """
    current = mode()
    if current == "off":
        return LockState(state="free")

    st = lock_state(read_lock(host, base_dir))

    if st.blocking:
        from navig import console_helper as ch

        if current == "warn":
            ch.warning(
                f"Another session is operating '{host}' — continuing anyway (NAVIG_HOST_LOCK=warn).",
                describe(st, host),
            )
        else:
            ch.error(
                f"Refusing to operate '{host}' — another session holds it.",
                describe(st, host) + "\n\nOptions:\n"
                f"  • wait — locks expire after {LOCK_TTL_MINUTES}m of inactivity\n"
                f"  • navig host lock status {host}      (inspect)\n"
                f"  • navig host lock release {host} --force   (only if that session is dead)\n"
                "  • NAVIG_HOST_LOCK=warn navig ...     (proceed anyway, this once)",
            )
            import typer as _typer

            raise _typer.Exit(2)
        return st

    claim(host, command, base_dir=base_dir)

    if identity_quality() == "weak" and st.state == "free":
        # Say it once per claim rather than implying protection that isn't there.
        from navig import console_helper as ch

        ch.dim(
            "host lock: weak identity (set NAVIG_SESSION_ID to make multi-agent detection reliable)"
        )
    return st


def guard_remote(config_manager, host: str | None, command: str | None = None) -> LockState | None:
    """:func:`guard`, skipped for the local host. Returns ``None`` when skipped.

    The one-liner every mutating remote command should call. Keeping the local-host
    exemption in here rather than at each call site means a new command cannot forget it
    — and cannot accidentally lock a host that is just this machine.
    """
    if not host:
        return None
    try:
        if config_manager.is_local_host(host):
            return None
    except Exception:
        # An unknown/odd host is treated as remote: locking something local by mistake is
        # a minor annoyance, failing to lock a real server is the bug we are fixing.
        pass
    return guard(host, command)

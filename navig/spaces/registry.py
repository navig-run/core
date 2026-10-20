"""Spaces registry — the brain's index of known workshops (enable/disable).

`~/.navig/spaces.json`:
    { "version": 1,
      "active": "<abs path>",
      "spaces": [ { id, name, path, source, enabled, last_active } ] }

Discovery auto-registers spaces found under ``spaces.roots`` as enabled. The
deck/extension toggle ``enabled`` (hidden from switcher + not merged) and set the
active space. The folder itself is always usable when you're inside it — the
registry only governs *global* visibility/activation.

Atomic JSON read/write mirrors the gateway ``cron_jobs.json``/``tasks.json`` pattern.
"""
from __future__ import annotations

import functools
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from navig.core.json_io import (
    JsonReadError,
    atomic_write_json,
    load_json_for_update,
    load_json_safe,
)
from navig.debug_logger import get_debug_logger
from navig.platform import paths

logger = get_debug_logger()

_DEFAULT_REGISTRY: dict[str, Any] = {"version": 1, "active": None, "spaces": []}

# Paths the user forgot. Discovery must not re-add them: before this list existed,
# `space forget` on a folder under a spaces root was undone by the very next scan.
# Cleared by an explicit `register` / `ensure_registered` (switch, doctor --fix).
_FORGOTTEN = "forgotten"

_F = TypeVar("_F", bound=Callable[..., Any])
_lock_state = threading.local()


def _registry_file() -> Path:
    return paths.config_dir() / "spaces.json"


@contextmanager
def _registry_lock() -> Iterator[None]:
    """Serialise load-modify-save across processes (CLI, gateway, discovery threads).

    Without it two writers each load, change one row, and save — the second save
    silently drops the first one's change. Re-entrant within a thread. A lock that
    cannot be taken (timeout, read-only dir) degrades to the old unlocked behaviour
    rather than failing the write.
    """
    depth = getattr(_lock_state, "depth", 0)
    if depth:
        _lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return
    lock_path = _registry_file().with_name("spaces.json.lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")  # noqa: SIM115 — held for the critical section
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # retries ~10 s, then OSError
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        logger.warning("spaces registry lock unavailable, writing unlocked: %s", exc)
        if handle is not None:
            handle.close()
            handle = None
    _lock_state.depth = 1
    try:
        yield
    finally:
        _lock_state.depth = 0
        if handle is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt  # noqa: PLC0415

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl  # noqa: PLC0415

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass  # closing the handle releases it anyway
            handle.close()


def _locked(fn: _F) -> _F:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _registry_lock():
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _norm(p: str | Path) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except Exception:  # noqa: BLE001
        return str(p)


def source_for(path: str | Path) -> str:
    """``"root"`` for a space living directly under ``~/.navig/spaces``, else ``"external"``.

    The one rule for the ``source`` column. Three callers each had their own: ``wire``
    used a string prefix (fragile to case and separators on Windows), ``space init``
    compared parents, and ``space doctor --fix`` hardcoded ``"root"`` — so an unregistered
    project folder repaired by doctor was filed as a spaces-root resident. Compared on
    normalised paths, direct children only: a space nested deeper is somebody's
    sub-space, not a root one.
    """
    try:
        return "root" if Path(_norm(path)).parent == Path(_norm(paths.spaces_dir())) else "external"
    except Exception:  # noqa: BLE001
        return "external"


def _normalize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return dict(_DEFAULT_REGISTRY)
    data.setdefault("version", 1)
    data.setdefault("active", None)
    if not isinstance(data.get("spaces"), list):
        data["spaces"] = []
    return data


def load_registry() -> dict[str, Any]:
    """Read-only view for discovery / list / is_enabled / is_trusted — degrades to a
    fresh registry on ANY failure (a corrupt or transiently-locked file must not break
    discovery). NEVER use this for a load-modify-save; use ``load_registry_for_update``."""
    return _normalize(load_json_safe(_registry_file(), default=dict(_DEFAULT_REGISTRY)))


def load_registry_for_update() -> dict[str, Any]:
    """Load for a load-MODIFY-save. Raises :class:`JsonReadError` when the file exists
    but is transiently unreadable (a lock that outlives the retries), so a mutator ABORTS
    its save instead of persisting an empty registry over every space and its
    trust/enabled/active state. A corrupt file is quarantined ``spaces.json.corrupt`` and
    then treated as empty (the bytes are already lost)."""
    return _normalize(load_json_for_update(_registry_file(), default=dict(_DEFAULT_REGISTRY)))


def _load_for_mutation() -> dict[str, Any] | None:
    """The registry to mutate, or ``None`` when it is transiently unreadable — in which
    case the caller MUST skip its save. Silently wiping every space (and the user's
    trust decisions) is far worse than skipping one update; the lock is temporary."""
    try:
        return load_registry_for_update()
    except JsonReadError as exc:
        logger.warning("spaces registry unreadable — skipping update to avoid wiping it: %s", exc)
        return None


def save_registry(reg: dict[str, Any]) -> None:
    try:
        atomic_write_json(reg, _registry_file())
    except OSError:
        pass  # best-effort


def _find(reg: dict[str, Any], id_or_path: str) -> dict[str, Any] | None:
    rp = _norm(id_or_path)
    for e in reg["spaces"]:
        if e.get("id") == id_or_path or _norm(e.get("path", "")) == rp:
            return e
    return None


def entry_for(path: str | Path) -> dict[str, Any] | None:
    """The registry row for *path* (read-only view), or None when unregistered."""
    rp = _norm(path)
    return next((e for e in load_registry()["spaces"] if _norm(e.get("path", "")) == rp), None)


def id_taken_by_another_path(space_id: str, path: str | Path) -> str | None:
    """The path already registered under *space_id*, if it is not *path* — else None.

    ``register`` keys on PATH, so a new folder that merely shares a NAME with an existing
    space appended a second entry with the same id, and ``_find(id)`` then answered
    whichever came first: ``cd ~/projects/homelab && navig space init`` silently made
    ``navig space use homelab`` ambiguous and listed two ``homelab`` rows. Callers that
    are about to register ask this first and refuse with the other path named.
    """
    reg = load_registry()
    rp = _norm(path)
    for e in reg.get("spaces", []):
        if e.get("id") == space_id and _norm(e.get("path", "")) != rp:
            return str(e.get("path", ""))
    return None


def forgotten_paths(reg: dict[str, Any] | None = None) -> set[str]:
    """Normalised paths the user forgot — discovery skips them (read-only)."""
    raw = (reg if reg is not None else load_registry()).get(_FORGOTTEN)
    return {_norm(p) for p in raw if isinstance(p, str)} if isinstance(raw, list) else set()


def is_forgotten(path: str | Path) -> bool:
    return _norm(path) in forgotten_paths()


def _unforget(reg: dict[str, Any], rp: str) -> bool:
    """Drop *rp* from the tombstone list; True when it was there."""
    raw = reg.get(_FORGOTTEN)
    if not isinstance(raw, list):
        return False
    kept = [p for p in raw if not (isinstance(p, str) and _norm(p) == rp)]
    if len(kept) == len(raw):
        return False
    if kept:
        reg[_FORGOTTEN] = kept
    else:
        reg.pop(_FORGOTTEN, None)
    return True


def _id_held_elsewhere(reg: dict[str, Any], space_id: str, rp: str) -> bool:
    return any(
        e.get("id") == space_id and _norm(e.get("path", "")) != rp for e in reg["spaces"]
    )


# ── one logical project identity ─────────────────────────────────────────────
#
# A project is ONE id → ONE canonical path; git worktrees (and other checkouts) of
# that repository are INSTANCES, never registry rows. Two defects violated this:
# `register`/`ensure_registered` appended a row for every path (discovery persisted a
# worktree folder before its duplicate check), and every id→root reader then took
# whichever row came first — so a migration or scanner could silently operate on the
# wrong checkout. The write layer now refuses/repoints collisions, and the readers
# ask `canonical_row_for_id` instead of trusting row order.


def _nested(child: str, parent: str) -> bool:
    """True when normed *child* is strictly inside *parent* (case/separator-insensitive)."""
    c = child.replace("/", "\\").casefold().rstrip("\\")
    p = parent.replace("/", "\\").casefold().rstrip("\\")
    return c != p and c.startswith(p + "\\")


def _is_checkout_of(path: str, holder: str) -> bool:
    """*path* is a checkout (worktree/instance) of the repository at *holder*:
    nested inside it, or the same git repository (worktrees live elsewhere on disk)."""
    if _nested(path, holder) or _nested(holder, path):
        return True
    try:
        from navig.projects import (
            gitinfo,  # noqa: PLC0415 — lazy: spaces must not import projects at module load
        )

        return gitinfo.same_repo(path, holder)
    except Exception:  # noqa: BLE001 — git missing/broken degrades to containment only
        return False


_claim_warned: set[tuple[str, str]] = set()


def _claim_id(reg: dict[str, Any], rp: str, space_id: str, name: str | None = None) -> str:
    """Decide a same-id collision BEFORE a row is written.

    Returns ``"write"`` (id free), ``"held"`` (another folder keeps the id — no row),
    or ``"repointed"`` (the holder row was moved to the ANCESTOR *rp*, which contains
    it — registering a main checkout heals a stray worktree row). Warns once per
    (id, path) pair: discovery re-tries every scan, a warning flood helps nobody.
    """
    holder = next(
        (e for e in reg["spaces"]
         if e.get("id") == space_id and _norm(e.get("path", "")) != rp),
        None,
    )
    if holder is None:
        return "write"
    hp = _norm(holder.get("path", ""))
    # The holder is an INSTANCE when it lives inside rp, or when it is a worktree of
    # the same repository while rp is the real checkout (a sibling/codex worktree row
    # heals to the path holding the actual `.git` directory). Registering the
    # INSTANCE's path while the canonical row exists stays refused below.
    holder_is_instance = (
        _nested(hp, rp)
        or (_is_checkout_of(hp, rp) and not (Path(hp) / ".git").is_dir()
            and (Path(rp) / ".git").is_dir())
    )
    if holder_is_instance:  # repoint the instance row to the canonical ancestor/checkout
        if holder.get("path") != rp:
            holder["path"] = rp
            if name:
                holder["name"] = name
        return "repointed"
    key = (space_id, rp)
    if key not in _claim_warned:
        _claim_warned.add(key)
        reason = "a checkout of it" if _is_checkout_of(rp, hp) else "another folder"
        logger.warning(
            "space id %r already belongs to %s (%s) — no row written for %s. "
            "One id, one project: rename the folder (navig space rename) to claim it.",
            space_id, hp, reason, rp,
        )
    return "held"


def canonical_row_for_id(reg: dict[str, Any], space_id: str) -> dict[str, Any] | None:
    """THE row for *space_id* — deterministic for any row order (legacy duplicates
    included): rows nested inside another same-id row are instances; among survivors
    the real repository (``.git`` directory, not a worktree's ``.git`` file) wins;
    then the shortest path, then casefold order.
    """
    rows = [e for e in reg.get("spaces", []) if e.get("id") == space_id]
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    paths = [_norm(e.get("path", "")) for e in rows]
    survivors = [
        e for i, e in enumerate(rows)
        if not any(_nested(paths[i], paths[j]) for j in range(len(rows)) if j != i)
    ]
    pool = survivors or rows
    real_repos = [e for e in pool if (Path(_norm(e.get("path", ""))) / ".git").is_dir()]
    if len(real_repos) == 1:
        return real_repos[0]
    pool = real_repos or pool
    return sorted(pool, key=lambda e: (len(_norm(e.get("path", ""))),
                                       _norm(e.get("path", "")).casefold()))[0]


def canonical_root(space_id: str, *, require_exists: bool = True) -> str | None:
    """The canonical filesystem root for a project id (stable regardless of row order).

    ``require_exists`` (default) returns None when the canonical folder is gone — a
    stale row must never resolve to a path that is not there. Set False for purely
    structural/registry questions.
    """
    row = canonical_row_for_id(load_registry(), space_id)
    if row is None:
        return None
    root = _norm(row.get("path", ""))
    if require_exists and not Path(root).is_dir():
        return None
    return root


@_locked
def register(
    path: str | Path,
    *,
    id: str | None = None,
    name: str | None = None,
    source: str = "root",
    enabled: bool = True,
) -> dict[str, Any]:
    """Add or update a space in the registry. Existing ``enabled`` is preserved.

    Keys on PATH, and enforces **one id ↔ one project**: a checkout (worktree) of the
    id's holder gets NO new row, an unrelated folder claiming a taken id is refused
    with a warning (the holder's row is returned so callers see the truth), and
    registering an ANCESTOR path repoints a stray descendant row to it. User-facing
    entry points still consult ``id_taken_by_another_path`` first to fail early with
    the other path named — this layer guarantees the invariant even when a caller
    forgets. An explicit register also lifts a previous ``forget``.
    """
    rp = _norm(path)
    entry = {
        "id": id or Path(rp).name,
        "name": name or Path(rp).name,
        "path": rp,
        "source": source,
        "enabled": enabled,
        "last_active": None,
    }
    reg = _load_for_mutation()
    if reg is None:
        return entry  # registry locked — not persisted now; usable in-memory, next write persists
    _unforget(reg, rp)
    existing = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == rp), None)
    if existing is not None:
        if id:
            existing["id"] = id
        if name:
            existing["name"] = name
        existing["source"] = source
        save_registry(reg)
        return existing
    # One id, one project: a checkout of the holder gets no row; an unrelated folder
    # claiming the id is refused (the holder's row comes back so callers see the truth).
    action = _claim_id(reg, rp, entry["id"], entry.get("name"))
    if action == "held":
        return canonical_row_for_id(reg, entry["id"]) or entry
    if action == "repointed":
        save_registry(reg)
        return canonical_row_for_id(reg, entry["id"]) or entry
    reg["spaces"].append(entry)
    save_registry(reg)
    return entry


def _ensure_in(
    reg: dict[str, Any], rp: str, id: str | None, name: str | None, source: str, auto: bool
) -> bool:
    """Apply ``ensure_registered`` to *reg* in memory; True when it changed anything."""
    if auto and rp in forgotten_paths(reg):
        return False  # the user forgot it; a scan is not a decision to bring it back
    changed = False if auto else _unforget(reg, rp)
    existing = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == rp), None)
    if existing is not None:
        # Never relabel a row onto an id another folder already holds: that undid the
        # `-2` suffix `projects` gives a clash and recreated the duplicate every scan.
        if id and existing.get("id") != id and not _id_held_elsewhere(reg, id, rp):
            existing["id"] = id
            if name:
                existing["name"] = name
            changed = True
        return changed
    want = id or Path(rp).name
    # The discovery hole that made 9 `navig` rows: a worktree folder scanned from its
    # own session appended a row before any duplicate check. Same rule as `register`.
    action = _claim_id(reg, rp, want, name)
    if action == "held":
        return False
    if action == "repointed":
        return True
    reg["spaces"].append({
        "id": want, "name": name or Path(rp).name,
        "path": rp, "source": source, "enabled": True, "last_active": None,
    })
    return True


def ensure_registered(
    path: str | Path,
    *,
    id: str | None = None,
    name: str | None = None,
    source: str = "root",
    auto: bool = False,
) -> None:
    """Register *path*, and correct a drifted id. Writes only when something changed.

    ⚠ This used to return the moment the PATH was known, so an entry written before the
    space had a manifest kept its FOLDER name as its id forever — `company-space` for a
    space canonically called `company`. All 19 entries on a real install were like that,
    which made a lookup by canonical id miss precisely the spaces that needed one.

    The id is a derived LABEL, not a key held anywhere else: `_find` matches by id or
    path, `is_enabled`/`is_trusted` take a path, and `enabled`/`trusted` live on the row
    — so correcting it preserves every stored state. A label another path already holds
    is left alone.

    *auto* marks a call from discovery: it never brings back a forgotten path. Explicit
    callers (switch, doctor --fix, repath) lift the tombstone, as ``register`` does.
    """
    rp = _norm(path)
    # Cheap unlocked dry run first: discovery calls this once per space per scan, and
    # almost every call is a no-op that must not take the cross-process lock.
    if not _ensure_in(load_registry(), rp, id, name, source, auto):
        return
    with _registry_lock():
        reg = _load_for_mutation()
        if reg is None:
            return  # registry locked — skip; a corrupt/locked read must not wipe it
        if _ensure_in(reg, rp, id, name, source, auto):
            save_registry(reg)


@_locked
def set_enabled(id_or_path: str, enabled: bool) -> bool:
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked — leave every space untouched
    e = _find(reg, id_or_path)
    if e is None:
        return False
    e["enabled"] = enabled
    save_registry(reg)
    return True


@_locked
def forget(id_or_path: str, *, remember: bool = True) -> bool:
    """Drop the row(s) for an id or path. The folder is never touched.

    With *remember* (the default) the forgotten paths are tombstoned so discovery does
    not re-register them on its next scan; ``register`` lifts it. ``space fold`` passes
    ``remember=False`` — the fold marker already hides the folder, and ``unfold`` must
    make it discoverable again without a second step.
    """
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked — don't drop anyone
    rp = _norm(id_or_path)
    dropped = [
        e for e in reg["spaces"]
        if e.get("id") == id_or_path or _norm(e.get("path", "")) == rp
    ]
    if not dropped:
        return False
    reg["spaces"] = [e for e in reg["spaces"] if not any(e is d for d in dropped)]
    if remember:
        tomb = reg.get(_FORGOTTEN) if isinstance(reg.get(_FORGOTTEN), list) else []
        known = {_norm(p) for p in tomb if isinstance(p, str)}
        for e in dropped:
            p = _norm(e.get("path", ""))
            if p and p not in known:
                tomb.append(p)
                known.add(p)
        reg[_FORGOTTEN] = tomb
    save_registry(reg)
    return True


@_locked
def rename(path: str | Path, new_id: str) -> str | None:
    """Re-key the entry for *path* as *new_id*; returns the id it had, or None if unregistered.

    The registry half of ``navig space rename``. Keys on PATH like ``register`` and
    ``ensure_registered`` do, so an entry whose id had drifted from its manifest is
    corrected rather than missed. Refuses (``ValueError``) when *new_id* already belongs
    to a different path — the one-id-one-space rule that init, wire and doctor enforce on
    the way in must hold on a rename too. The display ``name`` follows the id only when it
    was the same string (a space named after its id keeps that property; a space with its
    own display name keeps that instead). Never adds or drops a row.
    """
    reg = _load_for_mutation()
    if reg is None:
        return None  # registry locked — rename nothing rather than half of it
    rp = _norm(path)
    entry = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == rp), None)
    if entry is None:
        return None
    holder = next(
        (e for e in reg["spaces"] if e.get("id") == new_id and e is not entry), None
    )
    if holder is not None:
        raise ValueError(f"'{new_id}' is already the id of {holder.get('path', '?')}")
    old_id = str(entry.get("id") or "")
    if entry.get("name") in (old_id, None, ""):
        entry["name"] = new_id
    entry["id"] = new_id
    save_registry(reg)
    return old_id


@_locked
def repath(old_path: str | Path, new_path: str | Path) -> bool:
    """Point the row for *old_path* at *new_path*, preserving every other field.

    The registry keys on PATH, so a space whose FOLDER moves (``space rename
    --move-folder``) needs its row carried across — and `forget` + `register` would
    reset the columns that are decisions rather than derivations: ``enabled``,
    ``trusted``, ``last_active``. Refuses (returns False) when *new_path* already has
    a row, so two rows can never claim one folder. The ``active`` pointer is carried
    too when it named the old path.
    """
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked - move nothing rather than half of it
    op, np = _norm(old_path), _norm(new_path)
    if op == np:
        return False
    entry = next((e for e in reg["spaces"] if _norm(e.get("path", "")) == op), None)
    if entry is None:
        return False
    if any(_norm(e.get("path", "")) == np for e in reg["spaces"]):
        return False
    entry["path"] = np
    if _norm(reg.get("active") or "") == op:
        reg["active"] = np
    save_registry(reg)
    return True


def is_enabled(path: str | Path) -> bool:
    """Unknown spaces default to enabled (they get auto-registered on discovery)."""
    e = next((x for x in load_registry()["spaces"] if _norm(x.get("path", "")) == _norm(path)), None)
    return True if e is None else bool(e.get("enabled", True))


def is_trusted(path: str | Path) -> bool | None:
    """Space trust for bundled active capabilities (.navig/plugins, hooks, MCP).

    Returns True/False when the user has decided, None when never asked.
    Unknown spaces are None (untrusted until confirmed) — passive content
    (skills/prompts/personas) does not require trust.
    """
    e = next((x for x in load_registry()["spaces"] if _norm(x.get("path", "")) == _norm(path)), None)
    if e is None or "trusted" not in e:
        return None
    return bool(e.get("trusted"))


@_locked
def set_trusted(id_or_path: str, trusted: bool) -> bool:
    reg = _load_for_mutation()
    if reg is None:
        return False  # registry locked — never silently reset trust decisions
    e = _find(reg, id_or_path)
    if e is None:
        return False
    e["trusted"] = trusted
    save_registry(reg)
    return True


@_locked
def mark_active(path: str | Path) -> None:
    reg = _load_for_mutation()
    if reg is None:
        return  # registry locked — don't lose every space to record an active pointer
    rp = _norm(path)
    reg["active"] = rp
    e = next((x for x in reg["spaces"] if _norm(x.get("path", "")) == rp), None)
    if e is not None:
        e["last_active"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_registry(reg)

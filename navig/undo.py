"""
Undo engine (T-068, plan-evidence-ledger.md) — replay ``undo_data`` of green
operations, safely.

Safety contract (hard rules, in order of enforcement):

1. **Only green operations are undoable** — label computed by
   ``navig.reversibility.classify`` and stored on the ledger entry. Yellow
   gets its compensation hint; red gets an honest refusal.
2. **Idempotent** — an undo is itself recorded (chained, tagged ``undo``,
   ``args.undo_of = <target id>``); a target with a successful undo entry is
   *undone* and refused forever after. Undo entries are capped at yellow so
   they never become candidates themselves.
3. **Drift detection** — before touching anything, the target's CURRENT
   state must equal the state the operation left behind (``undo_data``'s
   "new" side). A config key that changed again, a host that switched again,
   a file whose post-hash no longer matches → refuse with what/why.
4. **Secrets never replay from plaintext** — a sensitive config change
   stores a vault reference, not values; the engine refuses it (and
   double-checks key names as defense in depth).

All refusals raise :class:`UndoRefused` with a user-facing message; the CLI
(`navig undo`) renders them. Nothing here prints.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navig.reversibility import (
    GREEN_CAPABLE_TYPES,
    Reversibility,
    classify,
    compensation_hint,
    is_sensitive_config_key,
)

if TYPE_CHECKING:
    from navig.operation_recorder import OperationRecord, OperationRecorder

logger = logging.getLogger(__name__)

#: How far back the engine looks when scanning for candidates / undo markers.
#: The ledger rotates at 10k entries, so this covers everything retained.
_SCAN_LIMIT = 20_000

#: Sentinel used in undo_data for "the key did not exist before".
ABSENT = None


class UndoRefused(Exception):
    """This operation must not be undone; ``str(exc)`` is the user-facing why."""


@dataclass
class UndoCandidate:
    """One green operation and whether it can be undone right now."""

    record: Any  # OperationRecord
    state: str  # "ready" | "undone" | "drift"
    detail: str = ""  # undone-by id, or the drift reason


# ---------------------------------------------------------------------------
# Ledger scanning
# ---------------------------------------------------------------------------


def effective_label(record: OperationRecord) -> str:
    """The stored label, or (for pre-T-068 entries) the computed one."""
    return record.reversibility or classify(
        record.operation_type.value, record.undo_data or None, record.tags
    ).value


def recent_records(recorder: OperationRecorder) -> list[OperationRecord]:
    """All retained operations, newest first."""
    return list(recorder.iter_operations(limit=_SCAN_LIMIT, reverse=True))


def collect_undone(records: list[OperationRecord]) -> dict[str, str]:
    """Map of target-operation id → the id of the SUCCESSFUL undo that undid it.

    Failed undo attempts do not count — the target stays undoable.
    """
    from navig.operation_recorder import OperationStatus

    undone: dict[str, str] = {}
    for rec in records:
        target = (rec.args or {}).get("undo_of")
        if target and rec.status == OperationStatus.SUCCESS:
            undone.setdefault(str(target), rec.id)
    return undone


def find_candidates(recorder: OperationRecorder, limit: int = 10) -> list[UndoCandidate]:
    """The most recent green operations with their current undo state."""
    from navig.operation_recorder import OperationStatus

    records = recent_records(recorder)
    undone = collect_undone(records)
    out: list[UndoCandidate] = []
    for rec in records:
        if len(out) >= limit:
            break
        if rec.status != OperationStatus.SUCCESS:
            continue
        if rec.tags and "undo" in rec.tags:
            continue
        if effective_label(rec) != Reversibility.GREEN.value:
            continue
        if rec.id in undone:
            out.append(UndoCandidate(rec, "undone", f"undone by {undone[rec.id]}"))
            continue
        try:
            check_drift(rec)
        except UndoRefused as exc:
            out.append(UndoCandidate(rec, "drift", str(exc)))
            continue
        out.append(UndoCandidate(rec, "ready"))
    return out


def latest_ready(recorder: OperationRecorder) -> OperationRecord | None:
    """The most recent green operation that can be undone right now."""
    for cand in find_candidates(recorder, limit=50):
        if cand.state == "ready":
            return cand.record
    return None


# ---------------------------------------------------------------------------
# Refusal checks
# ---------------------------------------------------------------------------


def ensure_undoable(record: OperationRecord, undone: dict[str, str]) -> None:
    """Every reason an operation may never be undone, checked in order."""
    from navig.operation_recorder import OperationStatus

    label = effective_label(record)
    op_type = record.operation_type.value

    if record.tags and "undo" in record.tags:
        raise UndoRefused(
            f"{record.id} is itself an undo — re-run the original command to redo instead"
        )
    if record.id in undone:
        raise UndoRefused(f"{record.id} was already undone by {undone[record.id]}")
    if (record.undo_data or {}).get("sensitive"):
        ref = (record.undo_data or {}).get("vault_ref")
        hint = f" (vault: {ref})" if ref else ""
        raise UndoRefused(
            "secret-bearing key — plaintext is never stored in the ledger, so it "
            f"cannot be replayed automatically; restore it manually{hint}"
        )
    if label != Reversibility.GREEN.value:
        hint = compensation_hint(op_type)
        extra = f" — compensation: {hint}" if hint else ""
        raise UndoRefused(f"{record.id} is labeled {label}, not green (undoable){extra}")
    if record.status != OperationStatus.SUCCESS:
        raise UndoRefused(f"{record.id} did not succeed ({record.status.value}) — nothing to undo")
    if not record.undo_data:
        raise UndoRefused(f"{record.id} has no captured undo data")
    if op_type not in GREEN_CAPABLE_TYPES:
        raise UndoRefused(f"no undo strategy for operation type '{op_type}'")
    # Defense in depth: never replay plaintext for a key that NAMES a secret,
    # even if a capture site forgot to mark it sensitive.
    key = (record.undo_data or {}).get("key")
    if op_type == "config_change" and key and is_sensitive_config_key(str(key)):
        raise UndoRefused(
            f"config key '{key}' names secret material — automatic undo is disabled for it"
        )


def check_drift(record: OperationRecord) -> None:
    """Refuse when the target's CURRENT state is not the state this operation left.

    Compares against ``undo_data``'s "new" side where a cheap comparison
    exists (config value, active host, file post-hash); refuses honestly when
    the recorded data predates post-state fingerprinting.
    """
    op_type = record.operation_type.value
    data = record.undo_data or {}

    if op_type == "config_change":
        key = data.get("key")
        if not key:
            raise UndoRefused("undo data is missing the config key")
        exists, current = _current_config_value(str(key))
        expected = data.get("new_value")
        if not exists:
            raise UndoRefused(f"config key '{key}' no longer exists — it changed since; not undoing")
        if current != expected:
            raise UndoRefused(
                f"config key '{key}' changed since this operation "
                f"(now {current!r}, this operation set {expected!r}) — not undoing"
            )
        return

    if op_type == "host_switch":
        new_host = data.get("new_host")
        _, current = _current_config_value("active_host")
        if new_host and current != new_host:
            raise UndoRefused(
                f"active host changed since this operation (now {current!r}, "
                f"this operation set {new_host!r}) — not undoing"
            )
        if not data.get("previous_host"):
            raise UndoRefused("undo data has no previous host to switch back to")
        return

    if op_type in {"file_create", "file_modify"}:
        if "file_history_backup" in data:
            backup = Path(str(data["file_history_backup"]))
            if not backup.is_file():
                raise UndoRefused(f"backup no longer exists: {backup}")
            path = data.get("path")
            after = data.get("after_sha256")
            if not path or not after:
                raise UndoRefused(
                    "recorded before post-state fingerprinting — restore manually "
                    f"from the backup: {backup}"
                )
            target = Path(str(path))
            if not target.is_file():
                raise UndoRefused(f"{target} no longer exists — it changed since; not undoing")
            if hashlib.sha256(target.read_bytes()).hexdigest() != after:
                raise UndoRefused(f"{target} changed since this operation — not undoing")
            return
        if op_type == "file_create" and data.get("file_path"):
            target = Path(str(data["file_path"]))
            if not target.is_file():
                raise UndoRefused(f"{target} no longer exists — nothing to undo")
            after = data.get("after_sha256")
            if after and hashlib.sha256(target.read_bytes()).hexdigest() != after:
                raise UndoRefused(f"{target} changed since this operation — not undoing")
            return
        raise UndoRefused("undo data carries no usable file backup")

    if op_type == "file_delete":
        backup = data.get("backup_path")
        original = data.get("original_path")
        if not backup or not original:
            raise UndoRefused("undo data is missing the backup or original path")
        if not Path(str(backup)).is_file():
            raise UndoRefused(f"backup no longer exists: {backup}")
        if Path(str(original)).exists():
            raise UndoRefused(f"{original} was recreated since — not overwriting it")
        return

    raise UndoRefused(f"no drift check for operation type '{op_type}'")


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


def describe_undo(record: OperationRecord) -> str:
    """What :func:`perform_undo` WILL do, in one plain sentence (no side effects).

    Shown in the confirm gate before anything is touched.
    """
    op_type = record.operation_type.value
    data = record.undo_data or {}

    if op_type == "config_change":
        key = data.get("key")
        old_exists = bool(data.get("old_exists", data.get("old_value") is not None))
        if old_exists:
            return f"restore config '{key}' to {data.get('old_value')!r}"
        return f"remove config '{key}' (it did not exist before)"
    if op_type == "host_switch":
        return f"switch active host back to '{data.get('previous_host')}'"
    if op_type in {"file_create", "file_modify"}:
        if "file_history_backup" in data:
            return f"restore {data.get('path')} from backup {data.get('file_history_backup')}"
        return f"delete created file {data.get('file_path')}"
    if op_type == "file_delete":
        return f"restore {data.get('original_path')} from backup {data.get('backup_path')}"
    return f"(no undo strategy for '{op_type}')"


def perform_undo(record: OperationRecord) -> dict[str, Any]:
    """Replay ``record.undo_data``. Returns the redo material (swapped undo_data).

    Callers run :func:`ensure_undoable` + :func:`check_drift` first; this
    function assumes both passed and performs the smallest possible write.
    """
    op_type = record.operation_type.value
    data = record.undo_data or {}

    if op_type == "config_change":
        key = str(data["key"])
        old_exists = bool(data.get("old_exists", data.get("old_value") is not None))
        old_value = data.get("old_value")
        _restore_config_value(key, old_value, old_exists)
        return {
            "key": key,
            "old_value": data.get("new_value"),
            "old_exists": True,
            "new_value": old_value,
            "new_exists": old_exists,
            "scope": data.get("scope", "global"),
        }

    if op_type == "host_switch":
        previous = str(data["previous_host"])
        from navig.config import get_config_manager

        cm = get_config_manager()
        cm.set_active_host(previous, local=False)
        cm.update_global_config({"active_host": previous})
        return {"previous_host": data.get("new_host"), "new_host": previous}

    if op_type in {"file_create", "file_modify"}:
        if "file_history_backup" in data:
            backup = Path(str(data["file_history_backup"]))
            target = Path(str(data["path"]))
            shutil.copy2(backup, target)
            return {"path": str(target), "restored_from": str(backup)}
        target = Path(str(data["file_path"]))
        target.unlink()
        return {"deleted": str(target)}

    if op_type == "file_delete":
        backup = Path(str(data["backup_path"]))
        original = Path(str(data["original_path"]))
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, original)  # copy, not move — the backup stays as evidence
        return {"path": str(original), "restored_from": str(backup)}

    raise UndoRefused(f"no undo strategy for operation type '{op_type}'")


# ---------------------------------------------------------------------------
# Config plumbing (call-time resolved; the ONE writer pattern from set_global)
# ---------------------------------------------------------------------------


def _current_config_value(key: str) -> tuple[bool, Any]:
    """(exists, value) for a dotted global-config *key*, read fresh from disk."""
    from navig.config import get_config_manager

    node: Any = get_config_manager().refresh_global_config()
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False, None
    return True, node


def _restore_config_value(key: str, value: Any, exists: bool) -> None:
    """Deep-set (or deep-delete when *exists* is False) a dotted global key.

    Mirrors ``ConfigManager.set_global``: refresh BEFORE mutate so the save
    writes current state, not a stale snapshot (the lost-update trap).
    """
    from navig.config import get_config_manager

    cm = get_config_manager()
    cfg = cm.refresh_global_config()
    parts = key.split(".")
    node = cfg
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            if not exists:
                return  # nothing to delete; the whole branch is already gone
            child = {}
            node[part] = child
        node = child
    if exists:
        node[parts[-1]] = value
    else:
        node.pop(parts[-1], None)
    cm._save_global_config(cfg)

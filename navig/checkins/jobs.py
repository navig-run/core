"""The ``checkin:*`` cron jobs — and the one-time move off the old weigh-in jobs.

Every check-in fires through jobs named ``checkin:<id>[:<slot>]`` whose command is
``NAVIG_CHECKIN:<id>:<chat>[:<slot>]`` — handled IN the scheduler's process by
:func:`navig.checkins.engine.run_command`, so a run costs milliseconds instead of a
CLI cold start. :data:`JOB_TIMEOUT` is 180 s, and every run marks "asked" before it
sends, so even a run killed at the ceiling never asks twice.

Jobs written per template:

* pills  — ``checkin:<id>:<HHMM>`` at each time, plus ``checkin:<id>:<HHMM>:nudge``
  ``missed_after`` minutes later (the single missed-dose nudge);
* water  — ``checkin:water:<HHMM>`` every N hours within the window;
* weigh  — ``checkin:weigh`` (daily ask) and ``checkin:weigh:weekly`` (the Sunday
  card), created by :func:`migrate_legacy_jobs` from the jobs that already exist.

Writes go through :mod:`navig.scheduler.habit_store`'s resolution order: the live
service in the gateway, the gateway over HTTP from the CLI, a detached service when
the daemon is down.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.checkins import cadence
from navig.checkins import defs as d
from navig.checkins.engine import COMMAND_PREFIX, NUDGE, WEEKLY, build_command

JOB_PREFIX = "checkin:"
JOB_TIMEOUT = 180

#: The legacy weigh-in jobs this migration knows by name.
LEGACY_NAMES = {"habit:weigh": "weigh", "habit:bodyreview": WEEKLY}


def job_name(item_id: str, slot: str | None = None, action: str | None = None) -> str:
    parts = [item_id] + ([slot] if slot else []) + ([action] if action else [])
    return JOB_PREFIX + ":".join(parts)


def owns(name: str, item_id: str) -> bool:
    return name == f"{JOB_PREFIX}{item_id}" or name.startswith(f"{JOB_PREFIX}{item_id}:")


def job_specs(item: d.CheckinItem, chat: int) -> list[dict[str, Any]]:
    """The jobs *item* should have — ``[{name, schedule, command}]``."""
    specs: list[dict[str, Any]] = []
    if item.template == "pills":
        for slot in item.slots():
            specs.append(
                {
                    "name": job_name(item.id, slot),
                    "schedule": cadence.slot_cron(slot),
                    "command": build_command(item.id, chat, slot),
                }
            )
            specs.append(
                {
                    "name": job_name(item.id, slot, NUDGE),
                    "schedule": cadence.slot_cron(slot, plus_minutes=item.missed_after),
                    "command": build_command(item.id, chat, slot, NUDGE),
                }
            )
    elif item.template == "water":
        for slot in item.slots():
            specs.append(
                {
                    "name": job_name(item.id, slot),
                    "schedule": cadence.slot_cron(slot),
                    "command": build_command(item.id, chat, slot),
                }
            )
    return specs


def sync_jobs(item: d.CheckinItem, chat: int) -> list[dict[str, Any]]:
    """Make the scheduler hold exactly *item*'s jobs. Returns the specs written."""
    from navig.scheduler import habit_store  # noqa: PLC0415

    remove_jobs(item.id)
    specs = job_specs(item, chat)
    for spec in specs:
        habit_store.create_job(
            spec["name"],
            spec["schedule"],
            spec["command"],
            enabled=item.enabled,
            timeout_seconds=JOB_TIMEOUT,
        )
    return specs


def remove_jobs(item_id: str) -> int:
    from navig.scheduler import habit_store  # noqa: PLC0415

    removed = 0
    for job in habit_store.list_jobs():
        name = str(job.get("name") or "")
        if owns(name, item_id):
            removed += habit_store.delete_jobs(name)
    return removed


def set_jobs_enabled(item_id: str, enabled: bool) -> int:
    """Pause/resume the jobs too (the engine also checks ``enabled`` at run time)."""
    from navig.scheduler import habit_store  # noqa: PLC0415

    return len(habit_store.set_named_jobs_enabled(lambda n: owns(n, item_id), enabled=enabled))


# ── the legacy weigh-in jobs ──────────────────────────────────────────────────


def _default_chat() -> int | None:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        allowed = get_config_manager().get("telegram.allowed_users", default=[]) or []
        return int(allowed[0]) if allowed else None
    except Exception:  # noqa: BLE001
        return None


def _legacy_kind(job: dict[str, Any]) -> str | None:
    """``weigh`` / ``weekly`` for an old weigh-in job, else None."""
    name = str(job.get("name") or "")
    command = str(job.get("command") or "")
    if command.startswith(COMMAND_PREFIX):
        return None  # already the new form
    if name in LEGACY_NAMES:
        return LEGACY_NAMES[name]
    if command.startswith("navig body checkin") and "--send" in command:
        return WEEKLY if "--weekly" in command else "weigh"
    return None


def _flag(argv: list[str], flag: str) -> str | None:
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def plan_legacy_migration(data: dict[str, Any], *, default_chat: int | None) -> dict[str, Any]:
    """Pure: what :func:`migrate_legacy_jobs` would do to the store *data*."""
    jobs = [j for j in data.get("jobs") or [] if isinstance(j, dict)]
    existing = {str(j.get("name") or "") for j in jobs}
    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    spaces: set[str] = set()
    new_jobs: list[dict[str, Any]] = []
    for job in jobs:
        kind = _legacy_kind(job)
        if kind is None:
            new_jobs.append(job)
            continue
        command = str(job.get("command") or "")
        # Split like the platform's shell: on Windows an unquoted --space path keeps
        # its backslashes (POSIX shlex would eat them and pin the wrong directory).
        from navig.scheduler.cron_service import split_exec_command  # noqa: PLC0415

        try:
            argv = split_exec_command(command)
        except ValueError:
            argv = command.split()
        chat_raw = _flag(argv, "--chat-id")
        chat = int(chat_raw) if chat_raw and chat_raw.lstrip("-").isdigit() else default_chat
        if chat is None:
            skipped.append({"id": job.get("id"), "name": job.get("name"), "reason": "no chat id"})
            new_jobs.append(job)
            continue
        space = _flag(argv, "--space")
        if space:
            spaces.add(space)
        target = job_name(d.WEIGH_ID, WEEKLY if kind == WEEKLY else None)
        if target in existing and target != job.get("name"):
            # The new job already exists (an earlier, partial run): the old one is a
            # duplicate that would ask a second time — remove it.
            changes.append(
                {"id": job.get("id"), "action": "remove-duplicate", "from": _brief(job), "to": None}
            )
            continue
        new = dict(job)
        new["name"] = target
        new["command"] = build_command(d.WEIGH_ID, chat, WEEKLY if kind == WEEKLY else None)
        new["timeout_seconds"] = JOB_TIMEOUT
        new["retry_count"] = 0
        existing.add(target)
        new_jobs.append(new)
        changes.append(
            {"id": job.get("id"), "action": "convert", "from": _brief(job), "to": _brief(new)}
        )
    return {
        "changes": changes,
        "skipped": skipped,
        "spaces": sorted(spaces),
        "jobs": new_jobs,
    }


def _brief(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": job.get("name"),
        "schedule": job.get("schedule"),
        "command": job.get("command"),
        "timeout_seconds": job.get("timeout_seconds"),
        "enabled": job.get("enabled", True),
    }


def migrate_legacy_jobs(
    store_path: Path,
    *,
    dry_run: bool = False,
    default_chat: int | None = None,
    persist_space: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Convert the old weigh-in jobs in *store_path* to ``checkin:weigh[:weekly]``.

    Idempotent: a second run finds nothing to change and writes nothing. Before
    any write the store is copied to ``cron_jobs.json.bak-<timestamp>`` beside it.
    ``dry_run`` returns the planned diff and touches nothing.

    ⚠ The running scheduler re-reads the store when its mtime changes, so this is
    the same "external edit" the ``navig cron`` CLI makes — but stop the daemon
    first if you can, so no scheduler save lands between our read and our write.
    """
    from navig.core.json_io import atomic_write_json, load_json_for_update  # noqa: PLC0415

    store_path = Path(store_path)
    if not store_path.exists():
        return {
            "ok": True,
            "dry_run": dry_run,
            "changes": [],
            "skipped": [],
            "backup": None,
            "store": str(store_path),
            "note": "no job store",
        }
    data = load_json_for_update(store_path, default={})
    chat = default_chat if default_chat is not None else _default_chat()
    plan = plan_legacy_migration(data, default_chat=chat)
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "store": str(store_path),
        "changes": plan["changes"],
        "skipped": plan["skipped"],
        "spaces": plan["spaces"],
        "backup": None,
    }
    if dry_run or not plan["changes"]:
        return result
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%S")
    backup = store_path.with_name(f"{store_path.name}.bak-{stamp}")
    shutil.copy2(store_path, backup)
    result["backup"] = str(backup)
    out = dict(data)
    out["jobs"] = plan["jobs"]
    atomic_write_json(out, store_path)
    # Read back: a write that did not land must not be reported as a migration.
    reread = json.loads(store_path.read_text(encoding="utf-8"))
    if plan_legacy_migration(reread, default_chat=chat)["changes"]:
        raise RuntimeError(f"migration did not persist — the backup is {backup}")
    if persist_space and len(plan["spaces"]) == 1:
        _remember_weigh_space(plan["spaces"][0])
    return result


def _remember_weigh_space(space: str) -> None:
    """Carry the old job's ``--space`` into ``checkins.weigh.space`` (when unset)."""
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        cm = get_config_manager()
        if not cm.get(f"{d.CONFIG_KEY}.{d.WEIGH_ID}.space", default=None):
            cm.set_global(f"{d.CONFIG_KEY}.{d.WEIGH_ID}.space", space)
    except Exception:  # noqa: BLE001 — the chat's pinned target still resolves it
        pass

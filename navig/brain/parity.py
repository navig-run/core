"""Brain parity — what the source runs that the target must also run.

A migration that copies the Brain's files but leaves a plugin, a scheduled job or a
space behind still *looks* finished: the lease moves, ``brain status`` is green, and
the missing work simply stops. That is how the 2026-10 NEURON → OUTPOST move lost the
Microsoft Store sale pings (``navig-msstore`` never installed) and the paperwork jobs
(``company-paperwork`` space never copied) for days without a single error.

This module turns that into a check. :func:`local_inventory` describes the source,
the target produces the same shape (see ``SshRemote.validate``), and :func:`reconcile`
lists every gap. Only gaps that silently stop work block a migration:

* an **enabled job** the target does not have (matched by name — ids differ per machine);
* a **space** an enabled job runs in, missing on the target;
* a **Brain-duty plugin** — one whose gateway loop does work nobody else does
  (:data:`BRAIN_DUTY_PLUGINS`, extended by config ``brain.migrate.required_plugins``)
  — that the source has enabled and the target does not.

Other missing plugins are reported, not blocking: almost every plugin registers a
gateway hook (routes, an app tile), so "has a hook" would flag the whole desktop.

Jobs that can only run on the source platform (a Windows drive path in the command)
are reported as ``platform_local`` instead of blocking: they belong where they are.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

GATEWAY_GROUP = "navig.plugins"

# Plugins whose gateway loop is the only thing doing its job: the Store sales poller
# and digest, the mail watcher, scheduled social publishing, document renewals.
BRAIN_DUTY_PLUGINS: frozenset[str] = frozenset(
    {"navig-msstore", "navig-email", "navig-social", "navig-cabinet", "navig-telegram"}
)

# A Windows drive path anywhere in a command (``C:\...`` or ``c:/...``).
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")


# ── inventory ──────────────────────────────────────────────────────────────
def jobs_from_store(data: Any) -> list[dict[str, Any]]:
    """The jobs of a ``cron_jobs.json`` payload, tolerant of a bare list."""
    if isinstance(data, dict):
        data = data.get("jobs")
    if not isinstance(data, list):
        return []
    return [j for j in data if isinstance(j, dict) and j.get("name")]


def plugins_from_list(data: Any) -> list[str]:
    """Enabled plugin ids from a ``navig plugin list --json`` payload."""
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    rows = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return sorted(
        str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id") and r.get("enabled", True)
    )


def gateway_plugin_ids() -> set[str]:
    """Distribution names that register a gateway hook on THIS machine."""
    from importlib import metadata  # noqa: PLC0415

    out: set[str] = set()
    for ep in metadata.entry_points(group=GATEWAY_GROUP):
        dist = getattr(ep, "dist", None)
        if dist is not None and getattr(dist, "name", None):
            out.add(str(dist.name))
    return out


def local_inventory(config_dir: Path | None = None) -> dict[str, Any]:
    """The source side: enabled jobs, installed plugins, spaces on disk."""
    if config_dir is None:
        from navig.platform import paths  # noqa: PLC0415

        config_dir = paths.config_dir()
    try:
        store = json.loads((config_dir / "scheduler" / "cron_jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        store = {}
    try:
        from navig.plugins.host import get_plugin_host  # noqa: PLC0415

        plugins = sorted(p.id for p in get_plugin_host().list_installed(refresh=True) if p.enabled)
    except Exception:  # noqa: BLE001 — no plugin evidence is reported, never a crash
        plugins = []
    spaces_root = config_dir / "spaces"
    spaces = sorted(p.name for p in spaces_root.iterdir() if p.is_dir()) if spaces_root.is_dir() else []
    try:
        from navig.brain.capabilities import config_section  # noqa: PLC0415

        extra = config_section("brain", "migrate").get("required_plugins") or []
    except Exception:  # noqa: BLE001
        extra = []
    return {
        "jobs": jobs_from_store(store),
        "plugins": plugins,
        "gateway_plugins": sorted(gateway_plugin_ids()),
        "required_plugins": sorted(BRAIN_DUTY_PLUGINS | {str(x) for x in extra if x}),
        "spaces": spaces,
    }


# ── comparison ─────────────────────────────────────────────────────────────
def job_space(command: str) -> str | None:
    """The space a job command runs in (``--space NAME`` or a path to it)."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = command.split()
    for i, tok in enumerate(argv):
        value = None
        if tok == "--space" and i + 1 < len(argv):
            value = argv[i + 1]
        elif tok.startswith("--space="):
            value = tok.split("=", 1)[1]
        if value:
            return re.split(r"[\\/]", value.rstrip("\\/"))[-1] or None
    return None


def _space_present(name: str, spaces: set[str]) -> bool:
    return name in spaces or f"{name}-space" in spaces or name.removesuffix("-space") in spaces


def is_platform_local(job: dict[str, Any]) -> bool:
    return bool(_WINDOWS_PATH.search(str(job.get("command") or "")))


def reconcile(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Every gap between *source* and *target*; ``unreconciled`` blocks a migration."""
    unreconciled: list[str] = []
    notes: list[str] = []
    platform_local: list[str] = []

    target_jobs = {str(j["name"]) for j in jobs_from_store(target.get("jobs"))}
    target_spaces = {str(s) for s in target.get("spaces") or []}
    enabled_jobs = [j for j in jobs_from_store(source.get("jobs")) if j.get("enabled", True)]

    missing_jobs: list[str] = []
    missing_spaces: dict[str, list[str]] = {}
    for job in enabled_jobs:
        name = str(job["name"])
        if name not in target_jobs:
            if is_platform_local(job) and job_space(str(job.get("command") or "")) is None:
                platform_local.append(name)
                continue
            missing_jobs.append(name)
        space = job_space(str(job.get("command") or ""))
        if space and not _space_present(space, target_spaces):
            missing_spaces.setdefault(space, []).append(name)

    if missing_jobs:
        unreconciled.append("jobs missing on the target: " + ", ".join(sorted(missing_jobs)))
    for space, users in sorted(missing_spaces.items()):
        unreconciled.append(f"space '{space}' missing on the target (used by {', '.join(sorted(users))})")

    source_plugins = {str(p) for p in source.get("plugins") or []}
    target_plugins = {str(p) for p in target.get("plugins") or []}
    required = {str(p) for p in source.get("required_plugins") or BRAIN_DUTY_PLUGINS}
    missing_plugins = source_plugins - target_plugins
    missing_gateway = sorted(missing_plugins & required)
    if missing_gateway:
        unreconciled.append(
            "Brain-duty plugins missing on the target: " + ", ".join(missing_gateway)
        )
    desktop_only = sorted(missing_plugins - required)
    if desktop_only:
        notes.append(f"{len(desktop_only)} other plugins stay on the source (not Brain duties)")
    if platform_local:
        notes.append("platform-local jobs stay on the source: " + ", ".join(sorted(platform_local)))

    reconciled = [
        k
        for k, bad in (
            ("jobs", bool(missing_jobs)),
            ("spaces", bool(missing_spaces)),
            ("plugins", bool(missing_gateway)),
        )
        if not bad
    ]
    return {
        "ok": not unreconciled,
        "reconciled": reconciled,
        "unreconciled": unreconciled,
        "notes": notes,
        "platform_local_jobs": sorted(platform_local),
        "desktop_only_plugins": desktop_only,
        "item_counts": {
            "jobs": len(enabled_jobs),
            "target_jobs": len(target_jobs),
            "plugins": len(source_plugins),
            "target_plugins": len(target_plugins),
            "spaces": len(source.get("spaces") or []),
            "target_spaces": len(target_spaces),
        },
    }

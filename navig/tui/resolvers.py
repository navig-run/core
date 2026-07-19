"""
navig.tui.resolvers — Synchronous status resolvers for the NAVIG dashboard.

Each resolver reads local state files / config only.  No async, no network
calls, no subprocess.  All must complete in <100 ms on a cold local disk.

StatusBadge.deep_link is the /settings/* route that repairs this section.
An empty string means no settings panel is available (read-only info).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from navig.platform.paths import builtin_store_dir, config_dir

# ---------------------------------------------------------------------------
# StatusBadge
# ---------------------------------------------------------------------------


@dataclass
class StatusBadge:
    """Lightweight health record for one NAVIG sub-system."""

    label: str
    status: str  # "ok" | "warn" | "error" | "missing"
    detail: str = ""
    icon: str = ""
    deep_link: str = ""  # /settings/<section> — empty = no settings panel

    @property
    def color(self) -> str:
        return {
            "ok": "#10b981",
            "warn": "#f59e0b",
            "error": "#ef4444",
            "missing": "#64748b",
        }.get(self.status, "#64748b")

    @property
    def symbol(self) -> str:
        return self.icon or {
            "ok": "●",
            "warn": "◑",
            "error": "✖",
            "missing": "○",
        }.get(self.status, "?")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

from navig.tui.config_model import load_navig_json as _load_navig_json  # noqa: PLC0415

# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def resolve_provider() -> StatusBadge:
    """Check AI provider configuration."""
    nj = _load_navig_json()
    provider_hint = "—"
    if nj:
        try:
            provider_hint = nj.get("agents", {}).get("defaults", {}).get("model", "—")
        except Exception:  # noqa: BLE001
            pass
    try:
        from navig.settings.resolver import get as _sget

        resolved = _sget("navig.ai.provider", "")
    except Exception:  # noqa: BLE001
        resolved = ""
    if resolved or (nj and provider_hint != "—"):
        return StatusBadge(
            "AI Provider",
            "ok",
            resolved or provider_hint,
            deep_link="/settings/vault",
        )
    return StatusBadge(
        "AI Provider",
        "missing",
        "navig init --provider",
        deep_link="/settings/vault",
    )


def resolve_telegram() -> StatusBadge:
    """Check Telegram bot configuration."""
    try:
        from navig.messaging.secrets import resolve_telegram_bot_token

        # The token lives in the vault (vault-first) or under telegram.bot_token in
        # config — NOT a flat TELEGRAM_BOT_TOKEN config key. resolve_telegram_bot_token()
        # is the one canonical resolver (vault → legacy → env → config); the old flat
        # read always returned "" so the badge said "missing" even when the bot was set up.
        if resolve_telegram_bot_token():
            return StatusBadge(
                "Telegram",
                "ok",
                "configured",
                deep_link="/settings/gateway",
            )
    except Exception:  # noqa: BLE001
        pass
    return StatusBadge(
        "Telegram",
        "missing",
        "Optional • navig bot setup",
        deep_link="/settings/gateway",
    )


def resolve_ssh() -> StatusBadge:
    """Check whether any SSH hosts are configured."""
    try:
        from navig.core import Config

        # Hosts are per-host YAML files under config_dir()/hosts (+ legacy apps/),
        # NOT a `hosts:` key in config.yaml. Config.list_hosts() is the same source
        # `navig host list` uses; the old config.yaml read found nothing, so the
        # badge always said "missing" even with hosts configured.
        hosts = Config().list_hosts()
        if hosts:
            count = len(hosts)
            return StatusBadge(
                "SSH Keys",
                "ok",
                f"{count} host{'s' if count != 1 else ''} active",
            )
    except Exception:  # noqa: BLE001
        pass
    return StatusBadge("SSH Keys", "missing", "navig host add")


def resolve_daemon() -> StatusBadge:
    """Check whether the NAVIG daemon process is running."""
    try:
        pid_file = config_dir() / "daemon" / "supervisor.pid"
        if pid_file.is_file():
            from navig.platform.windows_utils import check_pid_exists  # noqa: PLC0415

            pid = int(pid_file.read_text(encoding="utf-8").strip())
            # NB: os.kill(pid, 0) TERMINATES the process on Windows — use a probe.
            if check_pid_exists(pid):
                return StatusBadge("Daemon", "ok", f"pid {pid}")
    except (ValueError, OSError, ProcessLookupError, PermissionError):
        pass  # best-effort: skip on process/IO error
    return StatusBadge(
        "Daemon",
        "missing",
        "not installed → navig daemon start",
        deep_link="",
    )


def resolve_vault() -> StatusBadge:
    """Check vault initialisation state."""
    try:
        from navig.vault.manager import VaultManager  # type: ignore[import]

        vm = VaultManager()
        vm.list()
        return StatusBadge("Vault", "ok", "encrypted ✓", deep_link="/settings/vault")
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)[:50] if str(exc) else "locked or missing"
        return StatusBadge(
            "Vault",
            "warn",
            detail,
            deep_link="/settings/vault",
        )


# ---------------------------------------------------------------------------
# New resolvers (added per spec)
# ---------------------------------------------------------------------------


def resolve_agent() -> StatusBadge:
    """Check active agent config (soul.json / agent.json)."""
    try:
        from navig.agent_config_loader import load_agent_json

        cfg = load_agent_json("navig")
        if cfg:
            mode = cfg.llm_mode or "auto"
            name = cfg.name or cfg.id or "navig"
            detail = f"{name} / {mode}"
            # Check soul.json as a secondary signal. This used to also probe
            # `Path("store/agents/navig/soul.json")` — a CWD-RELATIVE path, so the badge
            # said different things depending on which directory you happened to run from,
            # and it pointed at the pre-migration `<repo>/core/store/` layout that no longer
            # exists (the builtin store now lives inside the package). Two real locations
            # remain: the user's own agents dir, and the seed shipped in the wheel.
            soul_ok = (config_dir() / "agents" / "navig" / "soul.json").is_file() or (
                builtin_store_dir() / "agents" / "navig" / "soul.json"
            ).is_file()
            soul_indicator = " soul.json ✓" if soul_ok else ""
            return StatusBadge(
                "Agent",
                "ok",
                detail + soul_indicator,
                deep_link="/settings/agents",
            )
        return StatusBadge(
            "Agent",
            "missing",
            "navig init",
            deep_link="/settings/agents",
        )
    except Exception:  # noqa: BLE001
        return StatusBadge(
            "Agent",
            "warn",
            "config unavailable",
            deep_link="/settings/agents",
        )


def resolve_gateway() -> StatusBadge:
    """Check gateway channel health (configured channels + blackbox errors)."""
    try:
        from navig.messaging.channel_config import configured_channels

        # The old code read a `channels` map from navig.json — a field nothing
        # populates, so the badge ALWAYS said "no channels configured". Real channel
        # config lives in config.yaml sections (telegram/discord/…); configured_channels()
        # is the shared source `navig gateway status` uses.
        active = configured_channels()

        if not active:
            return StatusBadge(
                "Gateway",
                "missing",
                "no channels configured",
                deep_link="/settings/gateway",
            )

        # Check blackbox for recent gateway errors
        error_count = _count_recent_errors("gateway", window_seconds=3600)

        channel_str = ", ".join(active[:2]) + ("…" if len(active) > 2 else "")
        if error_count > 0:
            return StatusBadge(
                "Gateway",
                "warn",
                f"{channel_str} — {error_count} error{'s' if error_count != 1 else ''} in last hour",
                deep_link="/settings/gateway",
            )
        return StatusBadge(
            "Gateway",
            "ok",
            channel_str,
            deep_link="/settings/gateway",
        )
    except Exception:  # noqa: BLE001
        return StatusBadge(
            "Gateway",
            "missing",
            "navig bot setup",
            deep_link="/settings/gateway",
        )


def resolve_mesh() -> StatusBadge:
    """Check mesh node topology (read-only, no settings panel)."""
    try:
        from navig.mesh.registry import get_registry  # type: ignore[import]

        registry = get_registry()
        # get_peers() = remote nodes only (excludes self). The old code called a
        # non-existent registry.list_nodes(), guarded by hasattr, so it silently
        # returned [] — node_count was ALWAYS 0 and the badge always read
        # "single-node mode", even on a live multi-node mesh.
        peers = registry.get_peers()
        if not peers:
            return StatusBadge("Mesh", "missing", "no peers — single-node mode")

        node_count = len(registry.get_all())  # peers + self
        # Read the elected leader from the registry — the same source of truth
        # ElectionManager uses (registry.get_leader()); no live manager needed.
        # (The old code imported a phantom navig.mesh.election.get_current_leader,
        # so the leader was always "—".)
        leader = registry.get_leader()
        leader_name = (leader.hostname or leader.node_id) if leader else "—"

        return StatusBadge(
            "Mesh",
            "ok",
            f"{node_count} node{'s' if node_count != 1 else ''} — leader: {leader_name}",
        )
    except Exception:  # noqa: BLE001
        return StatusBadge("Mesh", "missing", "single-node mode")


def resolve_scheduler() -> StatusBadge:
    """Check cron scheduler state."""
    try:

        from navig.scheduler.cron_service import CronService  # type: ignore[import]

        # The daemon + CLI store cron jobs under config_dir()/scheduler (see
        # deck/routes/schedule.py, gateway/server.py, scheduler/habit_store) — NOT
        # config_dir() itself. Reading the parent found no cron_jobs.json, so the
        # badge always read "no jobs configured" even with jobs scheduled.
        svc = CronService(gateway=None, storage_path=config_dir() / "scheduler")
        jobs = svc.list_jobs()
        count = len(jobs) if jobs else 0

        if count == 0:
            return StatusBadge(
                "Scheduler",
                "missing",
                "no jobs configured",
                deep_link="/settings/scheduler",
            )

        # Find the soonest upcoming run. CronJob exposes `next_run` (a datetime),
        # NOT `next_fire` (a unix ts) — the old code read a phantom attribute, so
        # `upcoming` was always empty and the "next: …" hint never showed (and the
        # `datetime - time.time()` math would have been a type error if reached).
        next_label = ""
        try:
            from datetime import datetime

            upcoming = sorted(
                (j for j in jobs if getattr(j, "next_run", None)),
                key=lambda j: j.next_run,
            )
            if upcoming:
                job = upcoming[0]
                name = getattr(job, "name", "job")
                delta = max(int((job.next_run - datetime.now()).total_seconds()), 0)
                if delta < 60:
                    next_label = f" — next: {name} in {delta}s"
                elif delta < 3600:
                    next_label = f" — next: {name} in {delta // 60}m"
                else:
                    next_label = f" — next: {name} in {delta // 3600}h"
        except Exception:  # noqa: BLE001
            pass

        return StatusBadge(
            "Scheduler",
            "ok",
            f"{count} job{'s' if count != 1 else ''}{next_label}",
            deep_link="/settings/scheduler",
        )
    except Exception:  # noqa: BLE001
        return StatusBadge(
            "Scheduler",
            "missing",
            "navig cron list",
            deep_link="/settings/scheduler",
        )


def resolve_task_queue() -> StatusBadge:
    """Check task queue depth (reads the daemon's persisted queue)."""
    try:
        from navig.tasks.queue import TaskQueue  # type: ignore[import]

        # Read the file the gateway persists to (storage_dir/task_queue.json;
        # storage_dir defaults to config_dir()). A bare TaskQueue() is an empty
        # throwaway that always reads 0 — and `size`/`total` are properties, so the
        # old q.pending_count() never existed: the badge was permanently "0 pending".
        q = TaskQueue(persist_path=str(config_dir() / "task_queue.json"))
        pending = q.size
        tracked = q.total

        detail = f"{pending} pending"
        if tracked > pending:
            detail += f" — {tracked} tracked"

        status = "warn" if pending > 10 else "ok"
        return StatusBadge("Task Queue", status, detail)
    except Exception:  # noqa: BLE001
        return StatusBadge("Task Queue", "missing", "navig tasks list")


def resolve_blackbox() -> StatusBadge:
    """Check recent blackbox operation timeline."""
    try:
        from datetime import datetime, timezone

        from navig.blackbox.recorder import get_recorder

        ops = get_recorder().read_events(limit=100)  # newest first

        if not ops:
            return StatusBadge("Blackbox", "ok", "no ops recorded")

        last_op = ops[0]  # read_events returns newest first
        name = last_op.event_type.value
        age = int((datetime.now(timezone.utc) - last_op.timestamp).total_seconds())
        plural = "s" if len(ops) != 1 else ""
        return StatusBadge(
            "Blackbox", "ok", f"{len(ops)} recent op{plural} — last: {name} {age}s ago"
        )
    except Exception:  # noqa: BLE001
        return StatusBadge("Blackbox", "missing", "no timeline")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_recent_errors(category: str, window_seconds: int = 3600) -> int:
    """Count recent blackbox ERROR/CRASH events within the window.

    When *category* is given it further restricts to events tagged with it (or
    carrying it as ``payload['component']``); if nothing is tagged that way the
    count is simply 0 — a best-effort badge signal, never a hard dependency.
    """
    try:
        from datetime import datetime, timedelta, timezone

        from navig.blackbox.recorder import get_recorder
        from navig.blackbox.types import EventType

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        recent = get_recorder().read_events(limit=200)
        return sum(
            1
            for ev in recent
            if ev.event_type in (EventType.ERROR, EventType.CRASH)
            and ev.timestamp >= cutoff
            and (not category or category in ev.tags or ev.payload.get("component") == category)
        )
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Full dashboard section list (ordered: errors first after sort)
# ---------------------------------------------------------------------------

SECTIONS = [
    ("Agent", resolve_agent),
    ("AI Provider", resolve_provider),
    ("Gateway", resolve_gateway),
    ("Mesh", resolve_mesh),
    ("Scheduler", resolve_scheduler),
    ("Task Queue", resolve_task_queue),
    ("Blackbox", resolve_blackbox),
    ("SSH Keys", resolve_ssh),
    ("Daemon", resolve_daemon),
    ("Vault", resolve_vault),
    ("Telegram", resolve_telegram),
]

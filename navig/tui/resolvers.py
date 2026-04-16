"""
navig.tui.resolvers — Synchronous status resolvers for the NAVIG dashboard.

Each resolver reads local state files / config only.  No async, no network
calls, no subprocess.  All must complete in <100 ms on a cold local disk.

StatusBadge.deep_link is the /settings/* route that repairs this section.
An empty string means no settings panel is available (read-only info).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from navig.core.yaml_io import safe_load_yaml
from navig.platform.paths import config_dir

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
        from navig.config import get as _cfg

        token = _cfg("TELEGRAM_BOT_TOKEN", "") or _cfg("telegram_bot_token", "")
        if token:
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
        cfg_path = config_dir() / "config.yaml"
        if cfg_path.is_file():
            data = safe_load_yaml(cfg_path) or {}
            hosts = data.get("hosts", {})
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
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
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
            # Check soul.json as secondary signal
            soul_path = Path("store/agents/navig/soul.json")
            soul_ok = (
                soul_path.is_file() or (config_dir() / "agents/navig/soul.json").is_file()
            )
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
    """Check gateway channel health (reads channel manifest + blackbox errors)."""
    try:
        # Try to get active channels from gateway config
        # Also check navig.json channels section
        nj = _load_navig_json()
        channels = {}
        if nj:
            channels = nj.get("channels", {})

        active = [name for name, cfg in channels.items() if cfg.get("enabled")]

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
        nodes = registry.list_nodes() if hasattr(registry, "list_nodes") else []
        node_count = len(nodes) if nodes else 0

        if node_count == 0:
            return StatusBadge("Mesh", "missing", "no nodes — single-node mode")

        # Try to get elected leader
        leader_name = "—"
        try:
            from navig.mesh.election import get_current_leader  # type: ignore[import]

            leader = get_current_leader()
            leader_name = str(leader) if leader else "—"
        except Exception:  # noqa: BLE001
            pass

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

        svc = CronService(gateway=None, storage_path=config_dir())
        jobs = svc.list_jobs() if hasattr(svc, "list_jobs") else []
        count = len(jobs) if jobs else 0

        if count == 0:
            return StatusBadge(
                "Scheduler",
                "missing",
                "no jobs configured",
                deep_link="/settings/scheduler",
            )

        # Find next fire time
        next_label = ""
        try:
            upcoming = sorted(
                [j for j in jobs if getattr(j, "next_fire", None)],
                key=lambda j: j.next_fire,
            )
            if upcoming:
                job = upcoming[0]
                name = getattr(job, "name", "job")
                import time

                delta = int(job.next_fire - time.time())
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
    """Check task queue depth and last completion."""
    try:
        from navig.tasks.queue import TaskQueue  # type: ignore[import]

        q = TaskQueue()
        pending = q.pending_count() if hasattr(q, "pending_count") else 0
        last_age = None
        if hasattr(q, "last_completed_age"):
            last_age = q.last_completed_age()

        detail = f"{pending} pending"
        if last_age is not None:
            detail += f" — last completed: {int(last_age)}s ago"

        status = "warn" if pending > 10 else "ok"
        if pending == 0:
            status = "ok"

        return StatusBadge("Task Queue", status, detail)
    except Exception:  # noqa: BLE001
        return StatusBadge("Task Queue", "missing", "navig tasks list")


def resolve_blackbox() -> StatusBadge:
    """Check recent blackbox operation timeline."""
    try:
        from navig.blackbox.timeline import BlackboxTimeline  # type: ignore[import]

        tl = BlackboxTimeline()
        ops = tl.recent(n=3) if hasattr(tl, "recent") else []

        if not ops:
            return StatusBadge("Blackbox", "ok", "no ops recorded")

        total = tl.total_count() if hasattr(tl, "total_count") else len(ops)
        last_op = ops[-1] if ops else None
        last_label = ""
        if last_op:
            import time

            name = getattr(last_op, "name", getattr(last_op, "action", "op"))
            ts = getattr(last_op, "timestamp", None)
            if ts:
                age = int(time.time() - ts)
                last_label = f" — last: {name} {age}s ago"
            else:
                last_label = f" — last: {name}"

        return StatusBadge("Blackbox", "ok", f"{total} ops{last_label}")
    except Exception:  # noqa: BLE001
        return StatusBadge("Blackbox", "missing", "no timeline")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_recent_errors(category: str, window_seconds: int = 3600) -> int:
    """Count recent error entries from blackbox that match a category."""
    try:
        import time

        from navig.blackbox.timeline import BlackboxTimeline  # type: ignore[import]

        tl = BlackboxTimeline()
        cutoff = time.time() - window_seconds
        recent = tl.recent(n=100) if hasattr(tl, "recent") else []
        return sum(
            1
            for op in recent
            if getattr(op, "status", "") in ("error", "failed")
            and getattr(op, "category", "") == category
            and getattr(op, "timestamp", 0) >= cutoff
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

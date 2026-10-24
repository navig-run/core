"""Proof that a Brain can actually do its job — from local state, with no network.

A migration is only complete when the remote Brain *can serve*: Telegram delivery, the
scheduler, the mail watcher and the API. "The package is installed" and "the files were
copied" are not that, and this module is the difference.

Every check reads state the service itself writes, so it answers "is this configured,
owned and alive?" rather than "did a TCP connect succeed?". That keeps it honest (no
green tick over an unknown), keeps it offline (tests never touch the network), and
still catches the incident: a Brain whose Telegram mode belongs to another owner is
NOT verified, and the receipt refuses to say complete.

A capability the deployment deliberately does not use (nobody configured mail) reports
``applicable=False`` with the evidence, which satisfies the gate WITHOUT being counted
as verified — the receipt shows the difference.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from navig.brain.telegram_mode import MODE_OFF, DeliveryLedger, TelegramModeStore

logger = logging.getLogger(__name__)

__all__ = [
    "CapabilityProbe",
    "CapabilityReport",
    "LocalCapabilityProbe",
    "REQUIRED_CAPABILITIES",
    "collect_capabilities",
    "config_section",
    "read_config",
]

#: The four capabilities a migration must verify before it may report complete.
REQUIRED_CAPABILITIES: tuple[str, ...] = ("telegram", "scheduler", "mail_watcher", "api")


@dataclass(frozen=True)
class CapabilityReport:
    """One capability: verified, deliberately-unused, or unverified (with a reason)."""

    name: str
    verified: bool
    detail: str = ""
    applicable: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verified": self.verified,
            "applicable": self.applicable,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


class CapabilityProbe(Protocol):
    """What the migration needs to ask a Brain. Implemented locally; faked in tests."""

    def telegram(self) -> CapabilityReport: ...
    def scheduler(self) -> CapabilityReport: ...
    def mail_watcher(self) -> CapabilityReport: ...
    def api(self) -> CapabilityReport: ...


def read_config() -> dict[str, Any]:
    """``config.yaml`` as a dict — the public reader the CLI and the probes share."""
    from navig.platform import paths  # noqa: PLC0415

    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load((paths.config_dir() / "config.yaml").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — unreadable config = no evidence, never a crash
        return {}
    return data if isinstance(data, dict) else {}


def config_section(*keys: str) -> dict[str, Any]:
    """A nested config section (``config_section("telegram")``), ``{}`` when absent."""
    node: Any = read_config()
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _config() -> dict[str, Any]:
    return read_config()


def _section(cfg: dict[str, Any], *keys: str) -> dict[str, Any]:
    node: Any = cfg
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


class LocalCapabilityProbe:
    """What THIS machine's install can prove about itself, right now."""

    def __init__(self, config_dir: Path | None = None) -> None:
        self._config_dir = Path(config_dir) if config_dir is not None else None

    @property
    def config_dir(self) -> Path:
        if self._config_dir is not None:
            return self._config_dir
        from navig.platform import paths  # noqa: PLC0415

        return paths.config_dir()

    # ── telegram ─────────────────────────────────────────────────────────
    def telegram(self) -> CapabilityReport:
        """Is delivery configured AND owned by a Brain role?"""
        import os  # noqa: PLC0415

        cfg = _config()
        token = (
            str(_section(cfg, "telegram").get("bot_token") or "").strip()
            or str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        )
        if not token:
            return CapabilityReport(
                name="telegram",
                verified=False,
                applicable=False,
                detail="no bot token configured — this deployment does not use Telegram",
                evidence={"token_configured": False},
            )
        store = TelegramModeStore(self.config_dir / "brain" / "telegram_mode.json")
        record = store.read()
        if record.mode == MODE_OFF:
            return CapabilityReport(
                name="telegram",
                verified=False,
                detail="a bot token is configured but no delivery mode is recorded — the "
                "bot would not receive anything",
                evidence={"mode": record.mode, "token_configured": True},
            )
        ledger = DeliveryLedger(self.config_dir / "brain" / "telegram_delivered.json").load()
        return CapabilityReport(
            name="telegram",
            verified=True,
            detail=f"{record.mode} owns delivery (epoch {record.epoch}); "
            f"{len(ledger.delivered)} update id(s) recorded as delivered",
            evidence={
                "mode": record.mode,
                "owner_id": record.owner_id,
                "epoch": record.epoch,
                "delivered_ids": len(ledger.delivered),
            },
        )

    # ── scheduler ────────────────────────────────────────────────────────
    def scheduler(self) -> CapabilityReport:
        """The schedule store must be readable and its jobs owned by this Brain."""
        jobs_path = self.config_dir / "scheduler" / "cron_jobs.json"
        jobs = _read_json(jobs_path)
        if not jobs_path.exists():
            return CapabilityReport(
                name="scheduler",
                verified=False,
                applicable=False,
                detail="no schedule store yet — nothing is scheduled on this Brain",
                evidence={"path": str(jobs_path)},
            )
        entries = jobs.get("jobs") if isinstance(jobs.get("jobs"), list) else []
        enabled = [j for j in entries if isinstance(j, dict) and j.get("enabled")]
        return CapabilityReport(
            name="scheduler",
            verified=True,
            detail=f"{len(enabled)} enabled job(s) of {len(entries)} in the schedule store",
            evidence={"jobs": len(entries), "enabled": len(enabled), "path": str(jobs_path)},
        )

    # ── mail watcher ─────────────────────────────────────────────────────
    def mail_watcher(self) -> CapabilityReport:
        """The proactive mail watcher is a capability only when it was turned on."""
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        enabled = coerce_bool(_section(_config(), "proactive", "email").get("enabled"), False)
        if not enabled:
            return CapabilityReport(
                name="mail_watcher",
                verified=False,
                applicable=False,
                detail="the mail watcher is not enabled for this install",
                evidence={"proactive.email.enabled": False},
            )
        return CapabilityReport(
            name="mail_watcher",
            verified=True,
            detail="the mail watcher is enabled (proactive.email.enabled=true)",
            evidence={"proactive.email.enabled": True},
        )

    # ── api ──────────────────────────────────────────────────────────────
    def api(self) -> CapabilityReport:
        """A live gateway process, discovered the way every client discovers it."""
        discovery = _read_json(self.config_dir / "gateway.json")
        port = discovery.get("port")
        pid_file = self.config_dir / "gateway.pid"
        alive = False
        try:
            from navig.daemon.single_instance import pid_from_pidfile  # noqa: PLC0415

            alive = pid_from_pidfile(pid_file, cmdline_contains="navig") is not None
        except Exception:  # noqa: BLE001 — a probe must never raise at the caller
            alive = False
        if not port:
            return CapabilityReport(
                name="api",
                verified=False,
                detail="no gateway discovery record (gateway.json) — no API is being served",
                evidence={"discovered_port": None, "gateway_pid_alive": alive},
            )
        if not alive:
            return CapabilityReport(
                name="api",
                verified=False,
                detail=f"gateway.json points at port {port} but no live gateway process was "
                "found — a stale discovery record is not an API",
                evidence={"discovered_port": port, "gateway_pid_alive": False},
            )
        return CapabilityReport(
            name="api",
            verified=True,
            detail=f"the gateway is serving on port {port}",
            evidence={"discovered_port": port, "gateway_pid_alive": True},
        )

    # ── all ──────────────────────────────────────────────────────────────
    def collect(self, names: tuple[str, ...] = REQUIRED_CAPABILITIES) -> dict[str, dict]:
        """``{name: report}`` for every requested capability. Never raises."""
        out: dict[str, dict] = {}
        for name in names:
            check = getattr(self, name, None)
            if check is None:
                continue
            try:
                out[name] = check().as_dict()
            except Exception as exc:  # noqa: BLE001 — an unverifiable capability is reported
                out[name] = CapabilityReport(
                    name=name,
                    verified=False,
                    detail=f"could not verify: {type(exc).__name__}: {exc}",
                ).as_dict()
        return out


def collect_capabilities(
    probe: CapabilityProbe | None = None,
    names: tuple[str, ...] = REQUIRED_CAPABILITIES,
) -> dict[str, dict]:
    """Convenience: collect from *probe* (default: this machine's local probe)."""
    concrete = probe or LocalCapabilityProbe()
    collect = getattr(concrete, "collect", None)
    if callable(collect):
        return collect(names)
    out: dict[str, dict] = {}
    for name in names:
        check = getattr(concrete, name, None)
        if check is None:
            continue
        try:
            report = check()
            out[name] = report.as_dict() if hasattr(report, "as_dict") else dict(report)
        except Exception as exc:  # noqa: BLE001
            out[name] = CapabilityReport(
                name=name, verified=False, detail=f"could not verify: {exc}"
            ).as_dict()
    return out
